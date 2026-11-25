from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiKey, hash_key


class ApiKeyAuthentication(BaseAuthentication):
    """Authorization: Bearer qcrm_…  — la clave actúa con los permisos de su usuario."""

    keyword = "Bearer"

    def authenticate_header(self, request):
        return self.keyword

    def authenticate(self, request):
        parts = get_authorization_header(request).decode("latin-1").split()
        if len(parts) != 2 or parts[0] != self.keyword or not parts[1].startswith("qcrm_"):
            return None
        key = ApiKey.objects.select_related("user", "user__role").filter(key_hash=hash_key(parts[1]), revoked_at__isnull=True).first()
        if not key or not key.user.is_active:
            raise AuthenticationFailed("Clave de API no válida o revocada.")
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key.user, key
