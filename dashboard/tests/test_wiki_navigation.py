import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from dashboard.models import ResourceTeamShare, WikiPage
from dashboard.resources_store import add_resource, get_resource
from dashboard.setup_state import get_or_create_setup_state


class WikiNavigationViewTests(TestCase):
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

    def setUp(self):
        setup_state = get_or_create_setup_state()
        if setup_state is not None:
            setup_state.is_completed = True
            setup_state.save(update_fields=["is_completed", "updated_at"])

    def _create_user(self, username: str, *, is_superuser: bool = False):
        User = get_user_model()
        return User.objects.create_user(
            username=username,
            password="pass1234",
            email=f"{username}@example.com",
            is_staff=True,
            is_superuser=is_superuser,
        )

    def _create_resource(self, *, owner, name: str):
        resource_id = add_resource(
            owner,
            name=name,
            resource_type="vm",
            target="10.0.0.20",
            address="10.0.0.20",
            notes="",
            ssh_username="ubuntu",
            ssh_key_text="dummy-private-key",
            ssh_port="22",
            access_scope="account",
            team_names=[],
        )
        resource = get_resource(owner, resource_id)
        self.assertIsNotNone(resource)
        return resource

    def _create_page(
        self,
        *,
        creator,
        scope: str,
        path: str,
        title: str,
        resource_uuid: str = "",
        resource_name: str = "",
        team_names=None,
    ):
        page = WikiPage.objects.create(
            scope=scope,
            resource_uuid=resource_uuid,
            resource_name=resource_name,
            path=path,
            title=title,
            is_draft=False,
            body_markdown=f"# {title}\n\nBody",
            body_html_fallback="",
            created_by=creator,
            updated_by=creator,
        )
        resolved_team_names = list(team_names or [])
        if resolved_team_names:
            teams = list(Group.objects.filter(name__in=resolved_team_names))
            page.team_access.set(teams)
        return page

    def test_wiki_landing_state_without_page_selection(self):
        actor = self._create_user("wiki_landing_actor")
        self._create_page(
            creator=actor,
            scope=WikiPage.SCOPE_WORKSPACE,
            path="workspace/intro",
            title="Workspace Intro",
        )

        self.client.force_login(actor)
        response = self.client.get(reverse("wiki"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_page"])
        self.assertContains(response, "Select a wiki page")
        self.assertEqual(response.context["wiki_editor_new_url"], reverse("wiki_editor_new"))

    def test_wiki_unified_listing_contains_workspace_team_and_resource_wikis(self):
        owner = self._create_user("wiki_owner")
        member = self._create_user("wiki_member")
        alpha = Group.objects.create(name="Alpha Team")
        member.groups.add(alpha)
        owner.groups.add(alpha)

        resource = self._create_resource(owner=owner, name="Shared Runtime")
        ResourceTeamShare.objects.create(
            owner=owner,
            resource_uuid=resource.resource_uuid,
            resource_name=resource.name,
            team=alpha,
            granted_by=owner,
        )

        workspace_page = self._create_page(
            creator=owner,
            scope=WikiPage.SCOPE_WORKSPACE,
            path="workspace/overview",
            title="Workspace Overview",
        )
        team_page = self._create_page(
            creator=owner,
            scope=WikiPage.SCOPE_TEAM,
            resource_uuid=str(alpha.id),
            resource_name=alpha.name,
            path="team/overview",
            title="Team Overview",
            team_names=[alpha.name],
        )
        resource_page = self._create_page(
            creator=owner,
            scope=WikiPage.SCOPE_RESOURCE,
            resource_uuid=resource.resource_uuid,
            resource_name=resource.name,
            path="runbooks/start",
            title="Runtime Runbook",
        )

        self.client.force_login(member)
        response = self.client.get(reverse("wiki"))

        self.assertEqual(response.status_code, 200)
        wiki_pages = list(response.context["wiki_pages"])
        wiki_pages_by_id = {int(item["id"]): item for item in wiki_pages}
        self.assertIn(workspace_page.id, wiki_pages_by_id)
        self.assertIn(team_page.id, wiki_pages_by_id)
        self.assertIn(resource_page.id, wiki_pages_by_id)

        self.assertEqual(wiki_pages_by_id[workspace_page.id]["wiki_key"], "workspace")
        self.assertEqual(wiki_pages_by_id[team_page.id]["wiki_key"], f"team:{alpha.id}")
        self.assertEqual(wiki_pages_by_id[resource_page.id]["wiki_key"], f"resource:{resource.resource_uuid}")

        self.assertContains(response, f"?page_id={workspace_page.id}")
        self.assertContains(response, f"?page_id={team_page.id}")
        self.assertContains(response, f"?page_id={resource_page.id}")

    def test_page_id_selects_exact_page_when_paths_overlap(self):
        actor = self._create_user("wiki_selector")
        alpha = Group.objects.create(name="Selector Team")
        actor.groups.add(alpha)

        self._create_page(
            creator=actor,
            scope=WikiPage.SCOPE_WORKSPACE,
            path="docs/getting-started",
            title="Workspace Getting Started",
        )
        team_page = self._create_page(
            creator=actor,
            scope=WikiPage.SCOPE_TEAM,
            resource_uuid=str(alpha.id),
            resource_name=alpha.name,
            path="docs/getting-started",
            title="Team Getting Started",
            team_names=[alpha.name],
        )

        self.client.force_login(actor)
        response = self.client.get(reverse("wiki"), {"page_id": str(team_page.id)})

        self.assertEqual(response.status_code, 200)
        selected_page = response.context["selected_page"]
        self.assertIsNotNone(selected_page)
        self.assertEqual(int(selected_page.id), int(team_page.id))

        payload = response.context["selected_page_payload"]
        self.assertEqual(str(payload.get("wiki_kind") or ""), WikiPage.SCOPE_TEAM)

        editor_url = str(response.context["wiki_editor_new_url"])
        parsed = urlparse(editor_url)
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("scope", [""])[0], WikiPage.SCOPE_TEAM)
        self.assertEqual(query.get("team_id", [""])[0], str(alpha.id))

    def test_legacy_scope_path_selection_remains_supported(self):
        actor = self._create_user("wiki_legacy_selector")
        alpha = Group.objects.create(name="Legacy Team")
        actor.groups.add(alpha)

        self._create_page(
            creator=actor,
            scope=WikiPage.SCOPE_WORKSPACE,
            path="docs/getting-started",
            title="Workspace Getting Started",
        )
        team_page = self._create_page(
            creator=actor,
            scope=WikiPage.SCOPE_TEAM,
            resource_uuid=str(alpha.id),
            resource_name=alpha.name,
            path="docs/getting-started",
            title="Team Getting Started",
            team_names=[alpha.name],
        )

        self.client.force_login(actor)
        response = self.client.get(
            reverse("wiki"),
            {
                "scope": WikiPage.SCOPE_TEAM,
                "team_id": str(alpha.id),
                "page": "docs/getting-started",
            },
        )

        self.assertEqual(response.status_code, 200)
        selected_page = response.context["selected_page"]
        self.assertIsNotNone(selected_page)
        self.assertEqual(int(selected_page.id), int(team_page.id))

    def test_page_id_for_inaccessible_page_returns_warning(self):
        owner = self._create_user("wiki_hidden_owner")
        actor = self._create_user("wiki_hidden_actor")
        hidden_team = Group.objects.create(name="Hidden Team")
        owner.groups.add(hidden_team)

        hidden_page = self._create_page(
            creator=owner,
            scope=WikiPage.SCOPE_TEAM,
            resource_uuid=str(hidden_team.id),
            resource_name=hidden_team.name,
            path="hidden/guide",
            title="Hidden Guide",
            team_names=[hidden_team.name],
        )

        self.client.force_login(actor)
        response = self.client.get(reverse("wiki"), {"page_id": str(hidden_page.id)})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_page"])
        self.assertEqual(response.context["status_message"], "You do not have access to this wiki page.")

