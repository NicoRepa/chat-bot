def panel_permissions(request):
    """
    Agrega variables de permisos al contexto global de todas las plantillas.
    """
    if not request.user.is_authenticated:
        return {}

    user = request.user

    # Feature flag: módulo de citas
    from apps.core.models import Business
    business = Business.objects.filter(is_active=True).only('feature_appointments').first()
    feature_appointments = getattr(business, 'feature_appointments', False) if business else False

    # Superuser siempre tiene todos los permisos
    if user.is_superuser:
        return {
            'can_manage_agents': True,
            'is_admin': True,
            'feature_appointments': feature_appointments,
        }

    # Para usuarios normales, verificar perfil
    try:
        profile = user.profile
        is_admin = profile.is_admin
        is_supervisor = profile.is_supervisor
    except Exception:
        is_admin = False
        is_supervisor = False

    return {
        'can_manage_agents': is_admin or is_supervisor,
        'is_admin': is_admin,
        'feature_appointments': feature_appointments,
    }
