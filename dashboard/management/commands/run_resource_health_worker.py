from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import os
from pathlib import Path
import random
import socket
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.utils import OperationalError, ProgrammingError

from dashboard.health import check_health
from dashboard.knowledge_store import cleanup_stale_knowledge_records
from dashboard.models import ResourcePackageOwner, ResourceRouteAlias
from dashboard.resources_store import get_resource_by_uuid
from dashboard.setup_state import is_global_monitoring_enabled


class Command(BaseCommand):
    help = "Run periodic health checks for resources discovered in user/team/global data roots."

    def _safe_cleanup_stale_knowledge_records(self) -> dict[str, int]:
        try:
            cleanup = cleanup_stale_knowledge_records()
        except Exception as exc:
            self.stderr.write(f"[health-worker] cleanup skipped (non-fatal): {exc}")
            return {
                "scanned": 0,
                "removed_knowledge": 0,
                "removed_snapshots": 0,
            }
        if not isinstance(cleanup, dict):
            return {
                "scanned": 0,
                "removed_knowledge": 0,
                "removed_snapshots": 0,
            }
        return cleanup

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=300,
            help="Seconds between health-check cycles (default: 300).",
        )
        parser.add_argument(
            "--run-once",
            action="store_true",
            help="Run a single cycle and exit.",
        )
        parser.add_argument(
            "--jitter-seconds",
            type=int,
            default=15,
            help="Random jitter (+/- seconds) applied between cycles (default: 15).",
        )
        parser.add_argument(
            "--users-per-worker",
            type=int,
            default=10,
            help="Target resources-to-worker ratio for each cycle (default: 10).",
        )
        parser.add_argument(
            "--max-workers",
            type=int,
            default=16,
            help="Maximum concurrent workers per cycle (default: 16).",
        )

    def _egress_probe_targets(self) -> list[tuple[str, int]]:
        raw_targets = str(os.getenv("HEALTH_EGRESS_PROBE_TARGETS", "") or "").strip()
        if raw_targets:
            candidates = [item.strip() for item in raw_targets.split(",") if item.strip()]
        else:
            candidates = [
                "1.1.1.1:443",
                "8.8.8.8:53",
                "9.9.9.9:53",
            ]

        parsed: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for candidate in candidates:
            host = candidate
            port = 443
            if ":" in candidate:
                maybe_host, maybe_port = candidate.rsplit(":", 1)
                host = maybe_host.strip()
                try:
                    port = int(maybe_port.strip())
                except Exception:
                    continue
            host = str(host or "").strip()
            if not host:
                continue
            if not (1 <= int(port) <= 65535):
                continue
            key = (host, int(port))
            if key in seen:
                continue
            seen.add(key)
            parsed.append(key)
        return parsed

    def _verify_egress(self, timeout_seconds: float = 3.0) -> tuple[bool, str]:
        probe_targets = self._egress_probe_targets()
        if not probe_targets:
            return False, "no egress probe targets configured"

        failures: list[str] = []
        for host, port in probe_targets:
            try:
                with socket.create_connection((host, int(port)), timeout=float(timeout_seconds)):
                    pass
                return True, f"{host}:{int(port)}"
            except Exception as exc:
                failures.append(f"{host}:{int(port)} ({exc})")
        return False, "; ".join(failures[:3])

    def _resource_data_roots(self) -> dict[str, Path]:
        return {
            "user": Path(getattr(settings, "USER_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "user_data")),
            "team": Path(getattr(settings, "TEAM_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "team_data")),
            "global": Path(getattr(settings, "GLOBAL_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "global_data")),
        }

    def _discover_resource_uuids_from_disk(self) -> tuple[dict[str, str], int]:
        discovered: dict[str, str] = {}
        scan_errors = 0
        roots = self._resource_data_roots()
        for scope, root in roots.items():
            try:
                root_path = Path(root)
                if not root_path.exists():
                    continue
                if scope == "global":
                    resource_roots = [root_path / "resources"]
                elif scope == "user":
                    resource_roots = []
                    for entry in root_path.iterdir():
                        if not entry.is_dir():
                            continue
                        resource_roots.append(entry / "home" / ".alshival" / "resources")
                        resource_roots.append(entry / "resources")
                else:
                    resource_roots = [entry / "resources" for entry in root_path.iterdir() if entry.is_dir()]
                for resources_dir in resource_roots:
                    if not resources_dir.exists() or not resources_dir.is_dir():
                        continue
                    for resource_dir in resources_dir.iterdir():
                        if not resource_dir.is_dir():
                            continue
                        resource_uuid = str(resource_dir.name or "").strip()
                        if resource_uuid and resource_uuid not in discovered:
                            discovered[resource_uuid] = scope
            except Exception:
                scan_errors += 1
        return discovered, int(scan_errors)

    def _resolve_resource_target(self, *, resource_uuid: str, active_users: list[object]) -> tuple[int, int] | None:
        candidate_users: list[object] = []
        seen_user_ids: set[int] = set()

        owner_row = (
            ResourcePackageOwner.objects.select_related("owner_user")
            .filter(resource_uuid=resource_uuid)
            .first()
        )
        if owner_row and owner_row.owner_user_id and owner_row.owner_user and bool(owner_row.owner_user.is_active):
            candidate_users.append(owner_row.owner_user)
            seen_user_ids.add(int(owner_row.owner_user_id))

        for row in (
            ResourceRouteAlias.objects.select_related("owner_user")
            .filter(resource_uuid=resource_uuid, owner_user_id__isnull=False)
            .order_by("-is_current", "-updated_at")
        ):
            owner_user = row.owner_user
            if owner_user is None or not bool(owner_user.is_active):
                continue
            owner_user_id = int(owner_user.id)
            if owner_user_id in seen_user_ids:
                continue
            candidate_users.append(owner_user)
            seen_user_ids.add(owner_user_id)

        for user in active_users:
            user_id = int(user.id)
            if user_id in seen_user_ids:
                continue
            candidate_users.append(user)
            seen_user_ids.add(user_id)

        for user in candidate_users:
            try:
                resource = get_resource_by_uuid(user, resource_uuid)
            except Exception:
                continue
            if resource is None:
                continue
            return int(user.id), int(resource.id)
        return None

    def _discover_targets(self) -> tuple[list[tuple[int, int, str]], int, int, int]:
        User = get_user_model()
        try:
            users = list(User.objects.filter(is_active=True).order_by("id"))
        except (OperationalError, ProgrammingError) as exc:
            self.stderr.write(f"[health-worker] database not ready yet; skipping cycle ({exc})")
            return [], 1, 0, 0

        targets: list[tuple[int, int, str]] = []
        discovery_errors = 0
        unresolved = 0
        discovered_uuids, scan_errors = self._discover_resource_uuids_from_disk()
        discovery_errors += int(scan_errors)
        for resource_uuid in sorted(discovered_uuids.keys()):
            try:
                resolved = self._resolve_resource_target(resource_uuid=resource_uuid, active_users=users)
            except Exception as exc:
                discovery_errors += 1
                self.stderr.write(f"[health-worker] failed resolving resource {resource_uuid}: {exc}")
                continue
            if resolved is None:
                unresolved += 1
                continue
            user_id, resource_id = resolved
            targets.append((int(user_id), int(resource_id), resource_uuid))
        return targets, int(discovery_errors), int(unresolved), int(len(discovered_uuids))

    def _check_target(self, user_id: int, resource_id: int, resource_uuid: str) -> tuple[int, int]:
        close_old_connections()
        try:
            User = get_user_model()
            user = User.objects.filter(id=int(user_id), is_active=True).first()
            if not user:
                return 0, 0
            try:
                check_health(int(resource_id), user=user, emit_transition_log=True)
                return 1, 0
            except Exception as exc:
                self.stderr.write(
                    f"[health-worker] check failed user={user.id} resource={int(resource_id)} "
                    f"uuid={str(resource_uuid or '')}: {exc}"
                )
                return 0, 1
        finally:
            close_old_connections()

    def _run_cycle(
        self,
        users_per_worker: int,
        max_workers: int,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int]:
        if not is_global_monitoring_enabled():
            cleanup = self._safe_cleanup_stale_knowledge_records()
            return (
                0,
                0,
                0,
                0,
                0,
                0,
                int(cleanup.get("scanned", 0)),
                int(cleanup.get("removed_knowledge", 0)),
                int(cleanup.get("removed_snapshots", 0)),
                0,
            )

        egress_ok, egress_detail = self._verify_egress()
        if not egress_ok:
            self.stderr.write(f"[health-worker] egress check failed; skipping cycle ({egress_detail})")
            cleanup = self._safe_cleanup_stale_knowledge_records()
            return (
                0,
                0,
                0,
                0,
                0,
                0,
                int(cleanup.get("scanned", 0)),
                int(cleanup.get("removed_knowledge", 0)),
                int(cleanup.get("removed_snapshots", 0)),
                1,
            )

        targets, discovery_errors, unresolved, discovered_total = self._discover_targets()
        target_count = len(targets)
        if target_count == 0:
            cleanup = self._safe_cleanup_stale_knowledge_records()
            return (
                0,
                int(discovery_errors),
                0,
                0,
                int(unresolved),
                int(discovered_total),
                int(cleanup.get("scanned", 0)),
                int(cleanup.get("removed_knowledge", 0)),
                int(cleanup.get("removed_snapshots", 0)),
                0,
            )

        ratio = max(1, int(users_per_worker))
        desired_workers = max(1, int(math.ceil(target_count / ratio)))
        pool_workers = max(1, desired_workers)
        if max_workers > 0:
            pool_workers = min(pool_workers, int(max_workers))

        checked_count = 0
        error_count = int(discovery_errors)

        with ThreadPoolExecutor(max_workers=pool_workers, thread_name_prefix="health-resource") as executor:
            futures = [
                executor.submit(self._check_target, int(user_id), int(resource_id), str(resource_uuid or ""))
                for user_id, resource_id, resource_uuid in targets
            ]
            for future in as_completed(futures):
                try:
                    checked, errors = future.result()
                    checked_count += int(checked)
                    error_count += int(errors)
                except Exception as exc:
                    error_count += 1
                    self.stderr.write(f"[health-worker] worker failure: {exc}")

        cleanup = self._safe_cleanup_stale_knowledge_records()
        return (
            checked_count,
            error_count,
            target_count,
            pool_workers,
            int(unresolved),
            int(discovered_total),
            int(cleanup.get("scanned", 0)),
            int(cleanup.get("removed_knowledge", 0)),
            int(cleanup.get("removed_snapshots", 0)),
            0,
        )

    def handle(self, *args, **options):
        interval_seconds = max(30, int(options["interval_seconds"]))
        run_once = bool(options["run_once"])
        jitter_seconds = max(0, int(options["jitter_seconds"]))
        users_per_worker = max(1, int(options["users_per_worker"]))
        max_workers = max(1, int(options["max_workers"]))

        self.stdout.write(
            "[health-worker] started "
            f"interval={interval_seconds}s jitter=±{jitter_seconds}s "
            f"resources_per_worker={users_per_worker} max_workers={max_workers} "
            f"run_once={run_once}"
        )

        while True:
            started = time.time()
            (
                checked_count,
                error_count,
                target_count,
                pool_workers,
                unresolved,
                discovered_total,
                cleanup_scanned,
                cleanup_removed_knowledge,
                cleanup_removed_snapshots,
                egress_skipped,
            ) = self._run_cycle(
                users_per_worker=users_per_worker,
                max_workers=max_workers,
            )
            elapsed = time.time() - started
            self.stdout.write(
                "[health-worker] cycle complete "
                f"targets={target_count} pool_workers={pool_workers} "
                f"checked={checked_count} errors={error_count} "
                f"discovered={discovered_total} unresolved={unresolved} "
                f"egress_skipped={egress_skipped} "
                f"cleanup_scanned={cleanup_scanned} cleanup_removed_knowledge={cleanup_removed_knowledge} "
                f"cleanup_removed_snapshots={cleanup_removed_snapshots} elapsed={elapsed:.1f}s"
            )

            if run_once:
                return

            jitter = random.uniform(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0.0
            next_interval = max(30.0, float(interval_seconds) + jitter)
            sleep_for = max(0.0, next_interval - elapsed)
            time.sleep(sleep_for)
