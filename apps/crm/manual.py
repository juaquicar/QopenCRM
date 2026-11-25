"""Manual de usuario: perfil de permisos con el que se redacta y capturas que le corresponden.

El texto vive en templates/crm/manual.html y se adapta a los permisos reales del rol,
así que sigue siendo cierto aunque el superusuario cambie lo que puede hacer cada rol.
"""
FLAGS = ("can_delete", "can_export", "can_manage_catalog", "can_manage_pipeline", "can_manage_events", "can_manage_knowledge", "can_manage_incentives", "can_use_api")

SUPERUSER = {
    "code": "superusuario", "name": "Superusuario", "is_superuser": True, "sees_all": True, "edits_all": True,
    "can_see_reports": True, **{flag: True for flag in FLAGS},
}
NO_ROLE = {
    "code": "sin-rol", "name": "Sin rol", "is_superuser": False, "sees_all": False, "edits_all": False,
    "can_see_reports": False, **{flag: False for flag in FLAGS},
}


def profile_for_role(role):
    return {
        "code": role.code, "name": role.name, "is_superuser": False, "can_see_reports": False,
        "sees_all": role.view_scope == "all", "edits_all": role.edit_scope == "all",
        **{flag: getattr(role, flag) for flag in FLAGS},
    }


def profile_for_user(user):
    if user.is_superuser:
        return SUPERUSER
    return profile_for_role(user.role) if user.role_id else NO_ROLE


def shots(profile):
    """Capturas que cambian según lo que ve el perfil."""
    if profile["is_superuser"]:
        settings_shot = "ajustes-super"
    elif profile["can_manage_pipeline"]:
        settings_shot = "ajustes-etapas"
    elif profile["can_export"]:
        settings_shot = "ajustes-exportar"
    else:
        settings_shot = "ajustes-basico"
    scope = "all" if profile["sees_all"] else "own"
    return {"home": f"hoy-{scope}", "pipeline": f"pipeline-{scope}", "settings": settings_shot,
            "incentives": "incentivos-equipo" if profile["can_manage_incentives"] else "incentivos-propios"}
