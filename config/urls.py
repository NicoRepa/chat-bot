"""
URL Configuration del proyecto.
"""
from django.contrib import admin
from django.urls import path, include
import os
from django.conf import settings
from django.conf.urls.static import static
from django.http import FileResponse, HttpResponse
from django.views.generic import RedirectView
from apps.core.admin_views import backup_view
from apps.webhooks.views import WhatsAppWebhookView

def _serve_sw(request):
    """Serve service worker from root for full PWA scope."""
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    return FileResponse(open(sw_path, 'rb'), content_type='application/javascript')

urlpatterns = [
    path('sw.js', _serve_sw, name='service_worker'),
    path('health/', lambda request: HttpResponse('ok', content_type='text/plain'), name='health_check'),
    path('', RedirectView.as_view(url='/panel/', permanent=False)),
    path('admin/backup/', backup_view, name='admin_backup'),
    path('admin/', admin.site.urls),
    path('api/webhooks/', include('apps.webhooks.urls')),
    path('api/whatsapp/webhook/', WhatsAppWebhookView.as_view(), name='whatsapp_webhook_direct'),
    path('panel/', include('apps.panel.urls')),
    path('panel/citas/', include('apps.appointments.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Personalización del admin
admin.site.site_header = 'Chatbot IA - Panel de Administración'
admin.site.site_title = 'Chatbot IA Admin'
admin.site.index_title = 'Administración'
