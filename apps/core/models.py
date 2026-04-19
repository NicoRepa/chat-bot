import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify
from apps.core.fields import EncryptedCharField


class Business(models.Model):
    """
    Representa un negocio registrado en la plataforma.
    Cada negocio tiene su configuración de IA, menú y clasificaciones independientes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('Nombre', max_length=200)
    slug = models.SlugField('Slug', max_length=200, unique=True, blank=True)
    industry = models.CharField('Rubro', max_length=100, help_text='Ej: Taller mecánico, Odontología, Comercio')
    description = models.TextField('Descripción', blank=True)
    address = models.CharField('Dirección', max_length=300, blank=True)
    phone = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField('Email', blank=True)
    contact_info = models.JSONField(
        'Información de contacto extra',
        default=dict,
        blank=True,
        help_text='Datos extra: horarios, redes sociales, etc.'
    )
    is_active = models.BooleanField('Activo', default=True)
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        verbose_name = 'Negocio'
        verbose_name_plural = 'Negocios'
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class BusinessConfig(models.Model):
    """
    Configuración de IA y comportamiento del chatbot para un negocio.
    El cliente puede editar esto desde el panel.
    """
    business = models.OneToOneField(
        Business,
        on_delete=models.CASCADE,
        related_name='config',
        verbose_name='Negocio'
    )

    # Configuración IA
    ai_model = models.CharField(
        'Modelo de IA',
        max_length=100,
        default='gpt-4o-mini',
        help_text='Modelo de OpenAI a usar (gpt-4o-mini, gpt-4o, etc.)'
    )
    ai_feedback_enabled = models.BooleanField(
        'Feedback IA activado',
        default=True,
        help_text='Permitir a los administradores calificar las respuestas de la IA.'
    )
    system_prompt = models.TextField(
        'Prompt del sistema',
        blank=True,
        help_text='Instrucciones para la IA sobre cómo comportarse y responder'
    )
    knowledge_base = models.TextField(
        'Base de conocimiento',
        blank=True,
        help_text='Información del negocio: servicios, precios, horarios, preguntas frecuentes, etc. La IA usará esto para responder.'
    )
    temperature = models.FloatField(
        'Temperatura',
        default=0.7,
        help_text='Creatividad de la IA (0.0 = preciso, 1.0 = creativo)'
    )
    max_tokens = models.IntegerField(
        'Tokens máximos',
        default=500,
        help_text='Largo máximo de respuesta'
    )

    # Clasificaciones de leads (editables por el cliente)
    classification_categories = models.JSONField(
        'Categorías de clasificación',
        default=list,
        blank=True,
        help_text='Lista de clasificaciones para los leads. La IA asigna automáticamente, el recepcionista puede cambiarla.'
    )

    # Saludo y menú
    greeting_message = models.TextField(
        'Mensaje de saludo',
        blank=True,
        help_text='Mensaje de bienvenida personalizado. Si está vacío, se genera uno automático.'
    )
    menu_enabled = models.BooleanField(
        'Menú interactivo activo',
        default=True,
        help_text='Si está activo, el primer mensaje incluye el menú de opciones.'
    )

    # Webhook
    webhook_secret = models.CharField(
        'API Key para webhooks',
        max_length=200,
        blank=True,
        help_text='Clave para autenticar las peticiones entrantes al webhook'
    )

    # WhatsApp Cloud API
    whatsapp_phone_id = models.CharField(
        'WhatsApp Phone Number ID',
        max_length=100,
        blank=True,
        help_text='Phone Number ID de Meta (se encuentra en el dashboard de WhatsApp Business)'
    )
    whatsapp_token = EncryptedCharField(
        'WhatsApp Access Token',
        max_length=2000,
        blank=True,
        help_text='Token de acceso permanente de la WhatsApp Cloud API'
    )
    whatsapp_verify_token = models.CharField(
        'WhatsApp Verify Token',
        max_length=200,
        blank=True,
        help_text='Token de verificación elegido por vos para validar el webhook de Meta'
    )
    whatsapp_app_secret = EncryptedCharField(
        'WhatsApp App Secret',
        max_length=500,
        blank=True,
        help_text='App Secret de la app de Meta. Si está cargado, el webhook valida X-Hub-Signature-256 antes de procesar.'
    )

    # Auto-asignación y límites
    auto_assign_enabled = models.BooleanField(
        'Auto-asignación',
        default=False,
        help_text='Asignar automáticamente conversaciones a agentes por especialización.'
    )
    ai_max_messages = models.IntegerField(
        'Límite mensajes IA',
        default=0,
        help_text='Derivar a humano tras N mensajes de IA sin resolver. 0 = sin límite.'
    )
    auto_close_hours = models.IntegerField(
        'Auto-cierre (horas)',
        default=0,
        help_text='Cerrar conversaciones inactivas después de X horas. 0 = desactivado.'
    )

    # Visibilidad y modo de operación
    VISIBILITY_CHOICES = [
        ('all', 'Ver no asignadas + propias'),
        ('assigned_only', 'Solo asignadas'),
    ]
    agent_visibility_mode = models.CharField(
        'Visibilidad de agentes', max_length=20,
        choices=VISIBILITY_CHOICES, default='all',
        help_text='all = agentes ven conversaciones no asignadas + las suyas. assigned_only = solo las asignadas.'
    )
    supervisor_only_mode = models.BooleanField(
        'Modo solo supervisores', default=False,
        help_text='Si está activo, no se auto-asigna nada y todos ven todas las conversaciones.'
    )

    # Comportamiento del menú
    menu_force_selection = models.BooleanField(
        'Obligar selección del menú',
        default=True,
        help_text='Si está activo, el cliente debe elegir una opción del menú con un número. Si no, puede escribir texto libre y la IA responde.'
    )
    menu_reactivation_message = models.CharField(
        'Mensaje de reactivación del menú',
        max_length=300,
        blank=True,
        default='📋 _Escribí *1* para volver al menú._',
        help_text='Se agrega al final de cada respuesta de la IA para que el cliente pueda volver al menú.'
    )

    # Mensajes automáticos
    welcome_back_message = models.TextField(
        'Mensaje de bienvenida recurrente',
        blank=True,
        help_text='Mensaje para clientes que ya escribieron antes. Vacío = se usa el saludo normal.'
    )
    escalation_message = models.TextField(
        'Mensaje de derivación a humano',
        blank=True,
        default='👤 Te voy a comunicar con un agente para ayudarte mejor. En breve te atiende una persona. ¡Gracias por tu paciencia!',
        help_text='Se envía cuando la conversación se deriva a un humano.'
    )
    business_hours_enabled = models.BooleanField(
        'Horarios habilitados',
        default=True,
        help_text='Si está activo, la plataforma evalúa los horarios para decidir si responder dentro o fuera de horario.'
    )
    business_hours = models.JSONField(
        'Horarios del negocio',
        default=dict,
        blank=True,
        help_text='JSON con horarios. Ejemplo: {"lun-vie": "9:00-18:00", "sab": "9:00-13:00"}'
    )
    out_of_hours_message = models.TextField(
        'Mensaje fuera de horario',
        blank=True,
        help_text='Se envía automáticamente fuera de horario. Vacío = no se envía.'
    )

    class Meta:
        verbose_name = 'Configuración del negocio'
        verbose_name_plural = 'Configuraciones de negocios'

    def __str__(self):
        return f'Config: {self.business.name}'

    def save(self, *args, **kwargs):
        # Si no hay categorías, cargar las predefinidas
        if not self.classification_categories:
            self.classification_categories = settings.DEFAULT_LEAD_CLASSIFICATIONS
        # Generar API key si no tiene
        if not self.webhook_secret:
            self.webhook_secret = uuid.uuid4().hex
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    """
    Perfil extendido para control de agentes/recepcionistas.
    Relaciona un usuario estándar de Django con un negocio y un rol específico.
    """
    ROLE_CHOICES = [
        ('admin', 'Administrador'),
        ('supervisor', 'Supervisor'),
        ('agent', 'Agente'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='Usuario'
    )
    business = models.ForeignKey(
        Business,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name='Negocio'
    )
    role = models.CharField(
        'Rol', max_length=20,
        choices=ROLE_CHOICES, default='agent'
    )
    specializations = models.JSONField(
        'Especializaciones',
        default=list,
        blank=True,
        help_text='Clasificaciones que este agente atiende (ej: ventas, reclamos). Vacío = atiende todo.'
    )
    
    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuario'

    def __str__(self):
        return f'{self.user.username} - {self.get_role_display()} ({self.business})'

    @property
    def is_admin(self):
        return self.role == 'admin'
        
    @property
    def is_supervisor(self):
        return self.role == 'supervisor'
        
    @property
    def is_agent(self):
        return self.role == 'agent'

class PushSubscription(models.Model):
    """
    Guarda las suscripciones Web Push de un usuario para enviarle notificaciones
    incluso cuando tiene la app cerrada.
    """
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Suscripción Push'
        verbose_name_plural = 'Suscripciones Push'

    def __str__(self):
        return f"Push Sub - {self.user.user.username}"

