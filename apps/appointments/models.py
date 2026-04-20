import uuid
from django.db import models
from apps.core.models import Business
from apps.conversations.models import Conversation


class AppointmentConfig(models.Model):
    """Configuración del sistema de citas para un negocio."""
    business = models.OneToOneField(
        Business, on_delete=models.CASCADE,
        related_name='appointment_config', verbose_name='Negocio'
    )
    is_enabled = models.BooleanField('Sistema de citas activo', default=False)
    slot_name = models.CharField(
        'Nombre del turno', max_length=50, default='Cita',
        help_text='Ej: Cita, Turno, Consulta, Reunión'
    )
    appointment_duration = models.IntegerField(
        'Duración por cita (min)', default=60,
        help_text='Duración de cada cita en minutos.'
    )
    buffer_time = models.IntegerField(
        'Tiempo entre citas (min)', default=0,
        help_text='Tiempo de preparación entre citas consecutivas.'
    )
    max_per_day = models.IntegerField(
        'Máx. citas por día', default=0,
        help_text='0 = sin límite.'
    )
    max_concurrent = models.IntegerField(
        'Citas simultáneas por horario', default=1,
        help_text='Cuántas citas pueden coexistir en el mismo horario.'
    )
    advance_booking_days = models.IntegerField(
        'Días de anticipación máxima', default=30,
        help_text='Con cuántos días de anticipación se puede reservar.'
    )
    min_advance_hours = models.IntegerField(
        'Horas mínimas de anticipación', default=2,
        help_text='Mínimo de horas antes del turno para poder reservarlo.'
    )
    confirmation_message = models.TextField(
        'Mensaje de confirmación', blank=True,
        default='✅ ¡{slot_name} confirmada!\n📅 Fecha: {date}\n🕐 Hora: {time}\n\nSi necesitás cancelar o reprogramar, escribinos con anticipación.'
    )
    cancellation_message = models.TextField(
        'Mensaje de cancelación', blank=True,
        default='❌ Tu {slot_name} del {date} a las {time} fue cancelada.\n¿Querés reagendar? Escribinos cuando quieras.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuración de citas'
        verbose_name_plural = 'Configuraciones de citas'

    def __str__(self):
        return f'Config citas: {self.business.name}'


class WeeklyAvailability(models.Model):
    """Horario disponible semanal recurrente."""
    DAY_CHOICES = [
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'),
        (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
    ]
    config = models.ForeignKey(
        AppointmentConfig, on_delete=models.CASCADE,
        related_name='weekly_schedule', verbose_name='Configuración'
    )
    day_of_week = models.IntegerField('Día', choices=DAY_CHOICES)
    start_time = models.TimeField('Hora inicio')
    end_time = models.TimeField('Hora fin')
    is_active = models.BooleanField('Activo', default=True)

    class Meta:
        verbose_name = 'Disponibilidad semanal'
        verbose_name_plural = 'Disponibilidades semanales'
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        return f'{self.get_day_of_week_display()} {self.start_time}–{self.end_time}'


class BlockedPeriod(models.Model):
    """Fecha/período bloqueado: feriado, emergencia, día libre, etc."""
    config = models.ForeignKey(
        AppointmentConfig, on_delete=models.CASCADE,
        related_name='blocked_periods', verbose_name='Configuración'
    )
    date = models.DateField('Fecha')
    start_time = models.TimeField('Hora inicio', null=True, blank=True)
    end_time = models.TimeField('Hora fin', null=True, blank=True)
    is_full_day = models.BooleanField('Día completo', default=True)
    reason = models.CharField('Motivo', max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Período bloqueado'
        verbose_name_plural = 'Períodos bloqueados'
        ordering = ['date', 'start_time']

    def __str__(self):
        label = 'Todo el día' if self.is_full_day else f'{self.start_time}–{self.end_time}'
        return f'{self.date} — {label}: {self.reason or "Sin motivo"}'


class Appointment(models.Model):
    """Una cita agendada."""
    STATUS_CHOICES = [
        ('scheduled', 'Agendada'),
        ('confirmed', 'Confirmada'),
        ('cancelled', 'Cancelada'),
        ('completed', 'Completada'),
        ('no_show', 'No se presentó'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(
        AppointmentConfig, on_delete=models.CASCADE,
        related_name='appointments', verbose_name='Configuración'
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='appointments'
    )
    contact_name = models.CharField('Nombre', max_length=200)
    contact_phone = models.CharField('Teléfono', max_length=50, blank=True)
    start_datetime = models.DateTimeField('Inicio')
    end_datetime = models.DateTimeField('Fin')
    status = models.CharField(
        'Estado', max_length=20, choices=STATUS_CHOICES, default='scheduled'
    )
    notes = models.TextField('Notas', blank=True)
    created_by_ai = models.BooleanField('Creada por IA', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cita'
        verbose_name_plural = 'Citas'
        ordering = ['start_datetime']

    def __str__(self):
        return f'{self.contact_name} — {self.start_datetime.strftime("%d/%m/%Y %H:%M")}'

    @property
    def duration_minutes(self):
        delta = self.end_datetime - self.start_datetime
        return int(delta.total_seconds() / 60)

    def get_status_color(self):
        return {
            'scheduled': '#3B82F6',
            'confirmed': '#10B981',
            'cancelled': '#EF4444',
            'completed': '#6B7280',
            'no_show': '#F59E0B',
        }.get(self.status, '#6B7280')
