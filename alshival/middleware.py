from django.conf import settings
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import redirect

from dashboard.request_context import clear_current_user, set_current_user
from dashboard.setup_state import is_setup_complete


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_user(getattr(request, "user", None))
        try:
            path = request.path
            setup_url = getattr(settings, "SETUP_URL", "/setup/")
            accounts_prefix = "/accounts/"

            static_prefix = f"/{settings.STATIC_URL.lstrip('/')}"
            if path.startswith(static_prefix):
                return self.get_response(request)
            if request.user.is_authenticated and not request.user.is_staff:
                logout(request)
                raise Http404("Not found")
            if path.startswith('/admin/'):
                return self.get_response(request)
            if path.startswith('/u/') and path.endswith('/logs/'):
                return self.get_response(request)
            if path.startswith('/twilio/sms') or path.startswith('/twilio/sms-group'):
                return self.get_response(request)

            if not is_setup_complete():
                if path.startswith(setup_url):
                    return self.get_response(request)
                return redirect(setup_url)

            if request.user.is_authenticated:
                return self.get_response(request)

            if path.startswith(accounts_prefix):
                return self.get_response(request)
            if path.startswith(settings.LOGIN_URL):
                return self.get_response(request)
            if path.startswith(setup_url):
                return redirect(f"{settings.LOGIN_URL}?next={path}")

            return redirect(f"{settings.LOGIN_URL}?next={path}")
        finally:
            clear_current_user()
