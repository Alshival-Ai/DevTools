from types import SimpleNamespace
from unittest.mock import patch
import os

from django.test import SimpleTestCase

from dashboard.github_wiki_sync_service import (
    _resolve_access_token_from_users,
    sync_resource_wiki_with_github,
)


class GithubWikiSyncServiceTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        for key in (
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "ALSHIVAL_GITHUB_ACCESS_TOKEN",
            "ASK_GITHUB_MCP_ACCESS_TOKEN",
            "ALSHIVAL_GITHUB_WIKI_ALLOW_ANON",
        ):
            os.environ.pop(key, None)

    def _resource(self):
        return SimpleNamespace(
            resource_uuid="123e4567-e89b-12d3-a456-426614174000",
            name="My Resource",
            last_checked_at="",
            last_status="healthy",
            last_error="",
        )

    def _actor(self):
        return SimpleNamespace(
            id=42,
            username="sync_user",
            email="sync_user@example.com",
            is_active=True,
        )

    def test_sync_reindexes_resource_kb_when_enabled(self):
        actor = self._actor()
        resource = self._resource()
        with patch(
            "dashboard.github_wiki_sync_service.resource_github_repository_names",
            return_value=["octocat/hello-world"],
        ), patch(
            "dashboard.github_wiki_sync_service._resolve_access_token_from_users",
            return_value=(actor, "ghs_token", ""),
        ), patch(
            "dashboard.github_wiki_sync_service._github_repo_context",
            return_value=({"wiki_enabled": "1"}, ""),
        ), patch(
            "dashboard.github_wiki_sync_service._pull_remote_wiki_into_local",
            return_value={
                "remote_files": 1,
                "created": 1,
                "updated": 0,
                "unchanged": 0,
                "draft_skipped": 0,
                "errors": 0,
                "error": "",
            },
        ), patch(
            "dashboard.github_wiki_sync_service._reindex_resource_kb_after_sync",
            return_value=(True, ""),
        ) as reindex_mock:
            result = sync_resource_wiki_with_github(
                actor=actor,
                resource=resource,
                token_users=[actor],
                pull_remote=True,
                push_changes=False,
                reindex_resource_kb=True,
            )

        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(str(result.get("code") or ""), "ok")
        self.assertTrue(bool(result.get("kb_reindexed")))
        self.assertEqual(str(result.get("kb_reindex_error") or ""), "")
        reindex_mock.assert_called_once_with(
            actor=actor,
            resource=resource,
            check_method="wiki_sync",
        )

    def test_sync_marks_partial_error_when_kb_reindex_fails(self):
        actor = self._actor()
        resource = self._resource()
        with patch(
            "dashboard.github_wiki_sync_service.resource_github_repository_names",
            return_value=["octocat/hello-world"],
        ), patch(
            "dashboard.github_wiki_sync_service._resolve_access_token_from_users",
            return_value=(actor, "ghs_token", ""),
        ), patch(
            "dashboard.github_wiki_sync_service._github_repo_context",
            return_value=({"wiki_enabled": "1"}, ""),
        ), patch(
            "dashboard.github_wiki_sync_service._pull_remote_wiki_into_local",
            return_value={
                "remote_files": 1,
                "created": 1,
                "updated": 0,
                "unchanged": 0,
                "draft_skipped": 0,
                "errors": 0,
                "error": "",
            },
        ), patch(
            "dashboard.github_wiki_sync_service._reindex_resource_kb_after_sync",
            return_value=(False, "kb_reindex_failed:boom"),
        ):
            result = sync_resource_wiki_with_github(
                actor=actor,
                resource=resource,
                token_users=[actor],
                pull_remote=True,
                push_changes=False,
                reindex_resource_kb=True,
            )

        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(str(result.get("code") or ""), "partial_error")
        self.assertFalse(bool(result.get("kb_reindexed")))
        self.assertIn("kb_reindex_failed:boom", str(result.get("kb_reindex_error") or ""))
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        self.assertIn("kb:kb_reindex_failed:boom", errors)

    def test_resolve_access_token_uses_global_env_fallback(self):
        os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = "ghp_global_fallback_token"

        token_user, access_token, token_error = _resolve_access_token_from_users([])

        self.assertIsNone(token_user)
        self.assertEqual(access_token, "ghp_global_fallback_token")
        self.assertEqual(token_error, "")

    def test_sync_allows_public_repo_without_token_when_enabled(self):
        actor = self._actor()
        resource = self._resource()
        os.environ["ALSHIVAL_GITHUB_WIKI_ALLOW_ANON"] = "1"
        with patch(
            "dashboard.github_wiki_sync_service.resource_github_repository_names",
            return_value=["Alshival-Ai/alshival"],
        ), patch(
            "dashboard.github_wiki_sync_service._resolve_access_token_from_users",
            return_value=(None, "", "missing_github_token"),
        ), patch(
            "dashboard.github_wiki_sync_service._github_repo_context",
            return_value=({"wiki_enabled": "1"}, ""),
        ) as repo_context_mock, patch(
            "dashboard.github_wiki_sync_service._pull_remote_wiki_into_local",
            return_value={
                "remote_files": 1,
                "created": 1,
                "updated": 0,
                "unchanged": 0,
                "draft_skipped": 0,
                "errors": 0,
                "error": "",
            },
        ):
            result = sync_resource_wiki_with_github(
                actor=actor,
                resource=resource,
                token_users=[actor],
                pull_remote=True,
                push_changes=False,
                reindex_resource_kb=False,
            )

        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(str(result.get("code") or ""), "ok")
        repo_context_mock.assert_called_once_with(
            repository_full_name="Alshival-Ai/alshival",
            access_token="",
        )
