"""
Celery tasks for the conversations app.
"""
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(name='apps.conversations.tasks.auto_reactivate_ai')
def auto_reactivate_ai():
    """
    Reactiva automáticamente la IA en conversaciones inactivas
    según la configuración de cada negocio (ai_auto_reactivate_value + unit).
    Se ejecuta periódicamente vía Celery Beat (cada 5 min).
    """
    from apps.core.models import Business
    from apps.conversations.models import Conversation, Message

    businesses = Business.objects.filter(is_active=True).select_related('config')
    total_reactivated = 0

    for business in businesses:
        config = business.config
        value = config.ai_auto_reactivate_value
        if value <= 0:
            continue

        unit = config.ai_auto_reactivate_unit
        if unit == 'hours':
            delta = timedelta(hours=value)
            display = f'{value} hora{"s" if value != 1 else ""}'
        else:
            delta = timedelta(minutes=value)
            display = f'{value} minuto{"s" if value != 1 else ""}'

        cutoff = timezone.now() - delta

        stale_convs = Conversation.objects.filter(
            business=business,
            is_ai_active=False,
            status__in=['activa', 'en_seguimiento', 'esperando_cliente'],
            updated_at__lt=cutoff,
        )

        count = stale_convs.count()
        if count > 0:
            for conv in stale_convs:
                conv.is_ai_active = True
                conv.human_needed_at = None
                conv.save(update_fields=['is_ai_active', 'human_needed_at'])
                Message.objects.create(
                    conversation=conv,
                    role='system',
                    content=f'🤖 La IA fue reactivada automáticamente por inactividad de {display}. (solo visible para vos)',
                )
            total_reactivated += count
            logger.info(f'{business.name}: {count} conversaciones reactivadas con IA')

    logger.info(f'auto_reactivate_ai: Total {total_reactivated} conversaciones reactivadas.')
    return total_reactivated
