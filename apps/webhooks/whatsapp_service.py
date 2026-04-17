"""
Servicio para enviar mensajes a través de la WhatsApp Cloud API de Meta.
"""
import json
import logging
import requests

logger = logging.getLogger(__name__)

GRAPH_API_URL = 'https://graph.facebook.com/v21.0'


class WhatsAppService:
    """
    Envía mensajes de texto usando la WhatsApp Cloud API.
    Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
    """

    @staticmethod
    def send_text_message(phone_number_id, access_token, recipient, text):
        """
        Envía un mensaje de texto a un número de WhatsApp.

        Args:
            phone_number_id: Phone Number ID de Meta
            access_token: Token de acceso de la Cloud API
            recipient: Número de teléfono del destinatario (formato internacional sin +)
            text: Texto del mensaje

        Returns:
            dict con la respuesta de la API o None si falla
        """
        url = f'{GRAPH_API_URL}/{phone_number_id}/messages'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient,
            'type': 'text',
            'text': {
                'preview_url': False,
                'body': text,
            }
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            logger.info(f'Mensaje WhatsApp enviado a {recipient}: {result}')
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f'Error enviando mensaje WhatsApp a {recipient}: {e}')
            if hasattr(e, 'response') and e.response is not None:
                try:
                    logger.error(f'Respuesta de Meta: {e.response.text}')
                except Exception:
                    pass
            return None

    @staticmethod
    def send_media_message(phone_number_id, access_token, recipient, media_url, media_type, caption=None, is_voice_note=False):
        """
        Envía un mensaje multimedia a un número de WhatsApp.
        """
        url = f'{GRAPH_API_URL}/{phone_number_id}/messages'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        
        # Determine Meta media type string
        if media_type not in ['image', 'audio', 'video', 'document']:
            media_type = 'document'
            
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient,
            'type': media_type
        }
        
        if media_url.startswith('http'):
            payload[media_type] = {'link': media_url}
        else:
            payload[media_type] = {'id': media_url}
            
        if is_voice_note and media_type == 'audio':
            payload[media_type]['voice'] = True
        
        if caption and media_type in ['image', 'document', 'video']:
            payload[media_type]['caption'] = caption

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            logger.info(f'Mensaje multimedia ({media_type}) WhatsApp enviado a {recipient}: {result}')
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f'Error enviando mensaje multimedia WhatsApp a {recipient}: {e}')
            if hasattr(e, 'response') and e.response is not None:
                try:
                    logger.error(f'Respuesta de Meta: {e.response.text}')
                except Exception:
                    pass
            return None

    @staticmethod
    def upload_media_by_url(phone_number_id, access_token, media_url, mime_type='audio/ogg'):
        """
        Descarga un archivo público (ej. de Cloudinary) y lo sube directamente a los servidores de Meta.
        Esto es a menudo necesario para que Meta renderice audios como Notas de Voz Nativas (PTT).
        Retorna el `media_id` de Meta.
        """
        try:
            download_resp = requests.get(media_url, timeout=30)
            download_resp.raise_for_status()

            url = f'{GRAPH_API_URL}/{phone_number_id}/media'
            headers = {
                'Authorization': f'Bearer {access_token}',
            }
            files = {
                'file': ('audio.ogg', download_resp.content, mime_type)
            }
            data = {
                'messaging_product': 'whatsapp'
            }
            
            response = requests.post(url, headers=headers, data=data, files=files, timeout=30)
            response.raise_for_status()
            result = response.json()
            logger.info(f'Media subida a Meta exitosamente: {result}')
            return result.get('id')
        except requests.exceptions.RequestException as e:
            logger.error(f'Error subiendo media a Meta desde URL ({media_url}): {e}')
            if hasattr(e, 'response') and e.response is not None:
                try:
                    logger.error(f'Respuesta de Meta (Upload): {e.response.text}')
                except Exception:
                    pass
            return None

    @staticmethod
    def download_media(media_id, access_token, msg_type=None, file_name=None):
        """
        Descarga un archivo multimedia desde la WhatsApp Cloud API y lo sube a Cloudinary.
        
        Args:
            media_id: ID del archivo multimedia en WhatsApp.
            access_token: Token de acceso de la Cloud API.
            
        Returns:
            URL segura de Cloudinary o None si falla.
        """
        import cloudinary.uploader
        
        # 1. Obtener URL de descarga temporal
        url = f'{GRAPH_API_URL}/{media_id}'
        headers = {
            'Authorization': f'Bearer {access_token}',
        }
        
        try:
            # Petición para obtener metadata del medio
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            media_info = response.json()
            download_url = media_info.get('url')
            
            if not download_url:
                logger.error(f'No se obtuvo URL de descarga para media_id {media_id}')
                return None
                
            # 2. Descargar archivo binario
            media_resp = requests.get(download_url, headers=headers, timeout=30)
            media_resp.raise_for_status()
            
            # 3. Subir a Cloudinary
            upload_kwargs = {'resource_type': 'auto'}
            if msg_type == 'document':
                upload_kwargs['resource_type'] = 'raw'
                if file_name:
                    import re
                    clean_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', file_name)
                    upload_kwargs['public_id'] = f"{media_id}_{clean_name}"
                    # Esto forzará content-disposition: attachment al acceder por URL si fuera posible
                    upload_kwargs['attachment'] = True

            upload_result = cloudinary.uploader.upload(
                media_resp.content,
                **upload_kwargs
            )
            
            secure_url = upload_result.get('secure_url')
            logger.info(f'Media subida a Cloudinary: {secure_url}')
            return secure_url
            
        except requests.exceptions.RequestException as e:
            logger.error(f'Error descargando media de WhatsApp ({media_id}): {e}')
        except Exception as e:
            logger.error(f'Error subiendo media a Cloudinary ({media_id}): {e}')
            
        return None

    @staticmethod
    def send_interactive_list_message(phone_number_id, access_token, recipient,
                                      body_text, button_text, sections,
                                      header_text=None, footer_text=None):
        """
        Envía un mensaje interactivo tipo lista (Interactive List Message) de WhatsApp.

        Args:
            phone_number_id: Phone Number ID de Meta
            access_token: Token de acceso de la Cloud API
            recipient: Número de teléfono del destinatario
            body_text: Texto principal del mensaje (max 4096 chars)
            button_text: Texto del botón que abre la lista (max 20 chars)
            sections: Lista de secciones, cada una con title y rows.
                      Cada row: {id, title, description (opcional)}
                      Máximo 10 rows en total, max 10 secciones.
            header_text: Texto del header (opcional, max 60 chars)
            footer_text: Texto del footer (opcional, max 60 chars)

        Returns:
            dict con la respuesta de la API o None si falla
        """
        url = f'{GRAPH_API_URL}/{phone_number_id}/messages'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }

        interactive = {
            'type': 'list',
            'body': {
                'text': body_text[:4096],
            },
            'action': {
                'button': button_text[:20],
                'sections': sections,
            }
        }

        if header_text:
            interactive['header'] = {
                'type': 'text',
                'text': header_text[:60],
            }

        if footer_text:
            interactive['footer'] = {
                'text': footer_text[:60],
            }

        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient,
            'type': 'interactive',
            'interactive': interactive,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            logger.info(f'Interactive list enviada a {recipient}: {result}')
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f'Error enviando interactive list a {recipient}: {e}')
            if hasattr(e, 'response') and e.response is not None:
                try:
                    logger.error(f'Respuesta de Meta: {e.response.text}')
                except Exception:
                    pass
            return None

    @staticmethod
    def mark_as_read(phone_number_id, access_token, message_id):
        """
        Marca un mensaje como leído en WhatsApp.
        """
        url = f'{GRAPH_API_URL}/{phone_number_id}/messages'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': message_id,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.warning(f'Error marcando mensaje como leído: {e}')
