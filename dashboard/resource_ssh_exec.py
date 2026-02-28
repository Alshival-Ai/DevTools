from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from tempfile import NamedTemporaryFile
from typing import Any

from .global_ssh_store import get_global_ssh_private_key
from .resources_store import _decrypt_key_text, get_ssh_credential_private_key


def _ssh_binary() -> str:
    configured = (
        str(os.getenv("MCP_RESOURCE_SSH_BIN") or "").strip()
        or str(os.getenv("WEB_TERMINAL_SSH_BIN") or "").strip()
    )
    if configured:
        if os.path.isabs(configured):
            if os.path.exists(configured):
                return configured
        else:
            resolved = shutil.which(configured)
            if resolved:
                return resolved

    fallback = shutil.which("ssh") or "/usr/bin/ssh"
    if os.path.exists(fallback):
        return fallback
    raise RuntimeError(f"ssh binary not found at {fallback}")


def _strict_host_key_checking() -> str:
    return (
        str(os.getenv("MCP_RESOURCE_SSH_STRICT_HOST_KEY_CHECKING") or "").strip()
        or str(os.getenv("WEB_TERMINAL_SSH_STRICT_HOST_KEY_CHECKING") or "").strip()
        or "no"
    )


def _known_hosts_path(strict_checking: str) -> str:
    known_hosts = (
        str(os.getenv("MCP_RESOURCE_SSH_KNOWN_HOSTS") or "").strip()
        or str(os.getenv("WEB_TERMINAL_SSH_KNOWN_HOSTS") or "").strip()
    )
    if not known_hosts and str(strict_checking).strip().lower() == "no":
        return "/dev/null"
    return known_hosts


def _extra_ssh_args() -> list[str]:
    raw = (
        str(os.getenv("MCP_RESOURCE_SSH_EXTRA_ARGS") or "").strip()
        or str(os.getenv("WEB_TERMINAL_SSH_EXTRA_ARGS") or "").strip()
    )
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return []


def _decode_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    resolved_limit = max(256, min(int(max_chars or 12000), 120000))
    if len(value) <= resolved_limit:
        return value, False
    return value[:resolved_limit], True


def _resolve_private_key_text(*, owner_user, resource) -> str:
    key_text = _decrypt_key_text(str(getattr(resource, "ssh_key_text", "") or "")) or ""
    credential_ref = str(getattr(resource, "ssh_credential_id", "") or "").strip()

    if not key_text and credential_ref:
        if credential_ref.startswith("global:"):
            try:
                credential_id = int(credential_ref.split(":", 1)[1])
            except (TypeError, ValueError):
                credential_id = 0
            if credential_id > 0:
                key_text = str(get_global_ssh_private_key(credential_id=credential_id) or "")
        else:
            local_ref = credential_ref
            if credential_ref.startswith("local:"):
                local_ref = credential_ref.split(":", 1)[1]
            key_text = str(get_ssh_credential_private_key(owner_user, local_ref) or "")

    return key_text


def execute_resource_ssh_command(
    *,
    owner_user,
    resource,
    command: str,
    timeout_seconds: int = 30,
    max_output_chars: int = 12000,
) -> dict[str, Any]:
    resolved_command = str(command or "").strip()
    if not resolved_command:
        return {"ok": False, "error": "command is required"}
    if len(resolved_command) > 8000:
        return {"ok": False, "error": "command is too long (max 8000 chars)"}

    resource_type = str(getattr(resource, "resource_type", "") or "").strip().lower()
    if resource_type != "vm":
        return {"ok": False, "error": "resource is not a virtual machine"}

    host = str(getattr(resource, "address", "") or getattr(resource, "target", "") or "").strip()
    username = str(getattr(resource, "ssh_username", "") or "").strip()
    if not host:
        return {"ok": False, "error": "resource host is missing"}
    if not username:
        return {"ok": False, "error": "resource ssh username is missing"}

    try:
        port = int(getattr(resource, "ssh_port", 22) or 22)
    except (TypeError, ValueError):
        port = 22
    if port <= 0:
        port = 22

    key_text = _resolve_private_key_text(owner_user=owner_user, resource=resource)
    if not key_text:
        return {"ok": False, "error": "no ssh key available for this resource"}

    resolved_timeout = max(5, min(int(timeout_seconds or 30), 600))
    key_path = ""
    started = time.monotonic()
    try:
        with NamedTemporaryFile(
            delete=False,
            prefix="alshival-mcp-ssh-",
            suffix=".key",
            mode="w",
            encoding="utf-8",
        ) as handle:
            normalized_key_text = key_text.replace("\r", "").strip()
            if normalized_key_text and not normalized_key_text.endswith("\n"):
                normalized_key_text += "\n"
            handle.write(normalized_key_text)
            key_path = handle.name
        os.chmod(key_path, 0o600)

        strict_checking = _strict_host_key_checking()
        known_hosts = _known_hosts_path(strict_checking)
        args = [
            _ssh_binary(),
            "-T",
            "-i",
            key_path,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            f"StrictHostKeyChecking={strict_checking}",
            "-p",
            str(port),
        ]
        if known_hosts:
            args.extend(["-o", f"UserKnownHostsFile={known_hosts}"])
        args.extend(_extra_ssh_args())
        args.extend([f"{username}@{host}", resolved_command])

        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=resolved_timeout,
            check=False,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout_text, stdout_truncated = _truncate_text(
            _decode_output(completed.stdout),
            max_output_chars,
        )
        stderr_text, stderr_truncated = _truncate_text(
            _decode_output(completed.stderr),
            max_output_chars,
        )
        return {
            "ok": int(completed.returncode) == 0,
            "resource_uuid": str(getattr(resource, "resource_uuid", "") or ""),
            "resource_name": str(getattr(resource, "name", "") or ""),
            "host": host,
            "username": username,
            "port": port,
            "command": resolved_command,
            "timeout_seconds": resolved_timeout,
            "duration_ms": elapsed_ms,
            "timed_out": False,
            "exit_code": int(completed.returncode),
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout_text, stdout_truncated = _truncate_text(
            _decode_output(exc.stdout),
            max_output_chars,
        )
        stderr_text, stderr_truncated = _truncate_text(
            _decode_output(exc.stderr),
            max_output_chars,
        )
        return {
            "ok": False,
            "resource_uuid": str(getattr(resource, "resource_uuid", "") or ""),
            "resource_name": str(getattr(resource, "name", "") or ""),
            "host": host,
            "username": username,
            "port": port,
            "command": resolved_command,
            "timeout_seconds": resolved_timeout,
            "duration_ms": elapsed_ms,
            "timed_out": True,
            "exit_code": None,
            "error": "ssh command timed out",
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "resource_uuid": str(getattr(resource, "resource_uuid", "") or ""),
            "resource_name": str(getattr(resource, "name", "") or ""),
            "host": host,
            "username": username,
            "port": port,
            "command": resolved_command,
            "timeout_seconds": resolved_timeout,
            "duration_ms": elapsed_ms,
            "timed_out": False,
            "exit_code": None,
            "error": f"ssh execution failed: {exc}",
            "stdout": "",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
    finally:
        if key_path:
            try:
                os.remove(key_path)
            except OSError:
                pass
