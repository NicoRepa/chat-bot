"""
Management command: auto_close
Cierra conversaciones inactivas según la configuración del negocio.
Ejecutar con cron cada hora: python manage.py auto_close
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.core.models import Business
from apps.conversations.models import Conversation, Message


class Command(BaseCommand):
    help = 'Cierra automáticamente conversaciones inactivas según auto_close_hours.'

    def handle(self, *args, **options):
        businesses = Business.objects.filter(is_active=True).select_related('config')
        total_closed = 0

        for business in businesses:
            hours = business.config.auto_close_hours
            if hours <= 0:
                continue

            cutoff = timezone.now() - timedelta(hours=hours)
            stale = Conversation.objects.filter(
                business=business,
                status__in=['activa', 'en_seguimiento', 'esperando_cliente'],
                updated_at__lt=cutoff,
            )
            count = stale.count()
            if count > 0:
                for conv in stale:
                    conv.status = 'finalizada'
                    conv.is_ai_active = False
                    conv.save(update_fields=['status', 'is_ai_active'])
                    Message.objects.create(
                        conversation=conv,
                        role='system',
                        content=f'Conversación cerrada automáticamente por inactividad ({hours}h).',
                    )
                total_closed += count
                self.stdout.write(f'  {business.name}: {count} conversaciones cerradas')

        self.stdout.write(self.style.SUCCESS(f'Total: {total_closed} conversaciones cerradas.'))
