import uuid
from django.db import models
from django.conf import settings
from apps.core.models import Business


class Contact(models.Model):
    """
    Persona que se comunica con el negocio.
    Se identifica por external_id + platform (ej: número de WhatsApp).
    """
    PLATFORM_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook'),
        ('web', 'Web'),
        ('telegram', 'Telegram'),
        ('otro', 'Otro'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE,
        related_name='contacts', verbose_name='Negocio'
    )
    external_id = models.CharField(
        'ID externo', max_length=200,
        help_text='Identificador en la plataforma (ej: número de WhatsApp)'
    )
    platform = models.CharField(
        'Plataforma', max_length=20,
        choices=PLATFORM_CHOICES, default='whatsapp'
    )
    name = models.CharField('Nombre', max_length=200, blank=True)
    phone = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField('Email', blank=True)
    metadata = models.JSONField('Datos adicionales', default=dict, blank=True)
    created_at = models.DateTimeField('Primer contacto', auto_now_add=True)

    class Meta:
        verbose_name = 'Contacto'
        verbose_name_plural = 'Contactos'
        unique_together = ['business', 'external_id', 'platform']
        ordering = ['-created_at']

    def __str__(self):
        return self.name or self.external_id

class Tag(models.Model):
    """
    Etiqueta personalizable para clasificar conversaciones localmente.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE,
        related_name='tags', verbose_name='Negocio'
    )
    name = models.CharField('Nombre de etiqueta', max_length=50)
    color = models.CharField('Color (Hex, css)', max_length=30, default='#3b82f6')

    class Meta:
        verbose_name = 'Etiqueta'
        verbose_name_plural = 'Etiquetas'
        unique_together = ['business', 'name']
        ordering = ['name']

    def __str__(self):
        return self.name



class ConversationQuerySet(models.QuerySet):
    def sum_panel_unread(self):
        """Calcula la suma de mensajes no leídos del panel en el QuerySet actual."""
        return self.aggregate(total=models.Sum('panel_unread_count'))['total'] or 0

class ConversationManager(models.Manager):
    def get_queryset(self):
        return ConversationQuerySet(self.model, using=self._db)

    def sum_panel_unread(self):
        return self.get_queryset().sum_panel_unread()


class Conversation(models.Model):
    """
    Hilo de conversación entre un contacto y el chatbot/recepcionista.
    """
    objects = ConversationManager()

    STATUS_CHOICES = [
        ('activa', 'Activa'),
        ('en_seguimiento', 'En seguimiento'),
        ('esperando_cliente', 'Esperando cliente'),
        ('pausada', 'Pausada'),
        ('finalizada', 'Finalizada'),
        ('archivada', 'Archivada'),
    ]

    MENU_STATE_CHOICES = [
        ('initial', 'Mensaje inicial'),
        ('main_menu', 'Menú principal'),
        ('submenu', 'Sub-menú'),
        ('sub_submenu', 'Sub-sub-menú'),
        ('menu_response', 'Respuesta de menú'),
        ('ai_chat', 'Chat con IA'),
        ('human_chat', 'Chat con humano'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE,
        related_name='conversations', verbose_name='Contacto'
    )
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE,
        related_name='conversations', verbose_name='Negocio'
    )
    status = models.CharField(
        'Estado', max_length=20,
        choices=STATUS_CHOICES, default='activa'
    )

    # Clasificación IA (la IA asigna, el recepcionista puede cambiar)
    classification = models.CharField(
        'Clasificación', max_length=50, blank=True,
        help_text='Etiqueta asignada por la IA. El recepcionista puede cambiarla.'
    )
    classification_confidence = models.FloatField(
        'Confianza', default=0.0,
        help_text='Confianza de la IA en la clasificación (0.0 a 1.0)'
    )

    # Resumen IA para el recepcionista
    summary = models.TextField(
        'Resumen IA', blank=True,
        help_text='Resumen automático de lo que quiere el contacto para el recepcionista.'
    )

    # Estado del menú interactivo
    menu_state = models.CharField(
        'Estado del menú', max_length=20,
        choices=MENU_STATE_CHOICES, default='initial'
    )
    menu_selections = models.JSONField(
        'Selecciones del menú', default=list, blank=True,
        help_text='Historial de selecciones del menú del usuario.'
    )
    current_menu_category_id = models.CharField(
        'Categoría actual del menú', max_length=50, null=True, blank=True,
        help_text='ID de la categoría del menú en la que está el usuario.'
    )
    current_menu_subcategory_id = models.CharField(
        'Subcategoría actual del menú', max_length=50, null=True, blank=True,
        help_text='ID de la subcategoría del menú (para 3er nivel).'
    )

    # Control IA/Humano
    is_ai_active = models.BooleanField(
        'IA activa', default=True,
        help_text='Si está activo, la IA responde automáticamente. Si no, solo responde el humano.'
    )
    
    # Asignación y SLA
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_conversations',
        verbose_name='Agente asignado'
    )
    human_needed_at = models.DateTimeField(
        'Humano solicitado el', null=True, blank=True,
        help_text='Útil para medir el SLA (tiempo de espera) desde que el cliente esperó al agente.'
    )

    # Tracking de mensajes no leídos para el panel
    panel_unread_count = models.IntegerField(
        'Mensajes no leídos', default=0,
        help_text='Cantidad de mensajes nuevos del cliente desde que un agente abrió el chat por última vez.'
    )

    # Contador de mensajes IA en la sesión actual (se resetea al reactivar IA)
    ai_messages_in_session = models.IntegerField(
        'Mensajes IA en sesión', default=0,
        help_text='Cantidad de mensajes generados por IA en la sesión actual. Se resetea al reactivar la IA.'
    )

    tags = models.ManyToManyField(
        Tag, related_name='conversations', blank=True, verbose_name='Etiquetas'
    )

    created_at = models.DateTimeField('Inicio', auto_now_add=True)
    updated_at = models.DateTimeField('Último mensaje', auto_now=True)

    class Meta:
        verbose_name = 'Conversación'
        verbose_name_plural = 'Conversaciones'
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.contact} - {self.business.name} ({self.status})'

    def get_classification_display_info(self):
        """Devuelve la info de display de la clasificación actual."""
        if not self.classification:
            return {'key': '', 'label': 'Sin clasificar', 'icon': '🔘', 'color': '#9CA3AF'}
        try:
            config = self.business.config
            for cat in config.classification_categories:
                if cat['key'] == self.classification:
                    return cat
        except Exception:
            pass
        return {'key': self.classification, 'label': self.classification, 'icon': '🏷️', 'color': '#6B7280'}


class Message(models.Model):
    """
    Mensaje individual dentro de una conversación.
    """
    ROLE_CHOICES = [
        ('user', 'Usuario'),
        ('assistant', 'IA'),
        ('agent', 'Recepcionista'),
        ('system', 'Sistema'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE,
        related_name='messages', verbose_name='Conversación'
    )
    role = models.CharField('Rol', max_length=20, choices=ROLE_CHOICES)
    content = models.TextField('Contenido')
    metadata = models.JSONField(
        'Metadatos', default=dict, blank=True,
        help_text='Datos extra: tipo de media, archivos adjuntos, etc.'
    )
    tokens_used = models.IntegerField(
        'Tokens', default=0,
        help_text='Tokens consumidos por la IA en este mensaje.'
    )
    ai_cost = models.DecimalField(
        'Costo USD', max_digits=10, decimal_places=6, default=0,
        help_text='Costo estimado en dólares.'
    )
    created_at = models.DateTimeField('Fecha', auto_now_add=True)

    class Meta:
        verbose_name = 'Mensaje'
        verbose_name_plural = 'Mensajes'
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.role}] {self.content[:50]}'


class ContactNote(models.Model):
    """
    CRM Módulo: Notas internas del agente referidas a un contacto.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE,
        related_name='notes', verbose_name='Contacto'
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True,
        related_name='contact_notes', verbose_name='Autor'
    )
    content = models.TextField('Nota')
    created_at = models.DateTimeField('Fecha', auto_now_add=True)
    
    class Meta:
        verbose_name = 'Nota de Contacto'
        verbose_name_plural = 'Notas de Contacto'
        ordering = ['-created_at']
        
    def __str__(self):
        return f'Nota de {self.author} sobre {self.contact}'


class AIFeedback(models.Model):
    """
    Feedback interno sobre la calidad de respuesta de la IA.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.OneToOneField(
        Message, on_delete=models.CASCADE,
        related_name='feedback', verbose_name='Mensaje IA'
    )
    rating = models.IntegerField(
        'Calificación', choices=[(1, '👍 Positivo'), (-1, '👎 Negativo')]
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='Administrador'
    )
    comment = models.TextField('Comentario opcional', blank=True)
    created_at = models.DateTimeField('Fecha', auto_now_add=True)

    class Meta:
        verbose_name = 'Feedback de IA'
        verbose_name_plural = 'Feedbacks de IA'

    def __str__(self):
        rating_str = "Positivo" if self.rating == 1 else "Negativo"
        return f'{rating_str} - {self.message.id}'
