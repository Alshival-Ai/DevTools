import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from dashboard.resources_store import add_resource, get_resource
from dashboard.setup_state import get_or_create_setup_state


class ResourceGithubSyncOnSaveTests(TestCase):
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
            is_staff=True,
        )

    def setUp(self):
        setup_state = get_or_create_setup_state()
        if setup_state is not None:
            setup_state.is_completed = True
            setup_state.save(update_fields=["is_completed", "updated_at"])

    def test_add_resource_triggers_github_sync_when_repository_attached(self):
        user = self._create_user("resource-github-add-user")
        self.client.force_login(user)

        with patch(
            "dashboard.views.sync_resource_wiki_with_github",
            return_value={"ok": True, "code": "ok", "pull": {}, "push": {}, "repo_docs": {}},
        ) as sync_mock:
            response = self.client.post(
                reverse("add_resource_item"),
                data={
                    "name": "Repo Resource",
                    "resource_type": "service",
                    "target": "repo-service-target",
                    "github_repositories": ["octocat/hello-world"],
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(sync_mock.called)
        kwargs = sync_mock.call_args.kwargs
        self.assertTrue(bool(kwargs.get("pull_remote")))
        self.assertFalse(bool(kwargs.get("push_changes")))
        self.assertTrue(bool(kwargs.get("reindex_resource_kb")))
        self.assertEqual(str(kwargs.get("reindex_check_method") or ""), "github_repo_sync")
        self.assertTrue(bool(kwargs.get("sync_repo_documents")))

    def test_edit_resource_triggers_github_sync_when_repository_attached(self):
        user = self._create_user("resource-github-edit-user")
        resource_id = add_resource(
            user,
            name="Editable Resource",
            resource_type="service",
            target="editable-target",
            notes="",
            resource_metadata={},
        )
        resource = get_resource(user, resource_id)
        self.assertIsNotNone(resource)

        self.client.force_login(user)
        with patch(
            "dashboard.views.sync_resource_wiki_with_github",
            return_value={"ok": True, "code": "ok", "pull": {}, "push": {}, "repo_docs": {}},
        ) as sync_mock:
            response = self.client.post(
                reverse("edit_resource_item", kwargs={"resource_id": int(resource_id)}),
                data={
                    "name": "Editable Resource",
                    "resource_type": "service",
                    "target": "editable-target",
                    "github_repositories": ["octocat/hello-world"],
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(sync_mock.called)
        kwargs = sync_mock.call_args.kwargs
        self.assertTrue(bool(kwargs.get("pull_remote")))
        self.assertFalse(bool(kwargs.get("push_changes")))
        self.assertTrue(bool(kwargs.get("reindex_resource_kb")))
        self.assertEqual(str(kwargs.get("reindex_check_method") or ""), "github_repo_sync")
        self.assertTrue(bool(kwargs.get("sync_repo_documents")))
