from __future__ import annotations

import random
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.utils import OperationalError, ProgrammingError

from dashboard.resources_store import rotate_internal_account_api_key


class Command(BaseCommand):
    help = "Generate and rotate internal account API keys for active users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=10800,
            help="Seconds between rotations (default: 10800 / 3h).",
        )
        parser.add_argument(
            "--run-once",
            action="store_true",
            help="Run a single cycle and exit.",
        )
        parser.add_argument(
            "--jitter-seconds",
            type=int,
            default=120,
            help="Random jitter (+/- seconds) between cycles (default: 120).",
        )
        parser.add_argument(
            "--key-name",
            type=str,
            default="Internal Worker API Key",
            help="Display name for generated user account keys.",
        )
        parser.add_argument(
            "--include-superusers",
            action="store_true",
            help="Also rotate internal keys for superusers.",
        )

    def _active_users(self, include_superusers: bool) -> list[object]:
        User = get_user_model()
        try:
            qs = User.objects.filter(is_active=True).order_by("id")
            if not include_superusers:
                qs = qs.filter(is_superuser=False)
            return list(qs)
        except (OperationalError, ProgrammingError) as exc:
            self.stderr.write(f"[user-key-worker] database not ready yet; skipping cycle ({exc})")
            return []

    def _run_cycle(self, *, key_name: str, include_superusers: bool) -> tuple[int, int]:
        users = self._active_users(include_superusers)
        if not users:
            self.stderr.write("[user-key-worker] no eligible active users found; skipping")
            return 0, 0

        rotated = 0
        errors = 0
        for user in users:
            try:
                key_id = rotate_internal_account_api_key(user, key_name)
                rotated += 1
                self.stdout.write(
                    f"[user-key-worker] rotated user={int(user.id)} username={user.get_username()} key_id={key_id}"
                )
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"[user-key-worker] failed user={int(getattr(user, 'id', 0) or 0)} "
                    f"username={user.get_username()}: {exc}"
                )
        return rotated, errors

    def handle(self, *args, **options):
        interval_seconds = max(300, int(options["interval_seconds"]))
        run_once = bool(options["run_once"])
        jitter_seconds = max(0, int(options["jitter_seconds"]))
        include_superusers = bool(options["include_superusers"])
        key_name = (options.get("key_name") or "Internal Worker API Key").strip() or "Internal Worker API Key"

        self.stdout.write(
            "[user-key-worker] started "
            f"interval={interval_seconds}s jitter=±{jitter_seconds}s run_once={run_once} "
            f"include_superusers={include_superusers}"
        )

        while True:
            started = time.time()
            rotated, errors = self._run_cycle(
                key_name=key_name,
                include_superusers=include_superusers,
            )
            elapsed = time.time() - started
            self.stdout.write(
                f"[user-key-worker] cycle complete rotated={rotated} errors={errors} elapsed={elapsed:.1f}s"
            )
            if run_once:
                return
            jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
            next_interval = max(300.0, float(interval_seconds) + jitter)
            sleep_for = max(0.0, next_interval - elapsed)
            time.sleep(sleep_for)
