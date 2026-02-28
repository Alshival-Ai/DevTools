from django.test import SimpleTestCase
from unittest.mock import patch

from dashboard.management.commands.run_resource_health_worker import Command


class _SocketContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class HealthWorkerEgressTests(SimpleTestCase):
    def test_verify_egress_succeeds_when_any_probe_reachable(self):
        command = Command()
        with patch.object(
            Command,
            "_egress_probe_targets",
            return_value=[("1.1.1.1", 443), ("8.8.8.8", 53)],
        ), patch(
            "dashboard.management.commands.run_resource_health_worker.socket.create_connection",
            side_effect=[OSError("network unreachable"), _SocketContext()],
        ) as create_connection:
            ok, detail = command._verify_egress(timeout_seconds=0.1)

        self.assertTrue(ok)
        self.assertIn("8.8.8.8:53", detail)
        self.assertEqual(create_connection.call_count, 2)

    def test_verify_egress_fails_when_all_probes_fail(self):
        command = Command()
        with patch.object(
            Command,
            "_egress_probe_targets",
            return_value=[("1.1.1.1", 443), ("8.8.8.8", 53)],
        ), patch(
            "dashboard.management.commands.run_resource_health_worker.socket.create_connection",
            side_effect=[OSError("no route"), OSError("timed out")],
        ):
            ok, detail = command._verify_egress(timeout_seconds=0.1)

        self.assertFalse(ok)
        self.assertIn("1.1.1.1:443", detail)
        self.assertIn("8.8.8.8:53", detail)

    def test_run_cycle_skips_checks_when_egress_verification_fails(self):
        command = Command()
        with patch(
            "dashboard.management.commands.run_resource_health_worker.is_global_monitoring_enabled",
            return_value=True,
        ), patch.object(
            Command,
            "_verify_egress",
            return_value=(False, "network unreachable"),
        ), patch.object(
            Command,
            "_safe_cleanup_stale_knowledge_records",
            return_value={"scanned": 7, "removed_knowledge": 2, "removed_snapshots": 1},
        ):
            result = command._run_cycle(users_per_worker=10, max_workers=4)

        self.assertEqual(
            result,
            (
                0,
                0,
                0,
                0,
                0,
                0,
                7,
                2,
                1,
                1,
            ),
        )
