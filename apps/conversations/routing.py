"""
WebSocket URL routing para conversations.
"""
from django.urls import path
from .consumers import InboxConsumer, ConversationConsumer

websocket_urlpatterns = [
    path('ws/panel/inbox/', InboxConsumer.as_asgi()),
    path('ws/panel/conversations/<str:conversation_id>/', ConversationConsumer.as_asgi()),
]
