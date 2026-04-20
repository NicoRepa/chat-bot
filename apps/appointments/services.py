"""
Lógica de disponibilidad y reserva de citas.
"""
from datetime import datetime, date, timedelta, time as dt_time
from django.utils import timezone

from .models import AppointmentConfig, WeeklyAvailability, BlockedPeriod, Appointment


class AppointmentService:

    @staticmethod
    def get_available_slots(config, target_date):
        """
        Retorna lista de tuplas (start_datetime, end_datetime) disponibles para un día.
        Tiene en cuenta: horario semanal, bloqueos, citas existentes, concurrencia.
        """
        day_of_week = target_date.weekday()  # 0=lunes, 6=domingo

        schedules = WeeklyAvailability.objects.filter(
            config=config, day_of_week=day_of_week, is_active=True
        )
        if not schedules.exists():
            return []

        # Día completo bloqueado
        if BlockedPeriod.objects.filter(config=config, date=target_date, is_full_day=True).exists():
            return []

        partial_blocks = list(BlockedPeriod.objects.filter(
            config=config, date=target_date, is_full_day=False
        ))

        slot_duration = timedelta(minutes=config.appointment_duration)
        buffer = timedelta(minutes=config.buffer_time)
        now = timezone.now()
        min_advance = timedelta(hours=config.min_advance_hours)
        max_advance = timedelta(days=config.advance_booking_days)

        # Verificar que la fecha esté dentro del rango permitido
        target_aware = timezone.make_aware(datetime.combine(target_date, dt_time.min))
        if target_aware > now + max_advance:
            return []

        available = []

        for schedule in schedules:
            current = timezone.make_aware(datetime.combine(target_date, schedule.start_time))
            end_of_schedule = timezone.make_aware(datetime.combine(target_date, schedule.end_time))

            while current + slot_duration <= end_of_schedule:
                slot_end = current + slot_duration

                # Saltar horarios pasados o muy próximos
                if current <= now + min_advance:
                    current += slot_duration + buffer
                    continue

                # Verificar bloqueos parciales
                blocked = False
                for block in partial_blocks:
                    bs = timezone.make_aware(datetime.combine(target_date, block.start_time))
                    be = timezone.make_aware(datetime.combine(target_date, block.end_time))
                    if current < be and slot_end > bs:
                        blocked = True
                        break

                if not blocked:
                    existing = Appointment.objects.filter(
                        config=config,
                        start_datetime=current,
                        status__in=['scheduled', 'confirmed'],
                    ).count()
                    if existing < config.max_concurrent:
                        available.append((current, slot_end))

                current += slot_duration + buffer

        return available

    @staticmethod
    def get_available_days(config, from_date=None, days_ahead=14):
        """Retorna lista de fechas que tienen al menos un slot disponible."""
        if from_date is None:
            from_date = date.today()

        days = []
        for i in range(days_ahead):
            d = from_date + timedelta(days=i)
            if AppointmentService.get_available_slots(config, d):
                days.append(d)
        return days

    @staticmethod
    def is_slot_available(config, start_datetime):
        """Verifica si un slot puntual está disponible."""
        slot_end = start_datetime + timedelta(minutes=config.appointment_duration)
        target_date = start_datetime.date()

        # Bloqueo total del día
        if BlockedPeriod.objects.filter(config=config, date=target_date, is_full_day=True).exists():
            return False, 'Ese día está bloqueado.'

        # Bloqueos parciales
        partial_blocks = BlockedPeriod.objects.filter(
            config=config, date=target_date, is_full_day=False
        )
        for block in partial_blocks:
            bs = timezone.make_aware(datetime.combine(target_date, block.start_time))
            be = timezone.make_aware(datetime.combine(target_date, block.end_time))
            if start_datetime < be and slot_end > bs:
                return False, 'Ese horario está bloqueado.'

        # Concurrencia
        existing = Appointment.objects.filter(
            config=config,
            start_datetime=start_datetime,
            status__in=['scheduled', 'confirmed'],
        ).count()
        if existing >= config.max_concurrent:
            return False, 'El horario ya no tiene disponibilidad.'

        # Máximo por día
        if config.max_per_day > 0:
            day_count = Appointment.objects.filter(
                config=config,
                start_datetime__date=target_date,
                status__in=['scheduled', 'confirmed'],
            ).count()
            if day_count >= config.max_per_day:
                return False, 'Se alcanzó el máximo de citas para ese día.'

        return True, None

    @staticmethod
    def book_appointment(config, contact_name, contact_phone, start_datetime,
                         conversation=None, notes='', created_by_ai=False):
        """
        Reserva una cita. Retorna (appointment, error_message).
        """
        ok, error = AppointmentService.is_slot_available(config, start_datetime)
        if not ok:
            return None, error

        slot_end = start_datetime + timedelta(minutes=config.appointment_duration)
        appt = Appointment.objects.create(
            config=config,
            conversation=conversation,
            contact_name=contact_name,
            contact_phone=contact_phone,
            start_datetime=start_datetime,
            end_datetime=slot_end,
            notes=notes,
            created_by_ai=created_by_ai,
        )
        return appt, None

    @staticmethod
    def format_confirmation(config, appointment):
        local_start = timezone.localtime(appointment.start_datetime)
        msg = config.confirmation_message
        msg = msg.replace('{slot_name}', config.slot_name)
        msg = msg.replace('{date}', local_start.strftime('%d/%m/%Y'))
        msg = msg.replace('{time}', local_start.strftime('%H:%M'))
        msg = msg.replace('{name}', appointment.contact_name)
        return msg

    @staticmethod
    def format_cancellation(config, appointment):
        local_start = timezone.localtime(appointment.start_datetime)
        msg = config.cancellation_message
        msg = msg.replace('{slot_name}', config.slot_name)
        msg = msg.replace('{date}', local_start.strftime('%d/%m/%Y'))
        msg = msg.replace('{time}', local_start.strftime('%H:%M'))
        msg = msg.replace('{name}', appointment.contact_name)
        return msg

    @staticmethod
    def format_slots_for_ai(config, slots, max_show=8):
        """Formatea slots disponibles para mostrar al cliente en el chat."""
        if not slots:
            return 'No hay horarios disponibles en los próximos días.'

        lines = [f'📅 *Horarios disponibles para tu {config.slot_name}:*\n']
        for i, (start, _) in enumerate(slots[:max_show], 1):
            local = timezone.localtime(start)
            lines.append(f'{i}. {local.strftime("%A %d/%m")} a las *{local.strftime("%H:%M")}*')
        lines.append('\n_Respondé con el número de la opción que preferís._')
        return '\n'.join(lines)
