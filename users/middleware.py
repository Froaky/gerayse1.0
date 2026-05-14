from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve


class ForcePasswordChangeMiddleware:
    """Keep default-password users away from the app until they set their own password."""

    allowed_view_names = {
        "users:login",
        "users:logout",
        "users:password_change_required",
        "users:first_access",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and getattr(user, "must_change_password", False)
            and not self._is_allowed_path(request)
        ):
            return redirect("users:password_change_required")
        return self.get_response(request)

    def _is_allowed_path(self, request) -> bool:
        path = request.path_info
        static_url = "/" + settings.STATIC_URL.lstrip("/")
        media_url = "/" + settings.MEDIA_URL.lstrip("/")
        if path.startswith(static_url) or path.startswith(media_url):
            return True
        try:
            match = resolve(path)
        except Resolver404:
            return False
        return match.view_name in self.allowed_view_names
