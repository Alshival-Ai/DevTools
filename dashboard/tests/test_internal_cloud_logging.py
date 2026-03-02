import os
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from dashboard.internal_cloud_logging import configure_internal_sdk_for_resource


class _FakeSdkModule:
    def __init__(self) -> None:
        self.configure_calls: list[dict] = []
        self.attach_calls: list[str] = []

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(dict(kwargs))

    def attach(self, logger, **_kwargs):
        self.attach_calls.append(str(getattr(logger, "name", "") or ""))


class InternalCloudLoggingTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        for key in [
            "ALSHIVAL_API_KEY",
            "ALSHIVAL_RESOURCE",
            "ALSHIVAL_USERNAME",
            "ALSHIVAL_CLOUD_LEVEL",
            "ALSHIVAL_SDK_AUTO_CONFIG",
            "ALSHIVAL_SDK_LOCAL_BASE_URL",
            "ALSHIVAL_SDK_LOCAL_HOST",
            "ALSHIVAL_SDK_LOCAL_PORT",
            "ALSHIVAL_SDK_LOGGER_NAMES",
            "ALSHIVAL_SDK_AUTO_ROTATE_KEY",
            "ALSHIVAL_SDK_KEY_ROTATE_INTERVAL_SECONDS",
            "ALSHIVAL_SDK_MANAGED_API_KEY",
        ]:
            os.environ.pop(key, None)

    def _owner(self):
        return SimpleNamespace(id=7, username="admin-user", get_username=lambda: "admin-user")

    @mock.patch("dashboard.internal_cloud_logging._start_key_rotation_thread")
    @mock.patch("dashboard.internal_cloud_logging.create_global_team_api_key")
    @mock.patch("dashboard.internal_cloud_logging._load_sdk_module")
    def test_configures_sdk_with_generated_global_key(
        self,
        load_sdk_mock,
        create_key_mock,
        rotation_thread_mock,
    ):
        fake_sdk = _FakeSdkModule()
        load_sdk_mock.return_value = fake_sdk
        create_key_mock.return_value = (1, "team-generated-key")
        os.environ["ALSHIVAL_SDK_LOCAL_BASE_URL"] = "http://localhost:8000"
        os.environ["ALSHIVAL_SDK_AUTO_ROTATE_KEY"] = "0"

        ok = configure_internal_sdk_for_resource(
            owner=self._owner(),
            resource_uuid="517616d5-4513-4d19-a59e-0e5d052f46b5",
        )

        self.assertTrue(ok)
        self.assertEqual(os.environ["ALSHIVAL_API_KEY"], "team-generated-key")
        self.assertEqual(os.environ["ALSHIVAL_USERNAME"], "admin-user")
        self.assertEqual(
            os.environ["ALSHIVAL_RESOURCE"],
            "http://localhost:8000/u/admin-user/resources/517616d5-4513-4d19-a59e-0e5d052f46b5/",
        )
        self.assertEqual(os.environ["ALSHIVAL_CLOUD_LEVEL"], "ERROR")
        self.assertEqual(os.environ["ALSHIVAL_SDK_MANAGED_API_KEY"], "1")
        self.assertEqual(len(fake_sdk.configure_calls), 1)
        self.assertEqual(
            fake_sdk.configure_calls[0]["resource"],
            "http://localhost:8000/u/admin-user/resources/517616d5-4513-4d19-a59e-0e5d052f46b5/",
        )
        self.assertEqual(fake_sdk.configure_calls[0]["cloud_level"], "ERROR")
        self.assertIn("dashboard", fake_sdk.attach_calls)
        self.assertIn("alshival", fake_sdk.attach_calls)
        rotation_thread_mock.assert_called_once()

    @mock.patch("dashboard.internal_cloud_logging._start_key_rotation_thread")
    @mock.patch("dashboard.internal_cloud_logging.create_global_team_api_key")
    @mock.patch("dashboard.internal_cloud_logging._load_sdk_module")
    def test_uses_existing_env_key_and_resource(
        self,
        load_sdk_mock,
        create_key_mock,
        rotation_thread_mock,
    ):
        fake_sdk = _FakeSdkModule()
        load_sdk_mock.return_value = fake_sdk
        os.environ["ALSHIVAL_API_KEY"] = "preconfigured-key"
        os.environ["ALSHIVAL_USERNAME"] = "custom-user"
        os.environ["ALSHIVAL_RESOURCE"] = "http://localhost:9000/u/custom-user/resources/resource-id/"
        os.environ["ALSHIVAL_CLOUD_LEVEL"] = "ERROR"

        ok = configure_internal_sdk_for_resource(
            owner=self._owner(),
            resource_uuid="517616d5-4513-4d19-a59e-0e5d052f46b5",
        )

        self.assertTrue(ok)
        self.assertEqual(len(fake_sdk.configure_calls), 1)
        self.assertEqual(fake_sdk.configure_calls[0]["api_key"], "preconfigured-key")
        self.assertEqual(fake_sdk.configure_calls[0]["username"], "custom-user")
        self.assertEqual(
            fake_sdk.configure_calls[0]["resource"],
            "http://localhost:9000/u/custom-user/resources/resource-id/",
        )
        self.assertEqual(fake_sdk.configure_calls[0]["cloud_level"], "ERROR")
        self.assertEqual(os.environ["ALSHIVAL_SDK_MANAGED_API_KEY"], "0")
        create_key_mock.assert_not_called()
        rotation_thread_mock.assert_not_called()
