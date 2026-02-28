from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from dashboard.resources_store import create_reminder, delete_reminder, list_due_reminders, update_reminder
from dashboard.views import _tool_set_reminder_for_actor


class ReminderStoreTests(TestCase):
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

    def test_reminder_store_create_due_update_and_soft_delete(self):
        owner = self._create_user("reminder_owner")
        due_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        reminder = create_reminder(
            owner,
            title="Rotate prod keys",
            remind_at=due_time,
            message="Rotate and verify app secrets.",
            recipients=["reminder_owner"],
            channels={"APP": True, "SMS": False, "EMAIL": False},
            metadata={"resource_uuid": "be01be12-6461-4d8f-a4fc-7dfaa47e091a"},
            created_by_user_id=int(owner.id),
            created_by_username=owner.username,
        )
        self.assertEqual(reminder["status"], "scheduled")

        due = list_due_reminders(owner, now_dt=datetime.now(timezone.utc), limit=10)
        self.assertEqual(len(due), 1)
        self.assertEqual(int(due[0]["id"]), int(reminder["id"]))

        updated = update_reminder(owner, int(reminder["id"]), status="sent")
        self.assertEqual(updated["status"], "sent")
        self.assertTrue(str(updated.get("sent_at") or "").strip())

        canceled = delete_reminder(owner, int(reminder["id"]), hard_delete=False)
        self.assertEqual(canceled["status"], "canceled")


class ReminderRecipientValidationTests(TestCase):
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

    def test_set_reminder_blocks_outside_team_scope(self):
        owner = self._create_user("owner")
        teammate = self._create_user("teammate")
        outsider = self._create_user("outsider")

        team = Group.objects.create(name="Ops")
        owner.groups.add(team)
        teammate.groups.add(team)

        remind_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        blocked = _tool_set_reminder_for_actor(
            owner,
            {
                "title": "Follow up",
                "remind_at": remind_at,
                "message": "Share deploy report",
                "recipients": ["teammate", "outsider"],
                "channels": {"APP": True, "SMS": True, "EMAIL": False},
            },
        )
        self.assertFalse(bool(blocked.get("ok")))
        invalid = blocked.get("invalid_recipients") if isinstance(blocked.get("invalid_recipients"), list) else []
        self.assertTrue(any(str(item.get("username") or "") == "outsider" for item in invalid))

        allowed = _tool_set_reminder_for_actor(
            owner,
            {
                "title": "Follow up",
                "remind_at": remind_at,
                "message": "Share deploy report",
                "recipients": ["teammate"],
                "channels": {"APP": True, "SMS": False, "EMAIL": False},
            },
        )
        self.assertTrue(bool(allowed.get("ok")))
        reminder = allowed.get("reminder") if isinstance(allowed.get("reminder"), dict) else {}
        recipients = reminder.get("recipients") if isinstance(reminder.get("recipients"), list) else []
        self.assertEqual(recipients, ["teammate"])
