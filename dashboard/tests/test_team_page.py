import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from dashboard.models import ResourceTeamShare
from dashboard.resources_store import (
    add_resource,
    get_resource,
    replace_user_calendar_event_cache,
    set_user_asana_task_resource_mapping,
)
from dashboard.setup_state import get_or_create_setup_state


class TeamPageResourceFilteringTests(TestCase):
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

    def _create_resource(self, *, owner, name: str, access_scope: str = "account", team_names=None):
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
            access_scope=access_scope,
            team_names=team_names or [],
        )
        resource = get_resource(owner, resource_id)
        self.assertIsNotNone(resource)
        return resource

    def test_team_page_lists_only_team_owned_or_shared_resources(self):
        setup_state = get_or_create_setup_state()
        if setup_state is not None:
            setup_state.is_completed = True
            setup_state.save(update_fields=["is_completed", "updated_at"])

        member = self._create_user("team_page_member")
        owner = self._create_user("team_page_owner")
        alpha = Group.objects.create(name="Alpha Team")
        bravo = Group.objects.create(name="Bravo Team")
        gamma = Group.objects.create(name="Gamma Team")
        member.groups.add(alpha, bravo)
        owner.groups.add(bravo)

        account_only = self._create_resource(owner=member, name="Account Only Resource")
        shared_alpha = self._create_resource(owner=owner, name="Shared Alpha Resource")
        team_owned_bravo = self._create_resource(
            owner=owner,
            name="Team Owned Bravo Resource",
            access_scope="team",
            team_names=[bravo.name],
        )
        shared_gamma = self._create_resource(owner=owner, name="Shared Gamma Resource")

        ResourceTeamShare.objects.create(
            owner=owner,
            resource_uuid=shared_alpha.resource_uuid,
            resource_name=shared_alpha.name,
            team=alpha,
            granted_by=owner,
        )
        ResourceTeamShare.objects.create(
            owner=owner,
            resource_uuid=shared_gamma.resource_uuid,
            resource_name=shared_gamma.name,
            team=gamma,
            granted_by=owner,
        )

        self.client.force_login(member)
        response = self.client.get(reverse("team_page"))
        self.assertEqual(response.status_code, 200)

        team_resources = list(response.context["team_resources"])
        resource_names = {str(item.get("name") or "").strip() for item in team_resources}
        self.assertIn(shared_alpha.name, resource_names)
        self.assertIn(team_owned_bravo.name, resource_names)
        self.assertNotIn(account_only.name, resource_names)
        self.assertNotIn(shared_gamma.name, resource_names)

        resources_by_name = {
            str(item.get("name") or "").strip(): item
            for item in team_resources
        }
        self.assertEqual(resources_by_name[shared_alpha.name]["team_names"], [alpha.name])
        self.assertEqual(resources_by_name[team_owned_bravo.name]["team_names"], [bravo.name])
        self.assertTrue(all(item.get("team_names") for item in team_resources))
        self.assertTrue(
            all("account-owned" not in str(item.get("source_label") or "") for item in team_resources)
        )

    def test_team_page_planner_includes_team_mapped_asana_tasks(self):
        setup_state = get_or_create_setup_state()
        if setup_state is not None:
            setup_state.is_completed = True
            setup_state.save(update_fields=["is_completed", "updated_at"])

        member = self._create_user("team_planner_member")
        owner = self._create_user("team_planner_owner")
        alpha = Group.objects.create(name="Alpha Team")
        member.groups.add(alpha)
        owner.groups.add(alpha)

        shared_resource = self._create_resource(owner=owner, name="Shared Planner Resource")
        private_resource = self._create_resource(owner=owner, name="Private Planner Resource")
        ResourceTeamShare.objects.create(
            owner=owner,
            resource_uuid=shared_resource.resource_uuid,
            resource_name=shared_resource.name,
            team=alpha,
            granted_by=owner,
        )

        set_user_asana_task_resource_mapping(
            owner,
            task_gid="task-team-visible",
            resource_uuids=[shared_resource.resource_uuid],
        )
        set_user_asana_task_resource_mapping(
            owner,
            task_gid="task-private-hidden",
            resource_uuids=[private_resource.resource_uuid],
        )
        replace_user_calendar_event_cache(
            owner,
            provider="asana",
            events=[
                {
                    "event_id": "task-team-visible",
                    "title": "Team visible task",
                    "due_date": "2026-03-10",
                    "due_time": "09:15",
                    "is_completed": False,
                    "status": "open",
                    "source_url": "https://app.asana.com/0/123/task-team-visible",
                    "gid": "task-team-visible",
                    "name": "Team visible task",
                    "completed": False,
                    "task_url": "https://app.asana.com/0/123/task-team-visible",
                    "project_links": [],
                    "section_name": "Delivery",
                },
                {
                    "event_id": "task-private-hidden",
                    "title": "Private hidden task",
                    "due_date": "2026-03-11",
                    "due_time": "13:00",
                    "is_completed": False,
                    "status": "open",
                    "source_url": "https://app.asana.com/0/123/task-private-hidden",
                    "gid": "task-private-hidden",
                    "name": "Private hidden task",
                    "completed": False,
                    "task_url": "https://app.asana.com/0/123/task-private-hidden",
                    "project_links": [],
                    "section_name": "Delivery",
                },
            ],
            fetched_at_epoch=1_771_000_000,
            status="ok",
        )

        self.client.force_login(member)
        response = self.client.get(reverse("team_page"))
        self.assertEqual(response.status_code, 200)

        planner_by_team = response.context["team_planner_external_items_by_team"]
        alpha_items = planner_by_team.get(str(alpha.id), [])
        item_ids = {str(item.get("id") or "").strip() for item in alpha_items}
        self.assertIn("asana-task-task-team-visible", item_ids)
        self.assertNotIn("asana-task-task-private-hidden", item_ids)

        visible_item = next(
            item for item in alpha_items
            if str(item.get("id") or "").strip() == "asana-task-task-team-visible"
        )
        self.assertEqual(str(visible_item.get("source") or "").strip(), "asana")
        self.assertFalse(bool(visible_item.get("can_toggle", True)))
