from django.urls import path
from . import views

app_name = 'webhooks'

urlpatterns = [
    path('incoming/', views.IncomingWebhookView.as_view(), name='incoming'),
    path(
        'conversations/<uuid:conversation_id>/takeover/',
        views.ConversationTakeoverView.as_view(),
        name='takeover'
    ),
    path(
        'conversations/<uuid:conversation_id>/activate-ai/',
        views.ConversationActivateAIView.as_view(),
        name='activate_ai'
    ),
    path(
        'conversations/<uuid:conversation_id>/reply/',
        views.AgentReplyView.as_view(),
        name='agent_reply'
    ),
    path(
        'whatsapp/',
        views.WhatsAppWebhookView.as_view(),
        name='whatsapp_webhook'
    ),
]
