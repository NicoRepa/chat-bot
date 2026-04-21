"""
WebSocket consumers para el panel de recepcionistas.
"""
import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


class InboxConsumer(AsyncJsonWebsocketConsumer):
    """
    Consumer del inbox general del negocio.
    Grupo: inbox.{business_id}
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return

        self.business_id = await self._get_business_id(user)
        if not self.business_id:
            await self.close()
            return

        self.group_name = f'inbox.{self.business_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:
                pass

    async def receive_json(self, content, **kwargs):
        if content.get('action') == 'ping':
            await self.send_json({'type': 'pong'})

    async def inbox_update(self, event):
        await self.send_json(event)

    @database_sync_to_async
    def _get_business_id(self, user):
        try:
            # Superusuario: buscar el primer negocio activo
            if user.is_superuser:
                from apps.core.models import Business
                biz = Business.objects.filter(is_active=True).first()
                return str(biz.id) if biz else None
            return str(user.profile.business_id)
        except Exception:
            return None

    @classmethod
    def broadcast_to_business(cls, business_id, payload):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        group_name = f'inbox.{business_id}'
        try:
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception as exc:
            logger.warning('Error broadcasting to inbox %s: %s', business_id, exc)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    """
    Consumer de una conversación específica.
    Grupo: conversation.{conversation_id}
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return

        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']

        is_valid = await self._verify_ownership(user, self.conversation_id)
        if not is_valid:
            await self.close()
            return

        self.group_name = f'conversation.{self.conversation_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self._mark_read()
        await self._notify_read()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:
                pass

    async def receive_json(self, content, **kwargs):
        action = content.get('action', '')
        if action == 'mark_read':
            await self._mark_read()
            await self._notify_read()
        elif action == 'ping':
            await self.send_json({'type': 'pong'})

    async def chat_message(self, event):
        await self.send_json(event)

    async def conversation_updated(self, event):
        await self.send_json(event)

    @database_sync_to_async
    def _verify_ownership(self, user, conversation_id):
        try:
            # Superusuario: acceso total
            if user.is_superuser:
                return True
            from apps.conversations.models import Conversation
            conv = Conversation.objects.select_related('business').get(pk=conversation_id)
            return str(conv.business_id) == str(user.profile.business_id)
        except Exception:
            return False

    @database_sync_to_async
    def _mark_read(self):
        try:
            from apps.conversations.models import Conversation
            Conversation.objects.filter(
                pk=self.conversation_id,
                panel_unread_count__gt=0
            ).update(panel_unread_count=0)
        except Exception as exc:
            logger.warning('Error marking conversation as read: %s', exc)

    @database_sync_to_async
    def _get_snapshot_and_total(self):
        from apps.conversations.models import Conversation
        from apps.conversations.serializers import serialize_conversation_snapshot
        conv = Conversation.objects.select_related('business').get(pk=self.conversation_id)
        snapshot = serialize_conversation_snapshot(conv)
        try:
            total_unread = Conversation.objects.filter(
                business=conv.business
            ).sum_panel_unread()
        except Exception:
            total_unread = 0
        return snapshot, str(conv.business_id), total_unread

    async def _notify_read(self):
        try:
            snapshot, business_id, total_unread = await self._get_snapshot_and_total()
            await self.channel_layer.group_send(
                f'inbox.{business_id}',
                {
                    'type': 'inbox.update',
                    'kind': 'update',
                    'conversation': snapshot,
                    'total_unread': total_unread,
                }
            )
        except Exception as exc:
            logger.warning('Error notifying read to inbox: %s', exc)

    @classmethod
    def broadcast_to_conversation(cls, conversation_id, payload):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        group_name = f'conversation.{conversation_id}'
        try:
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception as exc:
            logger.warning('Error broadcasting to conversation %s: %s', conversation_id, exc)
