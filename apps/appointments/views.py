import json
from datetime import datetime, date, timedelta
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from apps.core.models import Business
from .models import AppointmentConfig, WeeklyAvailability, BlockedPeriod, Appointment
from .services import AppointmentService


def _get_config(business):
    config, _ = AppointmentConfig.objects.get_or_create(business=business)
    return config


def _check_feature(business):
    """Retorna True si el módulo de citas está habilitado para el negocio."""
    return getattr(business, 'feature_appointments', False)


class AppointmentCalendarView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).select_related('appointment_config').first()
        if not business or not _check_feature(business):
            return redirect('panel:dashboard')
        config = _get_config(business)
        context = {
            'business': business,
            'config': config,
        }
        return render(request, 'panel/appointments/calendar.html', context)


class AppointmentEventsView(LoginRequiredMixin, View):
    """JSON feed para FullCalendar."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business:
            return JsonResponse([], safe=False)

        config = _get_config(business)
        start_str = request.GET.get('start', '')
        end_str = request.GET.get('end', '')

        events = []

        # Appointments
        qs = Appointment.objects.filter(config=config)
        if start_str:
            try:
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                qs = qs.filter(start_datetime__gte=start_dt)
            except Exception:
                pass
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                qs = qs.filter(start_datetime__lt=end_dt)
            except Exception:
                pass

        for appt in qs:
            events.append({
                'id': str(appt.id),
                'type': 'appointment',
                'title': f'🗓 {appt.contact_name}',
                'start': appt.start_datetime.isoformat(),
                'end': appt.end_datetime.isoformat(),
                'backgroundColor': appt.get_status_color(),
                'borderColor': appt.get_status_color(),
                'textColor': '#fff',
                'extendedProps': {
                    'status': appt.status,
                    'status_display': appt.get_status_display(),
                    'contact_name': appt.contact_name,
                    'contact_phone': appt.contact_phone,
                    'notes': appt.notes,
                    'created_by_ai': appt.created_by_ai,
                    'duration': appt.duration_minutes,
                },
            })

        # Blocked periods
        blocked_qs = BlockedPeriod.objects.filter(config=config)
        for block in blocked_qs:
            if block.is_full_day:
                events.append({
                    'id': f'block_{block.id}',
                    'type': 'block',
                    'title': f'🚫 {block.reason or "Bloqueado"}',
                    'start': block.date.isoformat(),
                    'allDay': True,
                    'backgroundColor': '#EF4444',
                    'borderColor': '#DC2626',
                    'textColor': '#fff',
                    'extendedProps': {
                        'block_id': block.id,
                        'reason': block.reason,
                        'is_full_day': True,
                    },
                })
            else:
                start_dt = datetime.combine(block.date, block.start_time)
                end_dt = datetime.combine(block.date, block.end_time)
                events.append({
                    'id': f'block_{block.id}',
                    'type': 'block',
                    'title': f'🚫 {block.reason or "Bloqueado"}',
                    'start': start_dt.isoformat(),
                    'end': end_dt.isoformat(),
                    'backgroundColor': '#EF4444',
                    'borderColor': '#DC2626',
                    'textColor': '#fff',
                    'extendedProps': {
                        'block_id': block.id,
                        'reason': block.reason,
                        'is_full_day': False,
                    },
                })

        return JsonResponse(events, safe=False)


class AppointmentCreateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        data = json.loads(request.body)

        contact_name = data.get('contact_name', '').strip()
        contact_phone = data.get('contact_phone', '').strip()
        start_str = data.get('start_datetime', '')
        notes = data.get('notes', '').strip()

        if not contact_name or not start_str:
            return JsonResponse({'ok': False, 'error': 'Faltan datos.'}, status=400)

        try:
            start_dt = datetime.fromisoformat(start_str)
            if timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Fecha inválida.'}, status=400)

        appt, error = AppointmentService.book_appointment(
            config, contact_name, contact_phone, start_dt, notes=notes
        )
        if error:
            return JsonResponse({'ok': False, 'error': error}, status=409)

        return JsonResponse({
            'ok': True,
            'id': str(appt.id),
            'start': appt.start_datetime.isoformat(),
            'end': appt.end_datetime.isoformat(),
            'contact_name': appt.contact_name,
        })


class AppointmentDetailView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def get(self, request, appointment_id):
        appt = get_object_or_404(Appointment, pk=appointment_id)
        local_start = timezone.localtime(appt.start_datetime)
        local_end = timezone.localtime(appt.end_datetime)
        return JsonResponse({
            'id': str(appt.id),
            'contact_name': appt.contact_name,
            'contact_phone': appt.contact_phone,
            'start': local_start.strftime('%d/%m/%Y %H:%M'),
            'end': local_end.strftime('%H:%M'),
            'status': appt.status,
            'status_display': appt.get_status_display(),
            'notes': appt.notes,
            'created_by_ai': appt.created_by_ai,
            'duration': appt.duration_minutes,
            'conversation_id': str(appt.conversation_id) if appt.conversation_id else None,
        })

    def post(self, request, appointment_id):
        appt = get_object_or_404(Appointment, pk=appointment_id)
        data = json.loads(request.body)
        action = data.get('action')

        if action == 'cancel':
            appt.status = 'cancelled'
            appt.save(update_fields=['status', 'updated_at'])
        elif action == 'confirm':
            appt.status = 'confirmed'
            appt.save(update_fields=['status', 'updated_at'])
        elif action == 'complete':
            appt.status = 'completed'
            appt.save(update_fields=['status', 'updated_at'])
        elif action == 'no_show':
            appt.status = 'no_show'
            appt.save(update_fields=['status', 'updated_at'])
        elif action == 'update':
            appt.contact_name = data.get('contact_name', appt.contact_name).strip()
            appt.contact_phone = data.get('contact_phone', appt.contact_phone).strip()
            appt.notes = data.get('notes', appt.notes).strip()
            appt.save(update_fields=['contact_name', 'contact_phone', 'notes', 'updated_at'])
        else:
            return JsonResponse({'ok': False, 'error': 'Acción inválida.'}, status=400)

        return JsonResponse({'ok': True, 'status': appt.status})


class BlockedPeriodCreateView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        data = json.loads(request.body)

        date_str = data.get('date', '')
        is_full_day = data.get('is_full_day', True)
        start_time_str = data.get('start_time', '')
        end_time_str = data.get('end_time', '')
        reason = data.get('reason', '').strip()

        try:
            block_date = date.fromisoformat(date_str)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Fecha inválida.'}, status=400)

        kwargs = dict(config=config, date=block_date, is_full_day=is_full_day, reason=reason)
        if not is_full_day:
            try:
                kwargs['start_time'] = datetime.strptime(start_time_str, '%H:%M').time()
                kwargs['end_time'] = datetime.strptime(end_time_str, '%H:%M').time()
            except Exception:
                return JsonResponse({'ok': False, 'error': 'Horario inválido.'}, status=400)

        block = BlockedPeriod.objects.create(**kwargs)
        return JsonResponse({'ok': True, 'id': block.id})


class BlockedPeriodDeleteView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, block_id):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        block = get_object_or_404(BlockedPeriod, pk=block_id, config=config)
        block.delete()
        return JsonResponse({'ok': True})


class AvailableSlotsView(LoginRequiredMixin, View):
    """Devuelve slots disponibles para una fecha dada (usado por panel y por IA)."""
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        date_str = request.GET.get('date', '')

        try:
            target = date.fromisoformat(date_str)
        except Exception:
            return JsonResponse({'error': 'Fecha inválida'}, status=400)

        slots = AppointmentService.get_available_slots(config, target)
        result = [
            {
                'start': s.isoformat(),
                'end': e.isoformat(),
                'label': timezone.localtime(s).strftime('%H:%M'),
            }
            for s, e in slots
        ]
        return JsonResponse({'slots': result, 'date': date_str})


class AppointmentConfigView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def get(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business or not _check_feature(business):
            return redirect('panel:dashboard')
        config = _get_config(business)
        weekly = WeeklyAvailability.objects.filter(config=config)

        # Build a dict day→schedules for the template
        schedule_by_day = {d: [] for d in range(7)}
        for s in weekly:
            schedule_by_day[s.day_of_week].append(s)

        days_meta = [
            (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'),
            (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
        ]
        context = {
            'business': business,
            'config': config,
            'schedule_by_day': schedule_by_day,
            'days_meta': days_meta,
            'saved': request.GET.get('saved') == '1',
        }
        return render(request, 'panel/appointments/config.html', context)

    def post(self, request):
        business = Business.objects.filter(is_active=True).first()
        if not business or not _check_feature(business):
            return redirect('panel:dashboard')
        config = _get_config(business)

        config.is_enabled = request.POST.get('is_enabled') == 'on'
        config.slot_name = request.POST.get('slot_name', 'Cita').strip()
        try:
            config.appointment_duration = int(request.POST.get('appointment_duration', 60))
            config.buffer_time = int(request.POST.get('buffer_time', 0))
            config.max_per_day = int(request.POST.get('max_per_day', 0))
            config.max_concurrent = int(request.POST.get('max_concurrent', 1))
            config.advance_booking_days = int(request.POST.get('advance_booking_days', 30))
            config.min_advance_hours = int(request.POST.get('min_advance_hours', 2))
        except (ValueError, TypeError):
            pass
        config.confirmation_message = request.POST.get('confirmation_message', config.confirmation_message)
        config.cancellation_message = request.POST.get('cancellation_message', config.cancellation_message)
        config.save()

        # Rebuild weekly schedule
        WeeklyAvailability.objects.filter(config=config).delete()
        for day in range(7):
            starts = request.POST.getlist(f'day_{day}_start')
            ends = request.POST.getlist(f'day_{day}_end')
            active_flags = request.POST.getlist(f'day_{day}_active')
            for i, (s, e) in enumerate(zip(starts, ends)):
                if s and e:
                    WeeklyAvailability.objects.create(
                        config=config,
                        day_of_week=day,
                        start_time=s,
                        end_time=e,
                        is_active=str(i) in active_flags,
                    )

        return redirect('/panel/citas/configuracion/?saved=1')


class WeeklyScheduleAddView(LoginRequiredMixin, View):
    """Agrega un bloque horario extra a un día de la semana."""
    login_url = '/admin/login/'

    def post(self, request):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        data = json.loads(request.body)
        day = int(data.get('day', 0))
        start = data.get('start', '')
        end = data.get('end', '')
        if not start or not end:
            return JsonResponse({'ok': False, 'error': 'Faltan horarios.'}, status=400)
        s = WeeklyAvailability.objects.create(
            config=config, day_of_week=day, start_time=start, end_time=end
        )
        return JsonResponse({'ok': True, 'id': s.id})


class WeeklyScheduleDeleteView(LoginRequiredMixin, View):
    login_url = '/admin/login/'

    def post(self, request, schedule_id):
        business = Business.objects.filter(is_active=True).first()
        config = _get_config(business)
        s = get_object_or_404(WeeklyAvailability, pk=schedule_id, config=config)
        s.delete()
        return JsonResponse({'ok': True})
