"""
Serializers simples (dicts, sin DRF) para envío real-time via WebSocket.
"""

from django.utils import timezone

def serialize_message(msg):
    """Serializa un Message a dict para WebSocket."""
    return {
        'id': str(msg.id),
        'conversation_id': str(msg.conversation_id),
        'role': msg.role,
        'content': msg.content,
        'metadata': msg.metadata or {},
        'tokens_used': msg.tokens_used,
        'ai_cost': str(msg.ai_cost) if msg.ai_cost else '0',
        'created_at': timezone.localtime(msg.created_at).isoformat() if msg.created_at else '',
    }


def serialize_conversation_snapshot(conv):
    """Serializa un Conversation a dict liviano para inbox updates."""
    contact = conv.contact
    contact_name = contact.name or contact.external_id or ''

    # Asignación display
    assigned_display = ''
    if conv.assigned_to:
        assigned_display = conv.assigned_to.first_name or conv.assigned_to.username

    # Último mensaje del usuario para preview
    last_msg_preview = ''
    try:
        last_user_msg = conv.messages.filter(role='user').order_by('-created_at').first()
        if last_user_msg:
            content = last_user_msg.content or ''
            last_msg_preview = content[:60] + ('...' if len(content) > 60 else '')
    except Exception:
        pass

    # Clasificación info
    classification_info = {'key': '', 'label': 'Sin clasificar', 'icon': '🔘', 'color': '#9CA3AF'}
    try:
        info = conv.get_classification_display_info()
        classification_info = {
            'key': info.get('key', ''),
            'label': info.get('label', 'Sin clasificar'),
            'icon': info.get('icon', '🔘'),
            'color': info.get('color', '#9CA3AF'),
        }
    except Exception:
        pass

    return {
        'id': str(conv.id),
        'contact_id': str(contact.id),
        'contact_name': contact_name,
        'contact_initial': (contact_name[0].upper() if contact_name else '?'),
        'contact_platform': contact.platform,
        'contact_external_id': (contact.external_id or '')[:18],
        'assigned_to_display': assigned_display,
        'summary': conv.summary or '',
        'last_msg_preview': last_msg_preview,
        'updated_at': timezone.localtime(conv.updated_at).isoformat() if conv.updated_at else '',
        'status': conv.status,
        'status_display': conv.get_status_display(),
        'is_ai_active': conv.is_ai_active,
        'panel_unread_count': conv.panel_unread_count or 0,
        'classification_info': classification_info,
    }
