import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from dashboard.models import ResourcePackageOwner
from dashboard.resources_store import add_resource, list_resources
from dashboard.startup import (
    DEFAULT_GLOBAL_RESOURCE_GITHUB_REPOSITORIES,
    DEFAULT_GLOBAL_RESOURCE_NAME,
    _ensure_default_global_resource_repo_links,
    ensure_default_global_resource,
)


class StartupDefaultGlobalResourceTests(TestCase):
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

    def _create_superuser(self, username: str):
        User = get_user_model()
        return User.objects.create_superuser(
            username=username,
            email=f"{username}@example.com",
            password="pass1234",
        )

    def test_creates_default_global_resource_once(self):
        owner = self._create_superuser("startup_admin")

        created = ensure_default_global_resource()
        self.assertTrue(created)

        owner_resources = list_resources(owner)
        matches = [
            item for item in owner_resources
            if str(item.name or "").strip() == DEFAULT_GLOBAL_RESOURCE_NAME
        ]
        self.assertEqual(len(matches), 1)
        created_resource = matches[0]
        self.assertEqual(created_resource.access_scope, "global")
        metadata = created_resource.resource_metadata if isinstance(created_resource.resource_metadata, dict) else {}
        repositories = metadata.get("github_repositories") if isinstance(metadata.get("github_repositories"), list) else []
        self.assertIn(DEFAULT_GLOBAL_RESOURCE_GITHUB_REPOSITORIES[0], repositories)

        owner_row = ResourcePackageOwner.objects.filter(
            resource_uuid=created_resource.resource_uuid
        ).first()
        self.assertIsNotNone(owner_row)
        self.assertEqual(owner_row.owner_scope, ResourcePackageOwner.OWNER_SCOPE_GLOBAL)

        created_again = ensure_default_global_resource()
        self.assertFalse(created_again)
        owner_resources_after = list_resources(owner)
        matches_after = [
            item for item in owner_resources_after
            if str(item.name or "").strip() == DEFAULT_GLOBAL_RESOURCE_NAME
        ]
        self.assertEqual(len(matches_after), 1)

    def test_uses_existing_named_global_resource_without_creating_duplicate(self):
        owner_primary = self._create_superuser("startup_admin_primary")
        owner_secondary = self._create_superuser("startup_admin_secondary")

        add_resource(
            owner_secondary,
            name=DEFAULT_GLOBAL_RESOURCE_NAME,
            resource_type="service",
            target="already-present",
            notes="",
            access_scope="global",
            team_names=[],
        )

        created = ensure_default_global_resource()
        self.assertFalse(created)

        primary_matches = [
            item for item in list_resources(owner_primary)
            if str(item.name or "").strip() == DEFAULT_GLOBAL_RESOURCE_NAME
        ]
        secondary_matches = [
            item for item in list_resources(owner_secondary)
            if str(item.name or "").strip() == DEFAULT_GLOBAL_RESOURCE_NAME
        ]
        self.assertEqual(len(primary_matches), 0)
        self.assertEqual(len(secondary_matches), 1)

    def test_no_superuser_is_noop(self):
        User = get_user_model()
        User.objects.create_user(
            username="regular_member",
            email="regular@example.com",
            password="pass1234",
        )

        created = ensure_default_global_resource()
        self.assertFalse(created)

    def test_existing_default_resource_gets_sdk_github_repo_link(self):
        owner = self._create_superuser("startup_admin_repo_link")
        resource_id = add_resource(
            owner,
            name=DEFAULT_GLOBAL_RESOURCE_NAME,
            resource_type="service",
            target="existing-global-resource",
            notes="",
            resource_metadata={},
            access_scope="global",
            team_names=[],
        )
        resource = list_resources(owner)[0]
        self.assertEqual(int(resource.id), int(resource_id))
        metadata = resource.resource_metadata if isinstance(resource.resource_metadata, dict) else {}
        repositories = metadata.get("github_repositories") if isinstance(metadata.get("github_repositories"), list) else []
        self.assertEqual(repositories, [])

        updated = _ensure_default_global_resource_repo_links(owner, resource.resource_uuid)
        self.assertTrue(updated)

        refreshed = next(
            item
            for item in list_resources(owner)
            if str(item.resource_uuid or "").strip() == str(resource.resource_uuid or "").strip()
        )
        refreshed_metadata = refreshed.resource_metadata if isinstance(refreshed.resource_metadata, dict) else {}
        refreshed_repositories = (
            refreshed_metadata.get("github_repositories")
            if isinstance(refreshed_metadata.get("github_repositories"), list)
            else []
        )
        self.assertIn(DEFAULT_GLOBAL_RESOURCE_GITHUB_REPOSITORIES[0], refreshed_repositories)
