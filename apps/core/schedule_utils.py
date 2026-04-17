"""
Utilidades para horarios de atención del negocio.
"""
from datetime import datetime
from django.utils import timezone

# Mapeo de nombres de días en español a weekday() de Python
DAY_MAP = {
    'lunes': 0,
    'martes': 1,
    'miércoles': 2,
    'miercoles': 2,
    'jueves': 3,
    'viernes': 4,
    'sábado': 5,
    'sabado': 5,
    'domingo': 6,
}

DEFAULT_SCHEDULE = [
    {"day": "lunes", "enabled": True, "start": "09:00", "end": "18:00"},
    {"day": "martes", "enabled": True, "start": "09:00", "end": "18:00"},
    {"day": "miércoles", "enabled": True, "start": "09:00", "end": "18:00"},
    {"day": "jueves", "enabled": True, "start": "09:00", "end": "18:00"},
    {"day": "viernes", "enabled": True, "start": "09:00", "end": "18:00"},
    {"day": "sábado", "enabled": False, "start": "", "end": ""},
    {"day": "domingo", "enabled": False, "start": "", "end": ""},
]

DAY_LABELS = {
    'lunes': 'Lunes',
    'martes': 'Martes',
    'miércoles': 'Miércoles',
    'jueves': 'Jueves',
    'viernes': 'Viernes',
    'sábado': 'Sábado',
    'domingo': 'Domingo',
}


def is_within_business_hours(config):
    """
    Verifica si el momento actual está dentro del horario de atención.
    Retorna (bool, str): (dentro_horario, mensaje_fuera_horario)
    """
    schedule = getattr(config, 'business_hours', None)
    if not schedule or not isinstance(schedule, list) or len(schedule) == 0:
        # Sin horario configurado = siempre abierto
        return True, ''

    now = timezone.localtime(timezone.now())
    current_weekday = now.weekday()  # 0=Monday ... 6=Sunday

    for entry in schedule:
        day_name = entry.get('day', '').lower()
        day_num = DAY_MAP.get(day_name)
        if day_num is None or day_num != current_weekday:
            continue

        if not entry.get('enabled', False):
            # Día deshabilitado
            out_msg = config.out_of_hours_message or (
                '⏰ Estamos fuera de horario de atención. '
                'La IA sigue activa para responder tus consultas, '
                'pero no hay agentes disponibles en este momento.'
            )
            return False, out_msg

        start_str = entry.get('start', '')
        end_str = entry.get('end', '')
        if not start_str or not end_str:
            return True, ''

        try:
            start_h, start_m = map(int, start_str.split(':'))
            end_h, end_m = map(int, end_str.split(':'))
            current_minutes = now.hour * 60 + now.minute
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if start_minutes <= current_minutes <= end_minutes:
                return True, ''
            else:
                out_msg = config.out_of_hours_message or (
                    f'⏰ Nuestro horario de atención hoy es de '
                    f'{start_str} a {end_str}. '
                    f'La IA sigue activa para responder tus consultas, '
                    f'pero no hay agentes disponibles en este momento.'
                )
                return False, out_msg
        except (ValueError, AttributeError):
            return True, ''

    # Day not in schedule = open
    return True, ''


def get_schedule_display(config):
    """
    Devuelve el horario formateado para mostrar al cliente.
    """
    schedule = getattr(config, 'business_hours', None)
    if not schedule or not isinstance(schedule, list):
        return 'No configurado'

    lines = []
    for entry in schedule:
        day = DAY_LABELS.get(entry.get('day', '').lower(), entry.get('day', ''))
        if entry.get('enabled'):
            lines.append(f"📅 {day}: {entry.get('start', '?')} - {entry.get('end', '?')}")
        else:
            lines.append(f"📅 {day}: Cerrado")
    return '\n'.join(lines)
