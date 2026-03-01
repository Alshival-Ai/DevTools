from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from dashboard.support_inbox import poll_support_inbox_once, run_support_inbox_email_agent_once
from dashboard.setup_state import get_setup_state


class Command(BaseCommand):
    help = "Poll Microsoft support inbox and ingest new messages into SQL + Chroma support_inbox collection."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=60,
            help="Polling interval in seconds (default: 60).",
        )
        parser.add_argument(
            "--initial-lookback-minutes",
            type=int,
            default=60,
            help="First-run lookback window when no sync cursor exists (default: 60).",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=20,
            help="Maximum Graph pages to read per poll (default: 20).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single poll cycle and exit.",
        )
        parser.add_argument(
            "--agent-limit",
            type=int,
            default=10,
            help="Maximum pending inbox emails processed by the AI agent per cycle (default: 10).",
        )

    def handle(self, *args, **options):
        interval = max(15, int(options.get("interval_seconds") or 60))
        lookback = max(5, int(options.get("initial_lookback_minutes") or 60))
        max_pages = max(1, int(options.get("max_pages") or 20))
        run_once = bool(options.get("once"))
        agent_limit = max(1, int(options.get("agent_limit") or 10))

        while True:
            started = time.monotonic()
            try:
                setup = get_setup_state()
                if setup is None or not bool(getattr(setup, "support_inbox_monitoring_enabled", False)):
                    self.stdout.write(
                        "support_inbox monitoring disabled in Alshival Admin; skipping poll cycle."
                    )
                    if run_once:
                        break
                    elapsed = time.monotonic() - started
                    sleep_for = max(0.0, interval - elapsed)
                    time.sleep(sleep_for)
                    continue
                result = poll_support_inbox_once(
                    initial_lookback_minutes=lookback,
                    max_pages=max_pages,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "support_inbox mailbox={mailbox} since={since} ingested={ingested_messages} kb_upserted={knowledge_upserted}".format(
                            **result
                        )
                    )
                )
                agent_result = run_support_inbox_email_agent_once(limit=agent_limit)
                self.stdout.write(
                    self.style.SUCCESS(
                        "support_inbox_agent mailbox={mailbox} status={status} processed={processed} replied={replied} skipped={skipped} errors={errors}".format(
                            mailbox=str(agent_result.get("mailbox") or result.get("mailbox") or ""),
                            status=str(agent_result.get("status") or ""),
                            processed=int(agent_result.get("processed") or 0),
                            replied=int(agent_result.get("replied") or 0),
                            skipped=int(agent_result.get("skipped") or 0),
                            errors=int(agent_result.get("errors") or 0),
                        )
                    )
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"support_inbox poll failed: {exc}"))
                if run_once:
                    raise

            if run_once:
                break

            elapsed = time.monotonic() - started
            sleep_for = max(0.0, interval - elapsed)
            time.sleep(sleep_for)
