from __future__ import annotations

from django.core.management.base import BaseCommand

from dashboard.user_knowledge_store import sync_all_user_records


class Command(BaseCommand):
    help = "Sync all user records into global Chroma user_records collection."

    def handle(self, *args, **options):
        result = sync_all_user_records()
        self.stdout.write(
            "[user-records-sync] complete "
            f"total={int(result.get('total', 0))} "
            f"upserted={int(result.get('upserted', 0))} "
            f"deleted={int(result.get('deleted', 0))}"
        )
