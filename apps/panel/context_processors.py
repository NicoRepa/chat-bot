def panel_permissions(request):
    """
    Agrega variables de permisos al contexto global de todas las plantillas.
    Esto evita tener que pasar 'can_manage_agents' manualmente en cada vista.
    """
    if not request.user.is_authenticated:
        return {}

    user = request.user

    # Superuser siempre tiene todos los permisos
    if user.is_superuser:
        return {
            'can_manage_agents': True,
            'is_admin': True,
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
    }
