import json
import tempfile
from pathlib import Path

import chromadb
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from dashboard.models import WikiPage
from dashboard.resources_store import add_resource, get_resource, get_resource_owner_context
from dashboard.setup_state import get_or_create_setup_state
from dashboard.views import _tool_resource_kb_for_actor, _upsert_resource_kb_after_wiki_mutation


class ResourceKbUnifiedAclTests(TestCase):
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

    def _create_team_resource(self):
        samuel = self._create_user("samuel")
        salvador = self._create_user("salvador")
        outsider = self._create_user("outsider")
        team = Group.objects.create(name="WardGPT")
        samuel.groups.add(team)
        salvador.groups.add(team)

        resource_id = add_resource(
            samuel,
            name="WardGPT Project",
            resource_type="service",
            target="wardgpt-target",
            notes="",
            access_scope="team",
            team_names=[team.name],
            resource_metadata={},
        )
        resource = get_resource(samuel, resource_id)
        self.assertIsNotNone(resource)
        return samuel, salvador, outsider, resource

    def test_team_member_can_query_resource_wiki_from_unified_kb(self):
        samuel, salvador, outsider, resource = self._create_team_resource()
        resource_uuid = str(resource.resource_uuid)

        WikiPage.objects.create(
            created_by=samuel,
            updated_by=samuel,
            scope=WikiPage.SCOPE_RESOURCE,
            resource_uuid=resource_uuid,
            path="home",
            title="WardGPT Wiki",
            body_markdown="Delegated team access content for WardGPT project wiki.",
            is_draft=False,
        )
        _upsert_resource_kb_after_wiki_mutation(actor=samuel, resource_uuid=resource_uuid)

        unified_path = Path(getattr(settings, "GLOBAL_DATA_ROOT")) / "knowledge.db"
        client = chromadb.PersistentClient(path=str(unified_path))
        collection = client.get_collection(name="knowledge")
        rows = collection.get(
            where={"resource_uuid": resource_uuid},
            include=["metadatas"],
            limit=300,
        )
        metadatas = rows.get("metadatas") or []
        sources = {str((item or {}).get("source") or "") for item in metadatas}
        self.assertIn("resource_wiki_page", sources)

        allowed = _tool_resource_kb_for_actor(
            salvador,
            {"resource_uuid": resource_uuid, "query": "delegated team access", "limit": 10},
        )
        self.assertTrue(bool(allowed.get("ok")))
        allowed_results = allowed.get("results") if isinstance(allowed.get("results"), list) else []
        self.assertGreater(len(allowed_results), 0)

        denied = _tool_resource_kb_for_actor(
            outsider,
            {"resource_uuid": resource_uuid, "query": "delegated team access", "limit": 10},
        )
        self.assertFalse(bool(denied.get("ok")))
        self.assertIn("cannot access resource", str(denied.get("error") or ""))

    def test_resource_repo_documents_are_queryable_from_resource_kb(self):
        samuel, salvador, _outsider, resource = self._create_team_resource()
        resource_uuid = str(resource.resource_uuid)
        owner_context = get_resource_owner_context(samuel, resource_uuid)
        resource_dir = Path(owner_context.get("resource_dir") or "")
        resource_dir.mkdir(parents=True, exist_ok=True)
        repo_payload = {
            "repository": "Alshival-Ai/WardGPT",
            "synced_at": "2026-03-30T00:00:00Z",
            "files": [
                {
                    "path": "README.md",
                    "title": "README.md",
                    "content": "Sentinel repo context phrase: WardGPT delegated knowledge.",
                    "truncated": False,
                    "size_chars": 56,
                }
            ],
        }
        (resource_dir / "repository_docs.json").write_text(
            json.dumps(repo_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        _upsert_resource_kb_after_wiki_mutation(actor=samuel, resource_uuid=resource_uuid)

        payload = _tool_resource_kb_for_actor(
            salvador,
            {"resource_uuid": resource_uuid, "query": "sentinel repo context phrase", "limit": 10},
        )
        self.assertTrue(bool(payload.get("ok")))
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        self.assertGreater(len(results), 0)
        repo_rows = [
            row for row in results
            if str(((row.get("metadata") or {}).get("source") or "")).strip() == "resource_repo_file"
        ]
        self.assertGreater(len(repo_rows), 0)
