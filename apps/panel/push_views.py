import json
import logging
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from apps.core.models import PushSubscription
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class PushSubscribeView(LoginRequiredMixin, View):
    """
    Recibe la información de la suscripción (endpoint, p256dh, auth) 
    y la asocia al UserProfile del usuario actual.
    """
    login_url = '/admin/login/'

    def post(self, request):
        try:
            data = json.loads(request.body)
            subscription_info = data.get('subscription', {})
            endpoint = subscription_info.get('endpoint')
            keys = subscription_info.get('keys', {})
            p256dh = keys.get('p256dh')
            auth = keys.get('auth')

            if not endpoint or not p256dh or not auth:
                return JsonResponse({"status": "error", "message": "Datos de suscripción incompletos."}, status=400)

            # Guardar o actualizar la suscripción para el usuario actual
            user_profile = request.user.profile
            # Si el endpoint ya lo tiene otro usuario o el mismo, get_or_create
            # Para simplificar, buscamos si el endpoint existe, sino lo creamos asociado al user.
            sub, created = PushSubscription.objects.update_or_create(
                endpoint=endpoint,
                defaults={
                    'user': user_profile,
                    'p256dh': p256dh,
                    'auth': auth
                }
            )

            return JsonResponse({"status": "success", "message": "Suscripción guardada correctamente."})

        except Exception as e:
            logger.error(f"Error guardando suscripción push: {e}")
            return JsonResponse({"status": "error", "message": "Error interno al guardar la suscripción."}, status=500)
