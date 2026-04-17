import json
import logging
from pywebpush import webpush, WebPushException
from django.conf import settings
from apps.core.models import PushSubscription

logger = logging.getLogger(__name__)

# Lee la clave de settings o usa la local
VAPID_PRIVATE_KEY = getattr(settings, 'VAPID_PRIVATE_KEY', 'private_key.pem')
VAPID_CLAIMS = {
    "sub": "mailto:admin@chatbot-ia.local"
}

def send_push_to_users(user_ids, title, body, url=None):
    """
    Envía una notificación web push a los usuarios indicados.
    Si user_ids es None, se envía a todos los administradores/supervisores.
    """
    if not user_ids:
        # Por defecto enviamos a todos los que tengan rol admin o supervisor
        # Para simplificar, buscamos los perfiles que no son "agent" o todos si es chico
        subs = PushSubscription.objects.all()
    else:
        subs = PushSubscription.objects.filter(user__user__id__in=user_ids)

    for sub in subs:
        try:
            subscription_data = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth
                }
            }
            
            payload = json.dumps({
                "title": title,
                "body": body,
                "url": url or "/panel/conversaciones/"
            })

            webpush(
                subscription_info=subscription_data,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
        except WebPushException as ex:
            logger.error(f"Web push error: {repr(ex)}")
            # Si el endpoint expiró (410), podríamos borrar la suscripción
            if ex.response and ex.response.status_code == 410:
                sub.delete()
        except Exception as e:
            logger.error(f"Exception sending push: {str(e)}")
