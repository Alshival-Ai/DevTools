import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from dashboard.resources_store import get_user_alert_filter_prompt, update_user_alert_filter_prompt


class AlertFilterPromptStoreTests(TestCase):
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

    def test_alert_filter_prompt_defaults_to_empty(self):
        user = self._create_user("alert_pref_default")
        row = get_user_alert_filter_prompt(user)
        self.assertEqual(str(row.get("prompt") or ""), "")

    def test_alert_filter_prompt_replace_append_clear(self):
        user = self._create_user("alert_pref_update")

        replaced = update_user_alert_filter_prompt(
            user,
            prompt="Do not send email alerts for low priority logs.",
            mode="replace",
        )
        self.assertIn("low priority logs", str(replaced.get("prompt") or ""))

        appended = update_user_alert_filter_prompt(
            user,
            prompt="Also suppress repetitive ping failure alerts overnight.",
            mode="append",
        )
        prompt_text = str(appended.get("prompt") or "")
        self.assertIn("low priority logs", prompt_text)
        self.assertIn("repetitive ping failure alerts", prompt_text)

        cleared = update_user_alert_filter_prompt(user, mode="clear")
        self.assertEqual(str(cleared.get("prompt") or ""), "")
