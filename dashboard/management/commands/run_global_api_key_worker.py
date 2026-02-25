from __future__ import annotations

import random
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.utils import OperationalError, ProgrammingError

from dashboard.global_api_key_store import create_global_team_api_key


class Command(BaseCommand):
    help = "Generate and rotate a global API key on a fixed interval."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=3600,
            help="Seconds between key rotations (default: 3600).",
        )
        parser.add_argument(
            "--run-once",
            action="store_true",
            help="Generate one key and exit.",
        )
        parser.add_argument(
            "--jitter-seconds",
            type=int,
            default=60,
            help="Random jitter (+/- seconds) between cycles (default: 60).",
        )
        parser.add_argument(
            "--key-name",
            type=str,
            default="Global Worker API Key",
            help="Display name for generated keys.",
        )

    def _resolve_actor(self):
        User = get_user_model()
        try:
            actor = User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
            if actor:
                return actor
            return User.objects.filter(is_active=True).order_by("id").first()
        except (OperationalError, ProgrammingError) as exc:
            self.stderr.write(f"[global-key-worker] database not ready yet; skipping cycle ({exc})")
            return None

    def _run_cycle(self, key_name: str) -> bool:
        actor = self._resolve_actor()
        if actor is None:
            self.stderr.write("[global-key-worker] no active user found; skipping")
            return False
        key_id, _raw_key = create_global_team_api_key(
            user=actor,
            name=key_name,
            team_name="",
        )
        self.stdout.write(
            f"[global-key-worker] rotated global key id={key_id} actor={actor.id} "
            "previous_active_keys_expire_in=3600s"
        )
        return True

    def handle(self, *args, **options):
        interval_seconds = max(60, int(options["interval_seconds"]))
        run_once = bool(options["run_once"])
        jitter_seconds = max(0, int(options["jitter_seconds"]))
        key_name = (options.get("key_name") or "Global Worker API Key").strip() or "Global Worker API Key"

        self.stdout.write(
            "[global-key-worker] started "
            f"interval={interval_seconds}s jitter=±{jitter_seconds}s run_once={run_once}"
        )

        while True:
            started = time.time()
            self._run_cycle(key_name)
            elapsed = time.time() - started
            if run_once:
                return
            jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
            next_interval = max(60.0, float(interval_seconds) + jitter)
            sleep_for = max(0.0, next_interval - elapsed)
            time.sleep(sleep_for)
