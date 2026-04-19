from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.conversations'
    verbose_name = 'Conversaciones'

    def ready(self):
        import apps.conversations.signals  # noqa: F401
