import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from dashboard.global_ssh_store import add_global_ssh_credential
from dashboard.resources_store import (
    add_ssh_credential,
    get_ssh_credential_private_key,
)
from dashboard.setup_state import get_or_create_setup_state


class SSHKeyDownloadTests(TestCase):
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

    def _create_user(self, username: str, *, is_superuser: bool = False):
        User = get_user_model()
        return User.objects.create_user(
            username=username,
            password="pass1234",
            email=f"{username}@example.com",
            is_superuser=is_superuser,
            is_staff=True,
        )

    def setUp(self):
        setup_state = get_or_create_setup_state()
        if setup_state is not None:
            setup_state.is_completed = True
            setup_state.save(update_fields=["is_completed", "updated_at"])

    def test_add_ssh_credential_normalizes_private_key_text(self):
        user = self._create_user("normalize-key-user")
        credential_id = add_ssh_credential(
            user=user,
            name="normalize-key",
            scope="account",
            team_names=[],
            private_key_text="line-a\r\nline-b\r\n",
        )
        resolved_key = get_ssh_credential_private_key(user, credential_id)
        self.assertEqual(resolved_key, "line-a\nline-b\n")

    def test_download_account_ssh_private_key(self):
        user = self._create_user("download-account-key-user")
        credential_id = add_ssh_credential(
            user=user,
            name="prod-vm-key",
            scope="account",
            team_names=[],
            private_key_text="private-key-body",
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("download_ssh_credential_item", kwargs={"credential_id": credential_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Alshival-File-Mode"], "0600")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn(
            'attachment; filename="prod-vm-key.key"',
            response["Content-Disposition"],
        )
        self.assertEqual(response.content.decode("utf-8"), "private-key-body\n")

    def test_download_global_ssh_private_key_requires_superuser(self):
        admin = self._create_user("download-global-admin", is_superuser=True)
        non_admin = self._create_user("download-global-user")
        credential_id = add_global_ssh_credential(
            user=admin,
            name="global-prod-key",
            team_name="",
            private_key_text="global-key-body",
        )

        self.client.force_login(non_admin)
        denied_response = self.client.post(
            reverse("download_global_ssh_credential_item", kwargs={"credential_id": credential_id})
        )
        self.assertEqual(denied_response.status_code, 302)
        self.assertEqual(denied_response.url, reverse("resources"))

        self.client.force_login(admin)
        allowed_response = self.client.post(
            reverse("download_global_ssh_credential_item", kwargs={"credential_id": credential_id})
        )
        self.assertEqual(allowed_response.status_code, 200)
        self.assertIn(
            'attachment; filename="global-prod-key.key"',
            allowed_response["Content-Disposition"],
        )
        self.assertEqual(allowed_response["X-Alshival-File-Mode"], "0600")
        self.assertEqual(allowed_response.content.decode("utf-8"), "global-key-body\n")
