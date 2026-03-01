import io
import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from dashboard.resources_store import _user_owner_dir


class MigrateUserHomeDataCommandTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._override = override_settings(
            USER_DATA_ROOT=str(Path(cls._tmp.name) / "user_data"),
            TEAM_DATA_ROOT=str(Path(cls._tmp.name) / "team_data"),
            GLOBAL_DATA_ROOT=str(Path(cls._tmp.name) / "global_data"),
        )
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._override.disable()
        finally:
            cls._tmp.cleanup()
        super().tearDownClass()

    def _create_user(self, username: str):
        User = get_user_model()
        return User.objects.create_user(
            username=username,
            password="pass1234",
            email=f"{username}@example.com",
        )

    def _create_asana_cache_db(self, path: Path, rows: list[tuple[str, str]]):
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asana_task_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    fetched_at_epoch INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            for cache_key, payload_json in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO asana_task_cache (
                        cache_key,
                        payload_json,
                        fetched_at_epoch,
                        updated_at
                    ) VALUES (?, ?, 0, datetime('now'))
                    """,
                    (cache_key, payload_json),
                )
            conn.commit()
        finally:
            conn.close()

    def test_command_migrates_legacy_files_and_prunes_empty_dirs(self):
        user = self._create_user("migrate_target")
        owner_dir = _user_owner_dir(user)
        legacy_member = owner_dir / "member.db"
        legacy_member.write_text("legacy-member", encoding="utf-8")

        legacy_knowledge = owner_dir / "knowledge.db"
        legacy_knowledge.mkdir(parents=True, exist_ok=True)
        (legacy_knowledge / "marker.txt").write_text("legacy-kb", encoding="utf-8")

        legacy_resource = owner_dir / "resources" / "abc-123"
        legacy_resource.mkdir(parents=True, exist_ok=True)
        (legacy_resource / "resource.txt").write_text("legacy-resource", encoding="utf-8")

        empty_legacy_dir = owner_dir / "stale_empty_dir"
        empty_legacy_dir.mkdir(parents=True, exist_ok=True)

        out = io.StringIO()
        call_command("migrate_user_home_data", stdout=out)
        output = out.getvalue()
        self.assertIn("dry_run=False", output)

        app_data_dir = owner_dir / "home" / ".alshival"
        self.assertTrue((app_data_dir / "member.db").exists())
        self.assertTrue((app_data_dir / "knowledge.db" / "marker.txt").exists())
        self.assertTrue((app_data_dir / "resources" / "abc-123" / "resource.txt").exists())

        self.assertFalse(legacy_member.exists())
        self.assertFalse(legacy_knowledge.exists())
        self.assertFalse((owner_dir / "resources").exists())
        self.assertFalse(empty_legacy_dir.exists())

    def test_command_dry_run_does_not_mutate_paths(self):
        user = self._create_user("dry_run_target")
        owner_dir = _user_owner_dir(user)
        legacy_member = owner_dir / "member.db"
        legacy_member.write_text("legacy-member", encoding="utf-8")

        out = io.StringIO()
        call_command("migrate_user_home_data", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("dry_run=True", output)

        app_data_member = owner_dir / "home" / ".alshival" / "member.db"
        self.assertTrue(legacy_member.exists())
        self.assertFalse(app_data_member.exists())

    def test_command_username_filter_only_migrates_targeted_user(self):
        first = self._create_user("filter_first")
        second = self._create_user("filter_second")

        first_owner = _user_owner_dir(first)
        second_owner = _user_owner_dir(second)
        (first_owner / "member.db").write_text("first", encoding="utf-8")
        (second_owner / "member.db").write_text("second", encoding="utf-8")

        out = io.StringIO()
        call_command(
            "migrate_user_home_data",
            "--username",
            "filter_first",
            "--username",
            "missing_user",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("missing_users=1", output)

        self.assertTrue((first_owner / "home" / ".alshival" / "member.db").exists())
        self.assertFalse((first_owner / "member.db").exists())
        self.assertTrue((second_owner / "member.db").exists())
        self.assertFalse((second_owner / "home" / ".alshival" / "member.db").exists())

    def test_finalize_removes_only_mirrored_legacy_paths(self):
        user = self._create_user("finalize_target")
        owner_dir = _user_owner_dir(user)
        app_data_dir = owner_dir / "home" / ".alshival"
        app_data_dir.mkdir(parents=True, exist_ok=True)

        legacy_member = owner_dir / "member.db"
        target_member = app_data_dir / "member.db"
        legacy_member.write_text("same-member", encoding="utf-8")
        target_member.write_text("same-member", encoding="utf-8")

        legacy_resource_file = owner_dir / "resources" / "abc" / "resource.txt"
        target_resource_file = app_data_dir / "resources" / "abc" / "resource.txt"
        legacy_resource_file.parent.mkdir(parents=True, exist_ok=True)
        target_resource_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_resource_file.write_text("same-resource", encoding="utf-8")
        target_resource_file.write_text("same-resource", encoding="utf-8")

        out = io.StringIO()
        call_command("migrate_user_home_data", "--finalize", "--username", "finalize_target", stdout=out)
        output = out.getvalue()
        self.assertIn("finalize=True", output)
        self.assertIn("finalize_conflicts=0", output)

        self.assertFalse(legacy_member.exists())
        self.assertFalse((owner_dir / "resources").exists())
        self.assertTrue(target_member.exists())
        self.assertTrue(target_resource_file.exists())

    def test_finalize_keeps_conflicting_legacy_paths(self):
        user = self._create_user("finalize_conflict")
        owner_dir = _user_owner_dir(user)
        app_data_dir = owner_dir / "home" / ".alshival"
        app_data_dir.mkdir(parents=True, exist_ok=True)

        legacy_member = owner_dir / "member.db"
        target_member = app_data_dir / "member.db"
        legacy_member.write_text("legacy-member", encoding="utf-8")
        target_member.write_text("target-member", encoding="utf-8")

        out = io.StringIO()
        call_command("migrate_user_home_data", "--finalize", "--username", "finalize_conflict", stdout=out)
        output = out.getvalue()
        self.assertIn("finalize=True", output)
        self.assertIn("finalize_conflicts=1", output)

        self.assertTrue(legacy_member.exists())
        self.assertTrue(target_member.exists())

    def test_finalize_merges_member_db_rows_and_removes_legacy(self):
        user = self._create_user("finalize_member_merge")
        owner_dir = _user_owner_dir(user)
        app_data_dir = owner_dir / "home" / ".alshival"
        app_data_dir.mkdir(parents=True, exist_ok=True)

        legacy_member = owner_dir / "member.db"
        target_member = app_data_dir / "member.db"
        self._create_asana_cache_db(target_member, [("task-a", '{"x":1}')])
        self._create_asana_cache_db(legacy_member, [("task-b", '{"x":2}')])

        out = io.StringIO()
        call_command("migrate_user_home_data", "--finalize", "--username", "finalize_member_merge", stdout=out)
        output = out.getvalue()
        self.assertIn("finalize=True", output)
        self.assertIn("merged_member_rows=1", output)
        self.assertIn("finalize_conflicts=0", output)

        self.assertFalse(legacy_member.exists())
        conn = sqlite3.connect(target_member)
        try:
            rows = conn.execute(
                "SELECT cache_key FROM asana_task_cache ORDER BY cache_key"
            ).fetchall()
            self.assertEqual([row[0] for row in rows], ["task-a", "task-b"])
        finally:
            conn.close()

    def test_finalize_keeps_member_db_when_rows_conflict_on_primary_key(self):
        user = self._create_user("finalize_member_pk_conflict")
        owner_dir = _user_owner_dir(user)
        app_data_dir = owner_dir / "home" / ".alshival"
        app_data_dir.mkdir(parents=True, exist_ok=True)

        legacy_member = owner_dir / "member.db"
        target_member = app_data_dir / "member.db"
        self._create_asana_cache_db(target_member, [("task-a", '{"x":"target"}')])
        self._create_asana_cache_db(legacy_member, [("task-a", '{"x":"legacy"}')])

        out = io.StringIO()
        call_command("migrate_user_home_data", "--finalize", "--username", "finalize_member_pk_conflict", stdout=out)
        output = out.getvalue()
        self.assertIn("finalize=True", output)
        self.assertIn("finalize_conflicts=1", output)

        self.assertTrue(legacy_member.exists())
        conn = sqlite3.connect(target_member)
        try:
            payload = conn.execute(
                "SELECT payload_json FROM asana_task_cache WHERE cache_key = ?",
                ("task-a",),
            ).fetchone()[0]
            self.assertEqual(payload, '{"x":"target"}')
        finally:
            conn.close()
