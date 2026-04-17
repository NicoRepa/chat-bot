from django.contrib import admin
from .models import Business, BusinessConfig


class BusinessConfigInline(admin.StackedInline):
    model = BusinessConfig
    can_delete = False
    verbose_name_plural = 'Configuración'
    fieldsets = (
        ('Inteligencia Artificial', {
            'fields': ('ai_model', 'system_prompt', 'knowledge_base', 'temperature', 'max_tokens'),
        }),
        ('Clasificaciones de Leads', {
            'fields': ('classification_categories',),
            'description': 'La IA asigna la clasificación automáticamente. El recepcionista puede cambiarla manualmente.',
        }),
        ('Saludo y Menú', {
            'fields': ('greeting_message', 'menu_enabled'),
        }),
        ('Webhook / WhatsApp', {
            'fields': ('webhook_secret', 'whatsapp_phone_id', 'whatsapp_token', 'whatsapp_verify_token'),
        }),
    )


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'is_active', 'created_at')
    list_filter = ('is_active', 'industry')
    search_fields = ('name', 'industry')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BusinessConfigInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Asegurar que exista config (por si se crea desde otro lado sin inline)
        BusinessConfig.objects.get_or_create(business=form.instance)
