from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        from . import signals  # noqa: F401
        from .startup import run_startup_initializers_once_async

        run_startup_initializers_once_async()
