"""
Vistas API para webhooks.
"""
import json
import logging
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from apps.core.models import Business
from apps.conversations.models import Conversation
from .services import ChatOrchestrator
from .whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class IncomingWebhookView(View):
    """
    POST /api/webhooks/incoming/
    Recibe un mensaje entrante y devuelve la respuesta.

    Headers:
        X-API-Key: clave del negocio

    Body JSON:
        {
            "business_slug": "taller-chapa",
            "platform": "whatsapp",
            "external_id": "+5491112345678",
            "sender_name": "Juan Pérez",
            "message": "Hola"
        }
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido'}, status=400)

        # Validar campos requeridos
        required_fields = ['business_slug', 'platform', 'external_id', 'message']
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({'error': f'Campo requerido: {field}'}, status=400)

        # Buscar negocio
        try:
            business = Business.objects.select_related('config').get(
                slug=data['business_slug'],
                is_active=True
            )
        except Business.DoesNotExist:
            return JsonResponse({'error': 'Negocio no encontrado'}, status=404)

        # Autenticar con API key
        api_key = request.headers.get('X-API-Key', '')
        if business.config.webhook_secret and api_key != business.config.webhook_secret:
            return JsonResponse({'error': 'API key inválida'}, status=401)

        # Procesar mensaje
        try:
            result = ChatOrchestrator.process_incoming_message(
                business=business,
                platform=data['platform'],
                external_id=data['external_id'],
                sender_name=data.get('sender_name', ''),
                message_text=data['message'],
                metadata=data.get('metadata', {})
            )
            return JsonResponse(result)
        except Exception as e:
            logger.error(f'Error procesando mensaje: {e}', exc_info=True)
            return JsonResponse({
                'error': 'Error interno procesando el mensaje',
                'detail': str(e)
            }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class ConversationTakeoverView(View):
    """
    POST /api/webhooks/conversations/<id>/takeover/
    El recepcionista toma control de la conversación.
    """

    def post(self, request, conversation_id):
        try:
            conversation = Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist:
            return JsonResponse({'error': 'Conversación no encontrada'}, status=404)

        ChatOrchestrator.takeover_conversation(conversation)
        return JsonResponse({'status': 'ok', 'message': 'IA desactivada, control humano activo.'})


@method_decorator(csrf_exempt, name='dispatch')
class ConversationActivateAIView(View):
    """
    POST /api/webhooks/conversations/<id>/activate-ai/
    Reactivar la IA en la conversación.
    """

    def post(self, request, conversation_id):
        try:
            conversation = Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist:
            return JsonResponse({'error': 'Conversación no encontrada'}, status=404)

        ChatOrchestrator.activate_ai(conversation)
        return JsonResponse({'status': 'ok', 'message': 'IA reactivada.'})


@method_decorator(csrf_exempt, name='dispatch')
class AgentReplyView(View):
    """
    POST /api/webhooks/conversations/<id>/reply/
    El recepcionista envía una respuesta.

    Body JSON:
        {
            "message": "Hola, te confirmo el turno para el lunes."
        }
    """

    def post(self, request, conversation_id):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido'}, status=400)

        if not data.get('message'):
            return JsonResponse({'error': 'Campo requerido: message'}, status=400)

        try:
            conversation = Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist:
            return JsonResponse({'error': 'Conversación no encontrada'}, status=404)

        result = ChatOrchestrator.send_agent_reply(conversation, data['message'])
        return JsonResponse(result)


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):
    """
    Webhook para la WhatsApp Cloud API de Meta.

    GET  /api/whatsapp/webhook/ → Verificación del webhook (Meta envía hub.challenge)
    POST /api/whatsapp/webhook/ → Recibe notificaciones de mensajes entrantes
    """

    def get(self, request):
        """
        Verificación del webhook de Meta.
        Meta envía: hub.mode, hub.verify_token, hub.challenge
        """
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode == 'subscribe' and token and challenge:
            # Buscar un negocio que tenga este verify_token
            from apps.core.models import BusinessConfig
            config = BusinessConfig.objects.filter(
                whatsapp_verify_token=token
            ).first()

            if config:
                logger.info(f'Webhook WhatsApp verificado para {config.business.name}')
                return HttpResponse(challenge, content_type='text/plain', status=200)

        logger.warning('Verificación de webhook WhatsApp fallida')
        return HttpResponse('Forbidden', status=403)

    def post(self, request):
        """
        Recibe notificaciones de la WhatsApp Cloud API.
        Parsea el payload, extrae mensajes de texto y los procesa.
        """
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido'}, status=400)

        # La API de WhatsApp siempre envía object: 'whatsapp_business_account'
        if payload.get('object') != 'whatsapp_business_account':
            return HttpResponse('OK', status=200)

        # Procesar cada entry/change
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})

                # Solo procesar mensajes (no statuses)
                if 'messages' not in value:
                    continue

                metadata = value.get('metadata', {})
                phone_number_id = metadata.get('phone_number_id', '')

                # Buscar negocio por phone_number_id
                from apps.core.models import BusinessConfig
                config = BusinessConfig.objects.filter(
                    whatsapp_phone_id=phone_number_id
                ).select_related('business', 'business__config').first()

                if not config:
                    logger.warning(f'No se encontró negocio para phone_id: {phone_number_id}')
                    continue

                business = config.business

                for message in value.get('messages', []):
                    msg_type = message.get('type')
                    sender = message.get('from', '')
                    msg_id = message.get('id', '')

                    # Obtener nombre del contacto
                    sender_name = ''
                    contacts = value.get('contacts', [])
                    if contacts:
                        profile = contacts[0].get('profile', {})
                        sender_name = profile.get('name', '')

                    # Por ahora solo procesamos mensajes de texto
                    msg_metadata = {'whatsapp_msg_id': msg_id}
                    
                    if msg_type == 'text':
                        text_body = message.get('text', {}).get('body', '')
                    elif msg_type == 'interactive':
                        # Botones o listas interactivas
                        interactive = message.get('interactive', {})
                        if interactive.get('type') == 'button_reply':
                            text_body = interactive.get('button_reply', {}).get('title', '')
                        elif interactive.get('type') == 'list_reply':
                            # Usar el ID del row para identificar la selección
                            # (e.g., 'main_1', 'sub_2', 'back_main')
                            text_body = interactive.get('list_reply', {}).get('id', '')
                        else:
                            text_body = ''
                    elif msg_type in ['image', 'video', 'audio', 'document']:
                        media_data = message.get(msg_type, {})
                        media_id = media_data.get('id')
                        
                        if media_id and config.whatsapp_token:
                            # Descargar e subir a Cloudinary
                            file_name = media_data.get('filename', '')
                            secure_url = WhatsAppService.download_media(media_id, config.whatsapp_token, msg_type, file_name)
                            if secure_url:
                                msg_metadata['media_url'] = secure_url
                                msg_metadata['media_type'] = msg_type
                                msg_metadata['mime_type'] = media_data.get('mime_type', '')
                                
                                # Texto representativo
                                caption = media_data.get('caption', '')
                                if caption:
                                    text_body = caption
                                else:
                                    text_body = ''
                            else:
                                text_body = f'[Error descargando {msg_type}]'
                        else:
                            text_body = f'[{msg_type} adjunto sin procesar]'
                    else:
                        # Otros tipos no soportados
                        text_body = f'[{msg_type}]'

                    if not text_body and 'media_url' not in msg_metadata:
                        continue

                    # Marcar como leído
                    if msg_id and config.whatsapp_token:
                        WhatsAppService.mark_as_read(
                            phone_number_id, config.whatsapp_token, msg_id
                        )

                    # Procesar mensaje
                    try:
                        result = ChatOrchestrator.process_incoming_message(
                            business=business,
                            platform='whatsapp',
                            external_id=sender,
                            sender_name=sender_name,
                            message_text=text_body,
                            metadata=msg_metadata
                        )

                        # Enviar respuesta por WhatsApp
                        response_text = result.get('response')
                        interactive_list = result.get('interactive_list')

                        if response_text and config.whatsapp_token and phone_number_id:
                            if interactive_list:
                                # Enviar como Interactive List Message nativo de WhatsApp
                                WhatsAppService.send_interactive_list_message(
                                    phone_number_id=phone_number_id,
                                    access_token=config.whatsapp_token,
                                    recipient=sender,
                                    body_text=interactive_list['body_text'],
                                    button_text=interactive_list['button_text'],
                                    sections=interactive_list['sections'],
                                    header_text=interactive_list.get('header_text'),
                                    footer_text=interactive_list.get('footer_text'),
                                )
                            else:
                                WhatsAppService.send_text_message(
                                    phone_number_id=phone_number_id,
                                    access_token=config.whatsapp_token,
                                    recipient=sender,
                                    text=response_text,
                                )

                    except Exception as e:
                        logger.error(f'Error procesando mensaje WhatsApp: {e}', exc_info=True)

        # WhatsApp espera siempre 200 OK
        return HttpResponse('OK', status=200)

