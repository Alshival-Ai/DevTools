from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from allauth.socialaccount.models import SocialAccount

from dashboard.calendar_sync_service import refresh_calendar_cache_for_user


class Command(BaseCommand):
    help = "Refresh user calendar cache in member.db (Asana now; provider surface is extensible)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            default="asana",
            choices=["asana", "outlook", "all"],
            help="Calendar provider to refresh.",
        )
        parser.add_argument(
            "--username",
            default="",
            help="Refresh a single username. If omitted, refresh all users connected to the provider.",
        )

    def handle(self, *args, **options):
        provider = str(options.get("provider") or "asana").strip().lower() or "asana"
        username = str(options.get("username") or "").strip()
        user_model = get_user_model()

        queryset = user_model.objects.filter(is_active=True)
        if username:
            queryset = queryset.filter(username=username)
        else:
            provider_user_ids: set[int] = set()
            if provider in {"asana", "all"}:
                provider_user_ids.update(
                    int(value)
                    for value in SocialAccount.objects.filter(provider="asana").values_list("user_id", flat=True)
                )
            if provider in {"outlook", "all"}:
                provider_user_ids.update(
                    int(value)
                    for value in SocialAccount.objects.filter(provider="microsoft").values_list("user_id", flat=True)
                )
            if provider_user_ids:
                queryset = queryset.filter(id__in=sorted(provider_user_ids))
            else:
                queryset = queryset.none()

        total = 0
        refreshed = 0
        failed = 0

        for user in queryset.iterator():
            total += 1
            try:
                result = refresh_calendar_cache_for_user(
                    user,
                    provider=provider,
                    force=True,
                )
                asana_result = result.get("asana") if isinstance(result, dict) else {}
                outlook_result = result.get("outlook") if isinstance(result, dict) else {}

                provider_errors: list[str] = []
                if isinstance(asana_result, dict):
                    asana_error = str(asana_result.get("error") or "").strip()
                    if asana_error:
                        provider_errors.append(f"asana={asana_error}")
                if isinstance(outlook_result, dict):
                    outlook_error = str(outlook_result.get("error") or "").strip()
                    if outlook_error:
                        provider_errors.append(f"outlook={outlook_error}")

                if provider_errors:
                    failed += 1
                    self.stdout.write(
                        f"[calendar-cache] user={user.username} provider={provider} status=error error={'; '.join(provider_errors)}"
                    )
                else:
                    refreshed += 1
                    asana_cached = 0
                    outlook_cached = 0
                    if isinstance(asana_result, dict):
                        asana_cached = int(asana_result.get("cached_events") or 0)
                    if isinstance(outlook_result, dict):
                        outlook_cached = int(outlook_result.get("cached_events") or 0)
                    self.stdout.write(
                        f"[calendar-cache] user={user.username} provider={provider} status=ok asana_cached={asana_cached} outlook_cached={outlook_cached}"
                    )
            except Exception as exc:
                failed += 1
                self.stdout.write(
                    f"[calendar-cache] user={user.username} provider={provider} status=error error={exc}"
                )

        self.stdout.write(
            f"[calendar-cache] complete provider={provider} total={total} refreshed={refreshed} failed={failed}"
        )
