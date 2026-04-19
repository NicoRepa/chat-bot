"""
Signals para broadcast de eventos via Django Channels.
Envían actualizaciones en tiempo real al channel layer cuando
se crean/actualizan mensajes y conversaciones.
"""
import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Message, Conversation
from .serializers import serialize_message, serialize_conversation_snapshot

logger = logging.getLogger(__name__)

# Campos de Conversation que disparan un broadcast al cambiar
_CONV_TRACKED_FIELDS = {
    'status', 'is_ai_active', 'assigned_to_id',
    'classification', 'summary', 'panel_unread_count', 'updated_at',
}


def _safe_broadcast(func, *args, **kwargs):
    """Wrapper para no crashear si el channel layer no está disponible."""
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.warning('Error en broadcast: %s', exc)


@receiver(post_save, sender=Message)
def on_message_created(sender, instance, created, **kwargs):
    """Cuando se crea un mensaje nuevo, notificar vía WebSocket."""
    if not created:
        return

    msg = instance
    conv = msg.conversation

    # Importar aquí para evitar circular imports
    from .consumers import ConversationConsumer, InboxConsumer

    # 1. Broadcast al grupo de la conversación
    msg_payload = {
        'type': 'chat.message',
        'message': serialize_message(msg),
    }
    _safe_broadcast(
        ConversationConsumer.broadcast_to_conversation,
        str(conv.id), msg_payload
    )

    # 2. Broadcast al inbox del negocio
    inbox_payload = {
        'type': 'inbox.update',
        'kind': 'update',
        'conversation': serialize_conversation_snapshot(conv),
        'total_unread': conv.business.conversations.filter(status='open').sum_panel_unread(),
    }
    _safe_broadcast(
        InboxConsumer.broadcast_to_business,
        str(conv.business_id), inbox_payload
    )


@receiver(post_save, sender=Conversation)
def on_conversation_updated(sender, instance, created, **kwargs):
    """Cuando se actualiza una conversación, notificar si cambió un campo relevante."""
    conv = instance

    # Si es una creación, siempre notificar al inbox
    if created:
        from .consumers import InboxConsumer
        inbox_payload = {
            'type': 'inbox.update',
            'kind': 'new',
            'conversation': serialize_conversation_snapshot(conv),
            'total_unread': conv.business.conversations.filter(status='open').sum_panel_unread(),
        }
        _safe_broadcast(
            InboxConsumer.broadcast_to_business,
            str(conv.business_id), inbox_payload
        )
        return

    # Para updates: verificar si cambiaron campos relevantes
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        changed = set(update_fields) & _CONV_TRACKED_FIELDS
        if not changed:
            return

    from .consumers import ConversationConsumer, InboxConsumer

    snapshot = serialize_conversation_snapshot(conv)

    # Broadcast al grupo de la conversación
    conv_payload = {
        'type': 'conversation.updated',
        'conversation': snapshot,
    }
    _safe_broadcast(
        ConversationConsumer.broadcast_to_conversation,
        str(conv.id), conv_payload
    )

    # Broadcast al inbox
    inbox_payload = {
        'type': 'inbox.update',
        'kind': 'update',
        'conversation': snapshot,
        'total_unread': conv.business.conversations.filter(status='open').sum_panel_unread(),
    }
    _safe_broadcast(
        InboxConsumer.broadcast_to_business,
        str(conv.business_id), inbox_payload
    )


@receiver(post_delete, sender=Conversation)
def on_conversation_deleted(sender, instance, **kwargs):
    """Cuando se elimina una conversación, notificar al inbox."""
    conv = instance
    from .consumers import InboxConsumer

    inbox_payload = {
        'type': 'inbox.update',
        'kind': 'delete',
        'conversation': {
            'id': str(conv.id),
        },
        'total_unread': conv.business.conversations.filter(status='open').sum_panel_unread(),
    }
    _safe_broadcast(
        InboxConsumer.broadcast_to_business,
        str(conv.business_id), inbox_payload
    )
