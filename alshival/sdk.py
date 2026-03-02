from __future__ import annotations

import logging
from typing import Any

from dashboard.internal_cloud_logging import get_internal_sdk_module


class _FallbackLogFacade:
    def __init__(self) -> None:
        self._logger = logging.getLogger("alshival.sdk")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)


def module():
    return get_internal_sdk_module()


def configure(**kwargs) -> None:
    sdk_module = module()
    if sdk_module is None:
        return
    sdk_module.configure(**kwargs)


def attach(logger: logging.Logger | str, **kwargs):
    sdk_module = module()
    if sdk_module is None:
        return None
    return sdk_module.attach(logger, **kwargs)


def get_logger(name: str, **kwargs):
    sdk_module = module()
    if sdk_module is None:
        return logging.getLogger(name)
    return sdk_module.get_logger(name, **kwargs)


def handler(**kwargs):
    sdk_module = module()
    if sdk_module is None:
        return None
    return sdk_module.handler(**kwargs)


def details() -> dict[str, Any]:
    sdk_module = module()
    if sdk_module is None:
        return {}
    if hasattr(sdk_module, "log") and hasattr(sdk_module.log, "details"):
        return dict(sdk_module.log.details())
    return {}


_sdk_module = module()
if _sdk_module is not None and hasattr(_sdk_module, "log"):
    log = _sdk_module.log
else:
    log = _FallbackLogFacade()

if _sdk_module is not None and hasattr(_sdk_module, "ALERT_LEVEL"):
    ALERT_LEVEL = int(getattr(_sdk_module, "ALERT_LEVEL"))
else:
    ALERT_LEVEL = 45
