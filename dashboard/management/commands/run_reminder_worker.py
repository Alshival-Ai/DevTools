from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from dashboard.reminder_service import run_due_reminders


class Command(BaseCommand):
    help = "Dispatch due reminders from user member.db stores with channel-aware delivery."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=60,
            help="Polling interval in seconds (default: 60).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single reminder cycle and exit.",
        )
        parser.add_argument(
            "--per-user-limit",
            type=int,
            default=200,
            help="Maximum due reminders processed per user per cycle (default: 200).",
        )
        parser.add_argument(
            "--user",
            action="append",
            default=[],
            help="Optional username/email filter (repeatable).",
        )
        parser.add_argument(
            "--max-users",
            type=int,
            default=0,
            help="Optional max users to process each cycle (0 = all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run without external sends or reminder status mutations.",
        )

    def handle(self, *args, **options):
        interval = max(15, int(options.get("interval_seconds") or 60))
        run_once = bool(options.get("once"))
        per_user_limit = max(1, int(options.get("per_user_limit") or 200))
        dry_run = bool(options.get("dry_run"))
        user_filters = list(options.get("user") or [])
        max_users = max(0, int(options.get("max_users") or 0))

        self.stdout.write(
            "[reminder-worker] started "
            f"interval={interval}s once={run_once} dry_run={dry_run} "
            f"per_user_limit={per_user_limit} max_users={max_users or 'all'}"
        )

        while True:
            started = time.monotonic()
            try:
                totals = run_due_reminders(
                    user_filters=user_filters,
                    per_user_limit=per_user_limit,
                    dry_run=dry_run,
                    max_users=max_users,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "[reminder-worker] cycle users={users} due={due} sent={sent} error={error} skipped={skipped}".format(
                            **totals
                        )
                    )
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"[reminder-worker] cycle failed: {exc}"))
                if run_once:
                    raise

            if run_once:
                return

            elapsed = time.monotonic() - started
            sleep_for = max(0.0, interval - elapsed)
            time.sleep(sleep_for)
