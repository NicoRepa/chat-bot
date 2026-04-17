from django.db.models import Q
from django.conf import settings
from apps.conversations.models import Conversation
from apps.core.models import Business

def global_unread_conversations(request):
    """
    Agrega 'global_unread_count' al contexto de todos los templates.
    """
    if not request.user.is_authenticated:
        return {'global_unread_count': 0}

    try:
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return {'global_unread_count': 0}
            
        qs = Conversation.objects.filter(business=business, panel_unread_count__gt=0)
        
        config = getattr(business, 'config', None)
        is_agent_only = request.user.profile.role not in ['admin', 'supervisor']
        
        if config and is_agent_only and not config.supervisor_only_mode:
            if config.agent_visibility_mode == 'assigned_only':
                qs = qs.filter(assigned_to=request.user)
            else:
                qs = qs.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))

        return {
            'global_unread_count': qs.count(),
            'VAPID_PUBLIC_KEY': getattr(settings, 'VAPID_PUBLIC_KEY', '')
        }
    except Exception:
        return {'global_unread_count': 0}
