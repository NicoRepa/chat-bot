"""
Servicio orquestador de mensajes.
Coordina el flujo: webhook → menú → IA → clasificación → respuesta.
"""
import logging
from django.db.models import Count, Q
from django.utils import timezone
from apps.core.models import Business, UserProfile
from apps.conversations.models import Contact, Conversation, Message
from apps.menu.services import MenuService
from apps.menu.models import MenuCategory, MenuSubcategory
from apps.ai_engine.services import ai_service
from apps.webhooks.whatsapp_service import WhatsAppService
from apps.core.schedule_utils import is_within_business_hours

logger = logging.getLogger(__name__)


class ChatOrchestrator:
    """
    Orquesta todo el flujo de un mensaje entrante:
    1. Identifica/crea contacto y conversación
    2. Gestiona el menú interactivo
    3. Genera respuesta IA si corresponde
    4. Clasifica y resume
    """

    @staticmethod
    def process_incoming_message(business, platform, external_id, sender_name, message_text, metadata=None):
        """
        Procesa un mensaje entrante y devuelve la respuesta.
        Este es el punto de entrada principal llamado por el webhook.
        """
        # 1. Obtener o crear contacto
        contact, _ = Contact.objects.get_or_create(
            business=business,
            external_id=external_id,
            platform=platform,
            defaults={'name': sender_name or ''}
        )
        # Actualizar nombre si viene y no lo tenía
        if sender_name and not contact.name:
            contact.name = sender_name
            contact.save(update_fields=['name'])

        # 2. Obtener conversación más reciente o crear nueva
        conversation = Conversation.objects.filter(
            contact=contact,
            business=business,
        ).order_by('-updated_at').first()

        if not conversation:
            # No existe ninguna conversación → crear nueva
            conversation = Conversation.objects.create(
                contact=contact,
                business=business,
                status='activa',
                menu_state='initial'
            )
        elif conversation.status != 'activa':
            # Guardar estado original antes de reactivar
            previous_status = conversation.status
            conversation.status = 'activa'
            conversation.is_ai_active = True
            conversation.human_needed_at = None

            # Mostrar saludo + menú si estaba finalizada o es primer interacción del día
            if previous_status == 'finalizada':
                conversation.menu_state = 'initial'
            else:
                # Para otros estados, verificar si es la primer interacción del día
                last_msg = conversation.messages.order_by('-created_at').first()
                if last_msg:
                    now = timezone.localtime(timezone.now())
                    last_date = timezone.localtime(last_msg.created_at).date()
                    conversation.menu_state = 'initial' if last_date < now.date() else 'ai_chat'
                else:
                    conversation.menu_state = 'initial'
        else:
            # Conversación ya activa: verificar si es la primer interacción del día
            last_msg = conversation.messages.order_by('-created_at').first()
            if last_msg:
                now = timezone.localtime(timezone.now())
                last_date = timezone.localtime(last_msg.created_at).date()
                if last_date < now.date():
                    conversation.menu_state = 'initial'

        # 2.5 Detección de Audio y Transcripción (PRO)
        audio_url = (metadata or {}).get('media_url') if metadata else None
        is_audio = (metadata or {}).get('media_type') == 'audio' if metadata else False
        transcription_usage = {'tokens': 0, 'cost': 0.0}

        if is_audio and audio_url:
            logger.info(f"Procesando nota de voz de {contact.external_id}...")
            transcribed_text, transcription_usage = ai_service.transcribe_audio(audio_url)
            if transcribed_text:
                message_text = f"🎤 [Nota de voz]: {transcribed_text}"
                logger.info(f"Transcripción exitosa: {transcribed_text}")
            else:
                message_text = "[Nota de voz sin transcripción]"

        # 2.6 Detección de Opt-out (Anti-Ban)
        opt_out_keywords = ['STOP', 'QUIT', 'CANCELAR', 'BAJA', 'UNSUBSCRIBE', 'DEJAR DE RECIBIR', 'REMOVER']
        clean_text = message_text.upper().strip()
        if any(kw in clean_text for kw in opt_out_keywords) and len(clean_text) < 20:
            conversation.status = 'finalizada'
            conversation.is_ai_active = False
            conversation.save(update_fields=['status', 'is_ai_active', 'updated_at'])
            logger.info(f"Usuario {contact.external_id} solicitó baja. IA desactivada.")
            return {
                'response': "Entendido. No volveremos a enviarte mensajes automáticos. Si necesitas algo, un agente humano revisará esta charla.",
                'conversation_id': str(conversation.id),
                'status': 'opted_out'
            }

        # 3. Guardar mensaje del usuario
        Message.objects.create(
            conversation=conversation,
            role='user',
            content=message_text,
            tokens_used=transcription_usage.get('tokens', 0),
            ai_cost=transcription_usage.get('cost', 0.0),
            metadata=metadata or {}
        )

        # Incrementar contador de no leídos para el panel
        conversation.panel_unread_count = (conversation.panel_unread_count or 0) + 1

        # Enviar notificación Web Push asíncrona del nuevo mensaje del usuario
        import threading
        from apps.core.push_utils import send_push_to_users
        
        def _send_push_async():
            title = f"Nuevo mensaje: {contact.get_full_name()}" if hasattr(contact, 'get_full_name') else f"Nuevo mensaje: {contact.name or contact.phone or contact.external_id}"
            body = message_text[:100] + ('...' if len(message_text) > 100 else '')
            url = f"/panel/conversaciones/{conversation.id}/"
            target_users = None
            if conversation.assigned_to:
                target_users = [conversation.assigned_to.id]
            send_push_to_users(target_users, title, body, url)
            
        threading.Thread(target=_send_push_async, daemon=True).start()

        # 4. Determinar modo de IA efectivo
        ai_mode = getattr(business.config, 'ai_mode', 'full')
        # Backwards compat: ai_globally_disabled legacy
        if getattr(business.config, 'ai_globally_disabled', False) and ai_mode == 'full':
            ai_mode = 'menu_only'

        # Si un agente humano tomó control Y el modo es IA completa → no interferir
        if not conversation.is_ai_active and ai_mode == 'full':
            conversation.save(update_fields=['panel_unread_count', 'updated_at'])
            return {
                'response': None,
                'conversation_id': str(conversation.id),
                'status': 'waiting_for_agent',
                'message': 'Agente humano activo, esperando respuesta del recepcionista.'
            }

        # 5. Procesar según el estado del menú
        usage_acc = {'tokens': 0, 'cost': 0.0}
        response_data = ChatOrchestrator._process_by_state(
            conversation, message_text, business, usage_acc, ai_mode=ai_mode
        )

        # _process_by_state puede devolver:
        #   - un string (texto plano, respuesta IA o mensaje simple)
        #   - un dict {'text': str, 'interactive_list': dict} (menú interactivo)
        #   - None (no responder)
        interactive_list = None
        if isinstance(response_data, dict):
            response_text = response_data.get('text', '')
            interactive_list = response_data.get('interactive_list')
        else:
            response_text = response_data

        # 6. Guardar respuesta del bot
        if response_text:
            Message.objects.create(
                conversation=conversation,
                role='assistant',
                content=response_text,
                tokens_used=usage_acc.get('tokens', 0),
                ai_cost=usage_acc.get('cost', 0.0)
            )
            # Incrementar contador de mensajes IA en sesión si hubo tokens usados
            if usage_acc.get('tokens', 0) > 0:
                conversation.ai_messages_in_session = (conversation.ai_messages_in_session or 0) + 1

        # 6.5 Límite de mensajes IA: derivar a humano si se supera
        config = business.config
        if config.ai_max_messages > 0 and conversation.is_ai_active:
            if (conversation.ai_messages_in_session or 0) >= config.ai_max_messages:
                conversation.is_ai_active = False
                conversation.menu_state = 'human_chat'
                conversation.human_needed_at = timezone.now()
                # Mensaje de derivación (configurable)
                escalation_msg = config.escalation_message or (
                    '👤 Te voy a comunicar con un agente para ayudarte mejor. '
                    'En breve te atiende una persona. ¡Gracias por tu paciencia!'
                )
                Message.objects.create(
                    conversation=conversation,
                    role='system',
                    content=escalation_msg
                )
                response_text = escalation_msg
                interactive_list = None  # No enviar lista interactiva para escalación
                # Auto-asignar agente si está habilitado
                ChatOrchestrator._auto_assign_agent(conversation, business)

        # 7. Clasificar y resumir según intervalo configurable (0 = desactivado)
        user_msg_count = conversation.messages.filter(role='user').count()
        summary_interval = getattr(business.config, 'ai_auto_summary_interval', 0)
        if summary_interval > 0 and user_msg_count >= summary_interval and user_msg_count % summary_interval == 0:
            ChatOrchestrator._classify_and_summarize(conversation)
        elif summary_interval == 0 and user_msg_count >= 2 and user_msg_count % 3 == 0:
            # fallback: cada 3 mensajes si no está configurado
            ChatOrchestrator._classify_and_summarize(conversation)

        conversation.save(update_fields=[
            'status', 'menu_state', 'menu_selections',
            'current_menu_category_id', 'current_menu_subcategory_id',
            'is_ai_active', 'ai_messages_in_session',
            'classification', 'classification_confidence', 'summary',
            'human_needed_at', 'assigned_to', 'panel_unread_count',
            'updated_at',
        ])

        # Web Push movido arriba para que se envíe incluso si la IA está inactiva.

        result = {
            'response': response_text,
            'conversation_id': str(conversation.id),
            'classification': conversation.classification,
            'menu_state': conversation.menu_state,
        }
        if interactive_list:
            result['interactive_list'] = interactive_list
        return result


    @staticmethod
    def _make_menu_response(text, interactive_list=None):
        """Helper: crea un dict con texto y (opcionalmente) datos de lista interactiva."""
        if interactive_list:
            return {'text': text, 'interactive_list': interactive_list}
        return text

    @staticmethod
    def _parse_selection(message_text, prefix):
        """
        Extrae el número de selección desde texto plano o un ID de interactive list.
        Soporta:
          - IDs como 'main_1', 'sub_2', 'subsub_3'
          - Números como '1', '2', '0'
          - IDs especiales como 'back_main', 'back_submenu', 'back_nav', 'back_main_nav'
        Retorna (selection_number, is_special_action, action_name).
        """
        stripped = message_text.strip()

        # Detectar IDs especiales de navegación
        if stripped in ('back_main', 'back_main_nav'):
            return 0, True, 'back_to_main'
        if stripped in ('back_submenu', 'back_nav'):
            return 0, True, 'back'

        # Detectar IDs con prefijo (e.g., main_1, sub_2)
        if stripped.startswith(f'{prefix}_'):
            try:
                return int(stripped.split('_', 1)[1]), False, None
            except (ValueError, IndexError):
                pass

        # Fallback: intentar parsear como número
        try:
            return int(stripped), False, None
        except (ValueError, TypeError):
            return None, False, None

    @staticmethod
    def _ai_generate(conversation, message_text, usage_acc):
        """
        Helper que llama a ai_service.generate_response y acumula usage.
        Retorna solo el texto de la respuesta.
        """
        text, usage = ai_service.generate_response(conversation, message_text)
        usage_acc['tokens'] = usage_acc.get('tokens', 0) + usage.get('tokens', 0)
        usage_acc['cost'] = usage_acc.get('cost', 0.0) + usage.get('cost', 0.0)
        return text

    @staticmethod
    def _process_by_state(conversation, message_text, business, usage_acc=None, ai_mode='full'):
        """
        Procesa el mensaje según el estado actual del menú.
        ai_mode: 'full' | 'menu_handoff' | 'menu_only'
        """
        if usage_acc is None:
            usage_acc = {'tokens': 0, 'cost': 0.0}
        config = business.config
        state = conversation.menu_state

        # En modos no-full, si la conversación quedó en human_chat, reiniciar al menú
        if ai_mode != 'full' and state == 'human_chat':
            conversation.menu_state = 'initial'
            state = 'initial'

        # Estado inicial → enviar saludo + menú
        if state == 'initial':
            if config.menu_enabled:
                greeting, has_menu = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu' if has_menu else 'ai_chat'
                if has_menu:
                    interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                    return ChatOrchestrator._make_menu_response(greeting, interactive_data)
                return greeting
            else:
                conversation.menu_state = 'ai_chat'
                greeting = config.greeting_message or f'¡Hola! 👋 Bienvenido/a a {business.name}. ¿En qué te puedo ayudar?'
                return greeting

        # Menú principal → procesar selección
        elif state == 'main_menu':
            selection, is_special, action = ChatOrchestrator._parse_selection(message_text, 'main')

            if selection is None:
                # No es un número ni un ID válido
                if not config.menu_force_selection:
                    conversation.menu_state = 'ai_chat'
                    response = ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)
                    return response
                # Re-mostrar el menú con aviso
                greeting, has_menu = MenuService.get_greeting_with_menu(business)
                hint = '⚠️ Por favor elegí una opción del menú.\n\n'
                if has_menu:
                    interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                    return ChatOrchestrator._make_menu_response(hint + greeting, interactive_data)
                return hint + greeting

            category, response, next_state = MenuService.process_main_menu_selection(business, selection)

            if category:
                conversation.current_menu_category_id = str(category.pk)
                if not conversation.menu_selections:
                    conversation.menu_selections = []
                conversation.menu_selections.append({
                    'category': category.name,
                    'category_id': str(category.pk),
                })

            conversation.menu_state = next_state

            if next_state == 'ai_chat' and not response:
                context_msg = f'El usuario seleccionó la categoría: {category.name}'
                return ChatOrchestrator._ai_generate(conversation, context_msg, usage_acc)

            # Si el next_state es submenu, agregar interactive list data
            if next_state == 'submenu' and category:
                interactive_data = MenuService.get_submenu_interactive_list(category)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            # Si es opción inválida y vuelve a main_menu
            if next_state == 'main_menu':
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            return response

        # Sub-menú → procesar selección de subcategoría
        elif state == 'submenu':
            selection, is_special, action = ChatOrchestrator._parse_selection(message_text, 'sub')

            # Navegación especial: volver al menú principal
            if is_special and action == 'back_to_main':
                selection = 0  # El process_submenu_selection trata 0 como back_to_main

            if selection is None:
                # No es un número ni un ID válido
                if not config.menu_force_selection:
                    conversation.menu_state = 'ai_chat'
                    response = ai_service.generate_response(conversation, message_text)
                    return response
                # Re-mostrar el sub-menú con aviso
                try:
                    category = MenuCategory.objects.get(pk=conversation.current_menu_category_id)
                    submenu_text = MenuService.get_submenu_text(category)
                    if submenu_text:
                        hint = '⚠️ Por favor elegí una opción del menú.\n\n'
                        interactive_data = MenuService.get_submenu_interactive_list(category)
                        return ChatOrchestrator._make_menu_response(hint + submenu_text, interactive_data)
                except MenuCategory.DoesNotExist:
                    pass
                # Fallback: re-mostrar menú principal
                greeting, _ = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu'
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(
                    '⚠️ Por favor elegí una opción.\n\n' + greeting, interactive_data
                )

            try:
                category = MenuCategory.objects.get(pk=conversation.current_menu_category_id)
            except MenuCategory.DoesNotExist:
                conversation.menu_state = 'ai_chat'
                return ai_service.generate_response(conversation, message_text)

            subcategory, response, next_state = MenuService.process_submenu_selection(category, selection)

            if next_state == 'back_to_main':
                greeting, _ = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu'
                conversation.current_menu_category_id = None
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(greeting, interactive_data)

            if subcategory:
                if conversation.menu_selections:
                    conversation.menu_selections[-1]['subcategory'] = subcategory.name
                    conversation.menu_selections[-1]['subcategory_id'] = str(subcategory.pk)

            conversation.menu_state = next_state

            if next_state == 'sub_submenu' and subcategory:
                conversation.current_menu_subcategory_id = str(subcategory.pk)
                interactive_data = MenuService.get_sub_submenu_interactive_list(subcategory)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            if next_state == 'ai_chat' and not response:
                context_msg = f'El usuario seleccionó: {category.name} > {subcategory.name}'
                return ChatOrchestrator._ai_generate(conversation, context_msg, usage_acc)

            if response and next_state == 'ai_chat':
                return response

            # Si es menu_response con auto_response, enviar con navegación interactiva
            if next_state == 'menu_response' and response:
                interactive_data = MenuService.get_menu_response_nav_interactive_list(response)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            # Si es submenu (opción inválida), agregar interactive list
            if next_state == 'submenu':
                interactive_data = MenuService.get_submenu_interactive_list(category)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            return response

        # Sub-sub-menú → procesar selección de sub-subcategoría (3er nivel)
        elif state == 'sub_submenu':
            selection, is_special, action = ChatOrchestrator._parse_selection(message_text, 'subsub')

            # Navegación especial: volver al submenu
            if is_special and action == 'back':
                selection = 0
            elif is_special and action == 'back_to_main':
                # Volver directo al menú principal
                greeting, has_menu = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu' if has_menu else 'ai_chat'
                conversation.current_menu_category_id = None
                conversation.current_menu_subcategory_id = None
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(greeting, interactive_data)

            if selection is None:
                if not config.menu_force_selection:
                    conversation.menu_state = 'ai_chat'
                    response = ai_service.generate_response(conversation, message_text)
                    return response
                try:
                    subcategory = MenuSubcategory.objects.get(pk=conversation.current_menu_subcategory_id)
                    sub_submenu_text = MenuService.get_sub_submenu_text(subcategory)
                    if sub_submenu_text:
                        hint = '⚠️ Por favor elegí una opción del menú.\n\n'
                        interactive_data = MenuService.get_sub_submenu_interactive_list(subcategory)
                        return ChatOrchestrator._make_menu_response(hint + sub_submenu_text, interactive_data)
                except MenuSubcategory.DoesNotExist:
                    pass
                conversation.menu_state = 'ai_chat'
                return ai_service.generate_response(conversation, message_text)

            try:
                subcategory = MenuSubcategory.objects.get(pk=conversation.current_menu_subcategory_id)
            except MenuSubcategory.DoesNotExist:
                conversation.menu_state = 'ai_chat'
                return ai_service.generate_response(conversation, message_text)

            child, response, next_state = MenuService.process_sub_submenu_selection(subcategory, selection)

            if next_state == 'back_to_submenu':
                try:
                    category = MenuCategory.objects.get(pk=conversation.current_menu_category_id)
                    submenu_text = MenuService.get_submenu_text(category)
                    conversation.menu_state = 'submenu'
                    conversation.current_menu_subcategory_id = None
                    interactive_data = MenuService.get_submenu_interactive_list(category)
                    return ChatOrchestrator._make_menu_response(submenu_text, interactive_data)
                except MenuCategory.DoesNotExist:
                    conversation.menu_state = 'ai_chat'
                    return ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)

            if child:
                if conversation.menu_selections:
                    conversation.menu_selections[-1]['sub_subcategory'] = child.name
                    conversation.menu_selections[-1]['sub_subcategory_id'] = str(child.pk)

            conversation.menu_state = next_state

            if next_state == 'ai_chat' and not response:
                category_name = subcategory.category.name
                context_msg = f'El usuario seleccionó: {category_name} > {subcategory.name} > {child.name}'
                return ChatOrchestrator._ai_generate(conversation, context_msg, usage_acc)

            if response and next_state == 'ai_chat':
                return response

            # Si es menu_response con auto_response, enviar con navegación interactiva
            if next_state == 'menu_response' and response:
                interactive_data = MenuService.get_menu_response_nav_interactive_list(response)
                return ChatOrchestrator._make_menu_response(response, interactive_data)

            return response

        # Respuesta de menú (el usuario recibió auto_response, puede navegar)
        elif state == 'menu_response':
            msg_stripped = message_text.strip()

            # Detectar IDs de interactive list para navegación
            if msg_stripped in ('back_main_nav', '00'):
                greeting, has_menu = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu' if has_menu else 'ai_chat'
                conversation.current_menu_category_id = None
                conversation.current_menu_subcategory_id = None
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(greeting, interactive_data)

            if msg_stripped in ('back_nav', '0'):
                # Si tenía subcategoría, volver al sub-menú de esa categoría
                if conversation.current_menu_subcategory_id:
                    try:
                        subcategory = MenuSubcategory.objects.get(pk=conversation.current_menu_subcategory_id)
                        category = subcategory.category
                        submenu_text = MenuService.get_submenu_text(category)
                        conversation.menu_state = 'submenu'
                        conversation.current_menu_subcategory_id = None
                        interactive_data = MenuService.get_submenu_interactive_list(category)
                        return ChatOrchestrator._make_menu_response(submenu_text, interactive_data)
                    except MenuSubcategory.DoesNotExist:
                        pass
                # Si tenía categoría, volver al submenú de esa categoría
                if conversation.current_menu_category_id:
                    try:
                        category = MenuCategory.objects.get(pk=conversation.current_menu_category_id)
                        submenu_text = MenuService.get_submenu_text(category)
                        if submenu_text:
                            conversation.menu_state = 'submenu'
                            interactive_data = MenuService.get_submenu_interactive_list(category)
                            return ChatOrchestrator._make_menu_response(submenu_text, interactive_data)
                    except MenuCategory.DoesNotExist:
                        pass
                # Fallback: menú principal
                greeting, has_menu = MenuService.get_greeting_with_menu(business)
                conversation.menu_state = 'main_menu' if has_menu else 'ai_chat'
                interactive_data, _ = MenuService.get_greeting_interactive_list(business)
                return ChatOrchestrator._make_menu_response(greeting, interactive_data)

            # Cualquier otro texto → pasar a IA
            conversation.menu_state = 'ai_chat'
            return ai_service.generate_response(conversation, message_text)

        # Selección de turno (estado intermedio de reserva de cita)
        elif state == 'appointment_slot_selection':
            return ChatOrchestrator._handle_appointment_selection(
                conversation, message_text, business, usage_acc
            )

        # Cancelación de turno (selección de cuál cancelar si hay varios)
        elif state == 'appointment_cancel_selection':
            return ChatOrchestrator._handle_cancellation_selection(
                conversation, message_text, business, usage_acc
            )

        # Chat con IA
        elif state == 'ai_chat':
            if ai_mode == 'menu_only':
                return None

            msg_stripped = message_text.strip()
            msg_lower = msg_stripped.lower()

            # Detectar intención de reservar cita (si el módulo está habilitado y activo)
            try:
                appt_config = business.appointment_config
                if business.feature_appointments and appt_config.is_enabled:
                    appointment_keywords = [
                        'turno', 'cita', 'reservar', 'agendar', 'agenda', 'quiero un turno',
                        'quiero una cita', 'quiero reservar', 'pedir turno', 'pedir cita',
                        'sacar turno', 'sacar cita', 'necesito un turno', 'necesito una cita',
                        'disponibilidad', 'horarios disponibles', 'cuando tienen lugar',
                    ]
                    if any(kw in msg_lower for kw in appointment_keywords):
                        result = ChatOrchestrator._handle_appointment_intent(
                            conversation, appt_config, message_text
                        )
                        if result is not None:
                            return result

                    cancel_keywords = [
                        'cancelar turno', 'cancelar cita', 'quiero cancelar', 'necesito cancelar',
                        'borrar turno', 'borrar cita', 'anular turno', 'anular cita',
                        'cancela mi turno', 'cancela mi cita', 'no puedo ir', 'no voy a poder',
                    ]
                    if any(kw in msg_lower for kw in cancel_keywords):
                        result = ChatOrchestrator._handle_cancellation_intent(
                            conversation, appt_config
                        )
                        if result is not None:
                            return result
            except Exception:
                pass

            # Modo menú+derivación: para chat libre siempre deriva a agente (sin IA)
            if ai_mode == 'menu_handoff':
                within_hours, _ = is_within_business_hours(config)
                if not within_hours:
                    from apps.core.schedule_utils import get_schedule_display
                    schedule_text = get_schedule_display(config)
                    out_msg = config.out_of_hours_message or (
                        '⏰ En este momento estamos fuera del horario de atención.'
                    )
                    return f'{out_msg}\n\n{schedule_text}'
                conversation.is_ai_active = False
                conversation.menu_state = 'human_chat'
                conversation.human_needed_at = timezone.now()
                ChatOrchestrator._classify_and_summarize(conversation)
                escalation_msg = config.escalation_message or (
                    '👤 Te voy a comunicar con un agente para ayudarte mejor. '
                    'En breve te atiende una persona. ¡Gracias por tu paciencia!'
                )
                return escalation_msg

            # Detectar pedido de agente humano (usando IA para entender intención)
            wants_human = ai_service.detect_human_request(message_text)

            if wants_human:
                # Verificar si estamos en horario de atención antes de derivar
                within_hours, _ = is_within_business_hours(config)
                if not within_hours:
                    from apps.core.schedule_utils import get_schedule_display
                    schedule_text = get_schedule_display(config)
                    out_msg = config.out_of_hours_message or (
                        '⏰ En este momento estamos fuera del horario de atención, '
                        'por lo que no hay agentes disponibles.'
                    )
                    return f'{out_msg}\n\n{schedule_text}'

                conversation.is_ai_active = False
                conversation.menu_state = 'human_chat'
                conversation.human_needed_at = timezone.now()
                ChatOrchestrator._classify_and_summarize(conversation)
                ChatOrchestrator._auto_assign_agent(conversation, business)
                escalation_msg = config.escalation_message or (
                    '👤 Te voy a comunicar con un agente para ayudarte mejor. '
                    'En breve te atiende una persona. ¡Gracias por tu paciencia!'
                )
                return escalation_msg

            # Detectar reactivación del menú: "1" o keywords
            menu_keywords = ['menu', 'menú', 'opciones', 'ver opciones', 'ver menu',
                             'ver menú', 'volver al menu', 'volver al menú', 'mostrar menu',
                             'mostrar menú', 'quiero ver el menu', 'quiero ver el menú']
            reactivate_by_number = msg_stripped == '1'
            reactivate_by_keyword = any(kw in msg_lower for kw in menu_keywords)

            if reactivate_by_number or reactivate_by_keyword:
                if config.menu_enabled:
                    menu_text, has_menu = MenuService.get_menu_only(business)
                    if has_menu:
                        conversation.menu_state = 'main_menu'
                        conversation.current_menu_category_id = None
                        conversation.current_menu_subcategory_id = None
                        interactive_data, _ = MenuService.get_menu_only_interactive_list(business)
                        return ChatOrchestrator._make_menu_response(menu_text, interactive_data)

            response = ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)
            return response

        # Chat humano (no responder con IA)
        elif state == 'human_chat':
            return None

        return None


    @staticmethod
    def _handle_appointment_intent(conversation, appt_config, message_text=None):
        """
        Detecta intención de reserva de cita y muestra los próximos slots disponibles.
        Retorna el mensaje a enviar o None si no se pudo determinar la intención.
        """
        from apps.appointments.services import AppointmentService
        from datetime import date
        try:
            date_range = ai_service.extract_appointment_date(message_text) if message_text else None
            if date_range:
                start_date = date_range['start_date']
                days_ahead = (date_range['end_date'] - date_range['start_date']).days + 1
            else:
                start_date = date.today()
                days_ahead = 14
            logger.info(f'_handle_appointment_intent: message="{message_text}", date_range={date_range}, start={start_date}, days_ahead={days_ahead}')

            available_days = AppointmentService.get_available_days(appt_config, start_date, days_ahead=days_ahead)
            if not available_days:
                if date_range:
                    return f'Lo siento, no hay {appt_config.slot_name.lower()}s disponibles para esas fechas. Podés pedir otro día o semana.'
                return f'Lo siento, no hay {appt_config.slot_name.lower()}s disponibles en los próximos 14 días. Te contactaremos para coordinar.'

            # Juntar hasta 8 slots de los días disponibles dentro del rango
            all_slots = []
            for d in available_days:
                slots = AppointmentService.get_available_slots(appt_config, d)
                all_slots.extend(slots)
                if len(all_slots) >= 8:
                    break

            if not all_slots:
                return None

            # Guardar slots en la conversación y pasar al estado de selección
            conversation.menu_state = 'appointment_slot_selection'
            slot_data = [
                {'start': s.isoformat(), 'end': e.isoformat()}
                for s, e in all_slots[:8]
            ]
            conversation.menu_selections = conversation.menu_selections or []
            conversation.menu_selections.append({'pending_slots': slot_data})

            return AppointmentService.format_slots_for_ai(appt_config, all_slots[:8])
        except Exception as e:
            logger.error(f'Error mostrando slots de citas: {e}')
            return None

    @staticmethod
    def _rebuild_slots(pending_slots):
        from datetime import datetime
        result = []
        for s in (pending_slots or []):
            try:
                start = datetime.fromisoformat(s['start'])
                end = datetime.fromisoformat(s['end'])
                if not start.tzinfo:
                    start = timezone.make_aware(start)
                if not end.tzinfo:
                    end = timezone.make_aware(end)
                result.append((start, end))
            except Exception:
                pass
        return result

    @staticmethod
    def _clear_pending_slots(conversation):
        if conversation.menu_selections:
            conversation.menu_selections = [
                s for s in conversation.menu_selections
                if 'pending_slots' not in s and 'slot_offset' not in s
            ]

    @staticmethod
    def _handle_appointment_selection(conversation, message_text, business, usage_acc):
        """
        Procesa la selección de un slot numerado por el usuario.
        """
        from apps.appointments.services import AppointmentService
        from datetime import datetime, date as dt_date
        try:
            appt_config = business.appointment_config
        except Exception:
            conversation.menu_state = 'ai_chat'
            return ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)

        msg_lower = message_text.strip().lower()

        # --- Palabras clave para cancelar/salir ---
        EXIT_KEYWORDS = ['cancelar', 'salir', 'no quiero', 'no gracias', 'dejalo', 'olvida', 'olvídalo', 'listo', 'otro tema', 'nada', 'no importa']
        if any(kw in msg_lower for kw in EXIT_KEYWORDS):
            ChatOrchestrator._clear_pending_slots(conversation)
            conversation.menu_state = 'ai_chat'
            return ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)

        # Recuperar slots pendientes y offset actual
        pending_slots = []
        slot_offset = 0
        if conversation.menu_selections:
            for sel in reversed(conversation.menu_selections):
                if 'pending_slots' in sel and not pending_slots:
                    pending_slots = sel['pending_slots']
                if 'slot_offset' in sel:
                    slot_offset = sel['slot_offset']

        # Intentar parsear la selección numérica
        try:
            idx = int(message_text.strip()) - 1
        except (ValueError, TypeError):
            idx = -1

        # --- Número válido → reservar ---
        if pending_slots and 0 <= idx < len(pending_slots):
            slot = pending_slots[idx]
            try:
                start_dt = datetime.fromisoformat(slot['start'])
                if not start_dt.tzinfo:
                    start_dt = timezone.make_aware(start_dt)
            except Exception:
                conversation.menu_state = 'ai_chat'
                return 'Hubo un error procesando el horario. Por favor intentá de nuevo.'

            contact_name = ''
            if conversation.contact:
                contact_name = conversation.contact.name or conversation.contact.phone or 'Cliente'
            contact_phone = conversation.contact.phone if conversation.contact else ''

            appt, error = AppointmentService.book_appointment(
                appt_config, contact_name, contact_phone, start_dt,
                conversation=conversation, created_by_ai=True
            )

            ChatOrchestrator._clear_pending_slots(conversation)
            conversation.menu_state = 'ai_chat'

            if error:
                return f'❌ No pudimos reservar ese horario: {error}. ¿Querés elegir otro?'
            return AppointmentService.format_confirmation(appt_config, appt)

        # --- Obtener última fecha mostrada para contexto ---
        context_last_date = None
        if pending_slots:
            from datetime import datetime as _dt
            for s in pending_slots:
                try:
                    d = _dt.fromisoformat(s['start']).date()
                    if context_last_date is None or d > context_last_date:
                        context_last_date = d
                except Exception:
                    pass

        # --- Verificar si está pidiendo una fecha específica ---
        date_range = ai_service.extract_appointment_date(message_text, context_last_date=context_last_date)
        logger.info(f'_handle_appointment_selection: date_range={date_range} for: "{message_text}"')
        if date_range:
            try:
                start_date = date_range['start_date']
                end_date = date_range['end_date']
                days_in_range = (end_date - start_date).days + 1

                all_days = AppointmentService.get_available_days(appt_config, start_date, days_ahead=days_in_range)
                all_slots = []
                for d in all_days:
                    all_slots.extend(AppointmentService.get_available_slots(appt_config, d))

                next_slots = all_slots[:8]
                if not next_slots:
                    return f'Lo siento, no encontré horarios disponibles del {start_date.strftime("%d/%m")} al {end_date.strftime("%d/%m")}. Podés pedir otro día o semana.'

                ChatOrchestrator._clear_pending_slots(conversation)
                conversation.menu_selections = conversation.menu_selections or []
                conversation.menu_selections.append({'pending_slots': [
                    {'start': s.isoformat(), 'end': e.isoformat()} for s, e in next_slots
                ]})
                conversation.menu_selections.append({'slot_offset': 0})

                return (
                    AppointmentService.format_slots_for_ai(appt_config, next_slots)
                    + '\n\n_Elegí un número, escribí *más opciones* para seguir viendo, o *cancelar* para salir._'
                )
            except Exception as e:
                logger.error(f'Error buscando slots para fecha específica: {e}')

        # --- Palabras clave para ver más horarios (paginación) ---
        MORE_KEYWORDS = ['más opciones', 'mas opciones', 'más horarios', 'mas horarios',
                         'más días', 'mas dias', 'ver más', 'ver mas', 'más adelante',
                         'mas adelante', 'otros horarios', 'otros turnos',
                         'otros días', 'otros dias', 'mostrame más', 'mostrame mas']
        if any(kw in msg_lower for kw in MORE_KEYWORDS):
            new_offset = slot_offset + 8
            try:
                all_days = AppointmentService.get_available_days(appt_config, dt_date.today(), days_ahead=30)
                all_slots = []
                for d in all_days:
                    all_slots.extend(AppointmentService.get_available_slots(appt_config, d))

                next_slots = all_slots[new_offset:new_offset + 8]
                if not next_slots:
                    slots_rebuilt = ChatOrchestrator._rebuild_slots(pending_slots)
                    msg = AppointmentService.format_slots_for_ai(appt_config, slots_rebuilt) if slots_rebuilt else ''
                    return (
                        f'No hay más horarios disponibles más adelante.\n\n'
                        + (msg + '\n\n' if msg else '')
                        + '_Elegí un número, o escribí *cancelar* para salir._'
                    )

                # Actualizar pending_slots y offset
                ChatOrchestrator._clear_pending_slots(conversation)
                conversation.menu_selections = conversation.menu_selections or []
                conversation.menu_selections.append({'pending_slots': [
                    {'start': s.isoformat(), 'end': e.isoformat()} for s, e in next_slots
                ]})
                conversation.menu_selections.append({'slot_offset': new_offset})

                return (
                    AppointmentService.format_slots_for_ai(appt_config, next_slots)
                    + '\n\n_Elegí un número, escribí *más opciones* para seguir viendo, o *cancelar* para salir._'
                )
            except Exception as e:
                logger.error(f'Error mostrando más slots: {e}')

        if pending_slots:
            slots_rebuilt = ChatOrchestrator._rebuild_slots(pending_slots)
            if slots_rebuilt:
                return (
                    AppointmentService.format_slots_for_ai(appt_config, slots_rebuilt)
                    + '\n\n_Respondé con el *número* de la opción, escribí *más opciones* para ver otros horarios, o *cancelar* para salir._'
                )

        conversation.menu_state = 'ai_chat'
        return '⚠️ No se encontraron horarios guardados. Escribí "turno" para ver la disponibilidad nuevamente.'

    @staticmethod
    def _handle_cancellation_intent(conversation, appt_config):
        """
        Detecta intención de cancelar y cancela directamente o pide confirmación si hay varias.
        """
        from apps.appointments.models import Appointment
        phone = conversation.contact.phone if conversation.contact else None
        appts = list(Appointment.objects.filter(
            config=appt_config,
            status__in=['scheduled', 'confirmed'],
        ).filter(
            Q(conversation=conversation) | (Q(contact_phone=phone) if phone else Q())
        ).order_by('start_datetime'))

        if not appts:
            return 'No encontré citas activas para cancelar. Si querés hacer una nueva reserva, escribí "turno".'

        if len(appts) == 1:
            appt = appts[0]
            appt.status = 'cancelled'
            appt.save(update_fields=['status'])
            from apps.appointments.services import AppointmentService
            return AppointmentService.format_cancellation(appt_config, appt)

        # Múltiples citas → pedir que elija
        lines = ['Tenés estas citas activas. ¿Cuál querés cancelar?\n']
        _DAYS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
        for i, appt in enumerate(appts, 1):
            local = timezone.localtime(appt.start_datetime)
            day_name = _DAYS_ES[local.weekday()]
            lines.append(f'{i}. {day_name} {local.strftime("%d/%m")} a las *{local.strftime("%H:%M")}*')
        lines.append('\n_Respondé con el número de la cita a cancelar, o escribí *cancelar* para salir._')

        conversation.menu_selections = conversation.menu_selections or []
        conversation.menu_selections.append({
            'pending_cancel': [str(a.pk) for a in appts]
        })
        conversation.menu_state = 'appointment_cancel_selection'
        return '\n'.join(lines)

    @staticmethod
    def _handle_cancellation_selection(conversation, message_text, business, usage_acc):
        """
        Procesa la elección de qué cita cancelar.
        """
        from apps.appointments.models import Appointment
        from apps.appointments.services import AppointmentService
        try:
            appt_config = business.appointment_config
        except Exception:
            conversation.menu_state = 'ai_chat'
            return ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)

        msg_lower = message_text.strip().lower()

        EXIT_KEYWORDS = ['cancelar', 'salir', 'no', 'nada', 'no quiero', 'listo']
        if any(kw in msg_lower for kw in EXIT_KEYWORDS):
            if conversation.menu_selections:
                conversation.menu_selections = [
                    s for s in conversation.menu_selections if 'pending_cancel' not in s
                ]
            conversation.menu_state = 'ai_chat'
            return ChatOrchestrator._ai_generate(conversation, message_text, usage_acc)

        pending_cancel = []
        if conversation.menu_selections:
            for sel in reversed(conversation.menu_selections):
                if 'pending_cancel' in sel:
                    pending_cancel = sel['pending_cancel']
                    break

        try:
            idx = int(message_text.strip()) - 1
        except (ValueError, TypeError):
            idx = -1

        if not pending_cancel or idx < 0 or idx >= len(pending_cancel):
            return '⚠️ Por favor respondé con el *número* de la cita, o escribí *salir* para cancelar.'

        appt_id = pending_cancel[idx]
        try:
            appt = Appointment.objects.get(pk=appt_id, status__in=['scheduled', 'confirmed'])
            appt.status = 'cancelled'
            appt.save(update_fields=['status'])
        except Appointment.DoesNotExist:
            conversation.menu_state = 'ai_chat'
            if conversation.menu_selections:
                conversation.menu_selections = [
                    s for s in conversation.menu_selections if 'pending_cancel' not in s
                ]
            return '❌ No encontré esa cita o ya estaba cancelada.'

        if conversation.menu_selections:
            conversation.menu_selections = [
                s for s in conversation.menu_selections if 'pending_cancel' not in s
            ]
        conversation.menu_state = 'ai_chat'
        return AppointmentService.format_cancellation(appt_config, appt)

    @staticmethod
    def _classify_and_summarize(conversation):
        """Clasifica y resume la conversación con IA."""
        try:
            classification, confidence, summary, _usage = ai_service.classify_conversation(conversation)
            if classification:
                conversation.classification = classification
                conversation.classification_confidence = confidence
            if summary:
                conversation.summary = summary
            conversation.save(update_fields=['classification', 'classification_confidence', 'summary'])
        except Exception as e:
            logger.error(f'Error clasificando/resumiendo: {e}')

    @staticmethod
    def _auto_assign_agent(conversation, business):
        """
        Auto-asigna la conversación al agente más adecuado.
        Prioridad:
        1. Agente con la especialización correcta que tenga 0 chats activos
        2. Agente con la especialización correcta con menos chats activos
        3. Cualquier agente con 0 chats activos
        4. Cualquier agente con menos chats activos
        """
        try:
            config = business.config
            if config.supervisor_only_mode:
                return

            # Buscar todos los agentes/supervisores del negocio
            all_candidates = list(UserProfile.objects.filter(
                business=business,
                role__in=['agent', 'supervisor'],
            ).select_related('user'))

            if not all_candidates:
                return

            # Helper: contar chats activos de un agente
            def active_chat_count(profile):
                return Conversation.objects.filter(
                    assigned_to=profile.user,
                    status__in=['activa', 'en_seguimiento', 'esperando_cliente']
                ).count()

            # Filtrar por especialización si la conversación tiene clasificación
            classification = conversation.classification
            specialized = []
            if classification:
                specialized = [
                    p for p in all_candidates
                    if not p.specializations or classification in p.specializations
                ]

            # Intentar asignar en orden de prioridad
            best = None

            if specialized:
                # Prioridad 1: especializado con 0 chats
                free_specialized = [p for p in specialized if active_chat_count(p) == 0]
                if free_specialized:
                    best = free_specialized[0]
                else:
                    # Prioridad 2: especializado con menos chats
                    best = min(specialized, key=active_chat_count)
            else:
                # Prioridad 3: cualquier agente con 0 chats
                free_agents = [p for p in all_candidates if active_chat_count(p) == 0]
                if free_agents:
                    best = free_agents[0]
                else:
                    # Prioridad 4: cualquier agente con menos chats
                    best = min(all_candidates, key=active_chat_count)

            if best:
                conversation.assigned_to = best.user
                logger.info(f'Auto-asignado conv {conversation.id} a {best.user.username}')
        except Exception as e:
            logger.error(f'Error en auto-asignación: {e}')

    @staticmethod
    def send_agent_reply(conversation, agent_message, media_file=None):
        """
        Envía una respuesta del recepcionista.
        Se guarda como mensaje 'agent' y además se envía por WhatsApp si corresponde.
        """
        metadata = {}
        secure_url = None
        media_type = None

        if media_file:
            try:
                import cloudinary.uploader
                content_type = media_file.content_type
                if content_type.startswith('image/'):
                    media_type = 'image'
                elif content_type.startswith('video/'):
                    media_type = 'video'
                elif content_type.startswith('audio/'):
                    media_type = 'audio'
                else:
                    media_type = 'document'

                import re
                clean_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', media_file.name)
                
                upload_kwargs = {'resource_type': 'auto'}
                if media_type == 'document':
                    upload_kwargs['resource_type'] = 'raw'
                    upload_kwargs['public_id'] = f"agent_upload_{conversation.id}_{clean_name}"
                    upload_kwargs['attachment'] = True
                elif media_type == 'audio':
                    upload_kwargs['resource_type'] = 'video'
                    upload_kwargs['format'] = 'ogg'
                    # Para que WhatsApp lo reconozca como "Nota de voz" en lugar de archivo de audio genérico
                    # requiere el contenedor OGG con el codec de audio Opus.
                    upload_kwargs['audio_codec'] = 'opus'

                upload_result = cloudinary.uploader.upload(
                    media_file.read(),
                    **upload_kwargs
                )
                secure_url = upload_result.get('secure_url')
                if secure_url:
                    metadata['media_url'] = secure_url
                    metadata['media_type'] = media_type
            except Exception as e:
                logger.error(f'Error uploading media from agent reply: {e}')

        if not agent_message and secure_url:
            agent_message = "[Archivo Adjunto]"

        Message.objects.create(
            conversation=conversation,
            role='agent',
            content=agent_message,
            metadata=metadata
        )
        
        # Enviar mensaje por WhatsApp
        if conversation.contact.platform == 'whatsapp':
            try:
                config = conversation.business.config
                if config.whatsapp_token and config.whatsapp_phone_id:
                    if secure_url:
                        # Para audio no se permite caption
                        if media_type == 'audio':
                            media_to_send = secure_url
                            # Subir archivo directamente a Meta para forzar renderizado de Voice Note (PTT)
                            meta_media_id = WhatsAppService.upload_media_by_url(
                                phone_number_id=config.whatsapp_phone_id,
                                access_token=config.whatsapp_token,
                                media_url=secure_url,
                                mime_type='audio/ogg'
                            )
                            if meta_media_id:
                                media_to_send = meta_media_id

                            WhatsAppService.send_media_message(
                                phone_number_id=config.whatsapp_phone_id,
                                access_token=config.whatsapp_token,
                                recipient=conversation.contact.external_id,
                                media_url=media_to_send,
                                media_type=media_type,
                                caption=None,
                                is_voice_note=True if meta_media_id else False
                            )
                            # Si además hay texto real escrito, mandarlo como segundo mensaje
                            if agent_message and agent_message != "[Archivo Adjunto]":
                                WhatsAppService.send_text_message(
                                    phone_number_id=config.whatsapp_phone_id,
                                    access_token=config.whatsapp_token,
                                    recipient=conversation.contact.external_id,
                                    text=agent_message
                                )
                        else:
                            caption = agent_message if agent_message and agent_message != "[Archivo Adjunto]" else None
                            WhatsAppService.send_media_message(
                                phone_number_id=config.whatsapp_phone_id,
                                access_token=config.whatsapp_token,
                                recipient=conversation.contact.external_id,
                                media_url=secure_url,
                                media_type=media_type,
                                caption=caption
                            )
                    elif agent_message:
                        WhatsAppService.send_text_message(
                            phone_number_id=config.whatsapp_phone_id,
                            access_token=config.whatsapp_token,
                            recipient=conversation.contact.external_id,
                            text=agent_message
                        )
                else:
                    logger.warning(f'Negocio {conversation.business.name} no tiene configurado WhatsApp.')
            except Exception as e:
                logger.error(f'Error al enviar mensaje WhatsApp desde send_agent_reply: {e}')

        return {
            'response': agent_message,
            'conversation_id': str(conversation.id),
            'contact_external_id': conversation.contact.external_id,
            'platform': conversation.contact.platform,
        }

    @staticmethod
    def takeover_conversation(conversation):
        """El recepcionista toma control de la conversación."""
        conversation.is_ai_active = False
        conversation.menu_state = 'human_chat'
        conversation.save(update_fields=['is_ai_active', 'menu_state'])

    @staticmethod
    def activate_ai(conversation):
        """Reactivar la IA en la conversación. Resetea contador de mensajes."""
        conversation.is_ai_active = True
        conversation.menu_state = 'ai_chat'
        conversation.ai_messages_in_session = 0
        conversation.save(update_fields=['is_ai_active', 'menu_state', 'ai_messages_in_session'])
