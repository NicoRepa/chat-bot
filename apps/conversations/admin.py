from django.contrib import admin
from .models import Contact, Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'content', 'created_at')
    can_delete = False


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'external_id', 'platform', 'business', 'created_at')
    list_filter = ('platform', 'business')
    search_fields = ('name', 'external_id', 'phone', 'email')


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        'contact', 'business', 'status', 'classification',
        'classification_confidence', 'menu_state', 'is_ai_active', 'updated_at'
    )
    list_filter = ('status', 'classification', 'menu_state', 'is_ai_active', 'business')
    search_fields = ('contact__name', 'contact__external_id', 'summary')
    readonly_fields = ('summary', 'menu_selections')
    inlines = [MessageInline]
    list_editable = ('classification',)

    fieldsets = (
        ('Conversación', {
            'fields': ('contact', 'business', 'status', 'is_ai_active'),
        }),
        ('Clasificación IA', {
            'fields': ('classification', 'classification_confidence', 'summary'),
            'description': 'La IA asigna la clasificación. Podés cambiarla manualmente.',
        }),
        ('Menú Interactivo', {
            'fields': ('menu_state', 'menu_selections', 'current_menu_category_id'),
        }),
    )


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'role', 'short_content', 'created_at')
    list_filter = ('role',)
    readonly_fields = ('conversation', 'role', 'content', 'created_at')

    def short_content(self, obj):
        return obj.content[:80] + '...' if len(obj.content) > 80 else obj.content
    short_content.short_description = 'Contenido'
