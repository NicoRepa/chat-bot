"""
Vistas API para webhooks.
"""
import hashlib
import hmac
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
        Parsea el payload, extrae mensajes de texto y los procesa en background.
        """
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido'}, status=400)

        # La API de WhatsApp siempre envía object: 'whatsapp_business_account'
        if payload.get('object') != 'whatsapp_business_account':
            return HttpResponse('OK', status=200)

        # 1. Validar firma HMAC si hay app_secret configurado
        from apps.core.models import BusinessConfig
        configs_with_secret = BusinessConfig.objects.exclude(
            whatsapp_app_secret=''
        ).exclude(whatsapp_app_secret__isnull=True)[:1]

        if configs_with_secret:
            app_secret = configs_with_secret[0].whatsapp_app_secret
            signature_header = request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
            if not self._verify_signature(request.body, app_secret, signature_header):
                logger.warning('Firma X-Hub-Signature-256 inválida en webhook WhatsApp')
                return HttpResponse('Forbidden', status=403)

        # 2. Procesar el payload en un hilo separado (Non-blocking)
        # Esto asegura que Meta reciba el 200 OK inmediatamente.
        import threading
        from django.db import connection

        def process_threaded(payload_data):
            try:
                # Importar aquí para evitar circular imports y asegurar contexto
                from apps.webhooks.services import ChatOrchestrator
                from apps.webhooks.whatsapp_service import WhatsAppService
                from apps.core.models import BusinessConfig

                for entry in payload_data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        if 'messages' not in value:
                            continue

                        metadata = value.get('metadata', {})
                        phone_number_id = metadata.get('phone_number_id', '')

                        config = BusinessConfig.objects.filter(
                            whatsapp_phone_id=phone_number_id
                        ).select_related('business', 'business__config').first()

                        if not config:
                            continue

                        business = config.business

                        for message in value.get('messages', []):
                            msg_type = message.get('type')
                            sender = message.get('from', '')
                            msg_id = message.get('id', '')
                            
                            sender_name = ''
                            contacts = value.get('contacts', [])
                            if contacts:
                                profile = contacts[0].get('profile', {})
                                sender_name = profile.get('name', '')

                            msg_metadata = {'whatsapp_msg_id': msg_id}
                            text_body = ''

                            if msg_type == 'text':
                                text_body = message.get('text', {}).get('body', '')
                            elif msg_type == 'interactive':
                                interactive = message.get('interactive', {})
                                if interactive.get('type') == 'button_reply':
                                    text_body = interactive.get('button_reply', {}).get('title', '')
                                elif interactive.get('type') == 'list_reply':
                                    text_body = interactive.get('list_reply', {}).get('id', '')
                            elif msg_type in ['image', 'video', 'audio', 'document']:
                                media_data = message.get(msg_type, {})
                                media_id = media_data.get('id')
                                if media_id and config.whatsapp_token:
                                    secure_url = WhatsAppService.download_media(media_id, config.whatsapp_token, msg_type, media_data.get('filename', ''))
                                    if secure_url:
                                        msg_metadata['media_url'] = secure_url
                                        msg_metadata['media_type'] = msg_type
                                        text_body = media_data.get('caption', '')
                            
                            if not text_body and 'media_url' not in msg_metadata:
                                continue

                            # Marcar como leído
                            if msg_id and config.whatsapp_token:
                                WhatsAppService.mark_as_read(phone_number_id, config.whatsapp_token, msg_id)

                            # Procesar e IA
                            result = ChatOrchestrator.process_incoming_message(
                                business=business,
                                platform='whatsapp',
                                external_id=sender,
                                sender_name=sender_name,
                                message_text=text_body,
                                metadata=msg_metadata
                            )

                            # Enviar respuesta
                            response_text = result.get('response')
                            interactive_list = result.get('interactive_list')

                            if response_text and config.whatsapp_token and phone_number_id:
                                if interactive_list:
                                    WhatsAppService.send_interactive_list_message(
                                        phone_number_id, config.whatsapp_token, sender,
                                        interactive_list['body_text'], interactive_list['button_text'], interactive_list['sections'],
                                        interactive_list.get('header_text'), interactive_list.get('footer_text')
                                    )
                                else:
                                    WhatsAppService.send_text_message(phone_number_id, config.whatsapp_token, sender, response_text)

            except Exception as e:
                logger.error(f'Error en proceso asíncrono de webhook: {e}', exc_info=True)
            finally:
                # Cerrar la conexión en el hilo para evitar fugas/leaks
                connection.close()

        # Disparar hilo y retornar
        threading.Thread(target=process_threaded, args=(payload,), daemon=True).start()

        return HttpResponse('OK', status=200)

    @staticmethod
    def _verify_signature(body_bytes, app_secret, signature_header):
        """
        Valida X-Hub-Signature-256 de Meta.
        Retorna True si la firma es válida, False si no.
        Si no hay signature header, retorna False.
        """
        if not signature_header:
            return False
        # El header viene como "sha256=<hex_digest>"
        parts = signature_header.split('=', 1)
        if len(parts) != 2 or parts[0] != 'sha256':
            return False
        expected_sig = parts[1]
        if isinstance(app_secret, str):
            app_secret = app_secret.encode()
        computed = hmac.new(app_secret, body_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, expected_sig)
