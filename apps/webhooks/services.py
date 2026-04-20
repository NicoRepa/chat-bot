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

        # 4. Si la IA no está activa, o si está desactivada globalmente para el negocio
        if not conversation.is_ai_active or getattr(business.config, 'ai_globally_disabled', False):
            conversation.save(update_fields=['panel_unread_count', 'updated_at'])
            return {
                'response': None,
                'conversation_id': str(conversation.id),
                'status': 'waiting_for_agent',
                'message': 'IA desactivada, esperando respuesta del recepcionista.'
            }

        # 5. Procesar según el estado del menú
        usage_acc = {'tokens': 0, 'cost': 0.0}
        response_data = ChatOrchestrator._process_by_state(conversation, message_text, business, usage_acc)

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

        # 7. Clasificar y resumir (en background, cada 3 mensajes del usuario)
        user_msg_count = conversation.messages.filter(role='user').count()
        if user_msg_count >= 2 and user_msg_count % 3 == 0:
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
    def _process_by_state(conversation, message_text, business, usage_acc=None):
        """
        Procesa el mensaje según el estado actual del menú.
        Retorna un string (texto plano) o un dict {'text': str, 'interactive_list': dict}
        cuando corresponde enviar un Interactive List Message de WhatsApp.
        """
        if usage_acc is None:
            usage_acc = {'tokens': 0, 'cost': 0.0}
        config = business.config
        state = conversation.menu_state

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

        # Chat con IA
        elif state == 'ai_chat':
            msg_stripped = message_text.strip()
            msg_lower = msg_stripped.lower()

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
