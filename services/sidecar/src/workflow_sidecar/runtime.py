"""Request dispatch and cancellation for the workflow sidecar."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
from typing import Any

from .diagnostics import JsonlLogger, create_diagnostic_pack
from .diagnostics.pack import default_log_path
from .persistence import (
    JobQueue,
    WorkspaceService,
    create_db_snapshot,
    default_global_env_path,
    list_snapshots,
    resolve_task_env,
    summarize_env,
)
from .persistence.overview import build_project_overview
from .persistence.paths import file_sha256, resolve_project_path, to_project_relative
from .persistence.creative_packs import CreativePackService
from .persistence.story import StoryService
from .protocol import Request, error_response, event, success_response


Message = dict[str, Any]
Emitter = Callable[[Message], None]


def default_global_db_path() -> Path:
    override = os.environ.get("WORKFLOW_GLOBAL_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ai-video-workflow" / "global.db"


class SidecarRuntime:
    """Runs requests without coupling protocol transport to business handlers."""

    def __init__(
        self,
        emit: Emitter,
        *,
        enable_test_methods: bool = False,
        global_db_path: Path | None = None,
    ) -> None:
        self._emit = emit
        self._enable_test_methods = enable_test_methods
        self._active: dict[str, asyncio.Task[None]] = {}
        self._global_db_path = Path(global_db_path or default_global_db_path())
        self._workspace = WorkspaceService(self._global_db_path)
        self._app_logger = JsonlLogger(
            default_log_path(project_root=None, global_db_path=self._global_db_path)
        )

    async def shutdown(self) -> None:
        self._workspace.close()

    async def handle(self, request: Request) -> None:
        if request.method == "request.cancel":
            await self._cancel(request)
            return

        if request.id in self._active:
            self._emit(
                error_response(
                    request.id,
                    "DUPLICATE_REQUEST_ID",
                    f"Request is already running: {request.id}",
                )
            )
            return

        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise RuntimeError("SidecarRuntime.handle requires an asyncio task")
        self._active[request.id] = current
        try:
            await self._execute(request)
        except asyncio.CancelledError:
            self._emit(error_response(request.id, "CANCELLED", "Request was cancelled"))
        except ValueError as exc:
            self._emit(error_response(request.id, "INVALID_PARAMS", str(exc)))
        except Exception:
            self._emit(
                error_response(
                    request.id,
                    "INTERNAL_ERROR",
                    "The sidecar could not complete the request",
                )
            )
        finally:
            if self._active.get(request.id) is current:
                self._active.pop(request.id, None)

    async def _execute(self, request: Request) -> None:
        if request.method == "system.ping":
            self._emit(
                success_response(
                    request.id,
                    {
                        "status": "ok",
                        "protocol_version": 1,
                        "echo": request.params.get("echo"),
                    },
                )
            )
            return

        if request.method == "project.create":
            parent_dir = request.params.get("parent_dir")
            name = request.params.get("name")
            if not isinstance(parent_dir, str) or not parent_dir:
                raise ValueError("parent_dir must be a non-empty string")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")
            record = self._workspace.create_project(parent_dir, name)
            self._emit(success_response(request.id, record.as_dict()))
            return

        if request.method == "project.open":
            root_dir = request.params.get("root_dir")
            if not isinstance(root_dir, str) or not root_dir:
                raise ValueError("root_dir must be a non-empty string")
            record = self._workspace.open_project(root_dir)
            self._emit(success_response(request.id, record.as_dict()))
            return

        if request.method == "project.close":
            previous = self._workspace.close_project()
            self._emit(
                success_response(
                    request.id,
                    {"closed": previous.as_dict() if previous else None},
                )
            )
            return

        if request.method == "project.current":
            current = self._workspace.current
            self._emit(
                success_response(
                    request.id,
                    {"project": current.as_dict() if current else None},
                )
            )
            return

        if request.method == "project.list_recent":
            limit = request.params.get("limit", 20)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            projects = self._workspace.list_recent(limit)
            self._emit(
                success_response(
                    request.id,
                    {"projects": [item.as_dict() for item in projects]},
                )
            )
            return

        if request.method == "project.overview":
            current = self._workspace.current
            if current is None:
                raise ValueError("no project is open")
            overview = build_project_overview(
                project=current,
                jobs=self._jobs(),
            )
            self._emit(success_response(request.id, overview))
            return

        if request.method.startswith("story."):
            await self._execute_story(request)
            return

        if request.method.startswith("pack."):
            await self._execute_pack(request)
            return

        if request.method.startswith("job."):
            await self._execute_job(request)
            return

        if request.method.startswith("env."):
            await self._execute_env(request)
            return

        if request.method.startswith("snapshot."):
            await self._execute_snapshot(request)
            return

        if request.method.startswith("log.") or request.method.startswith(
            "diagnostics."
        ):
            if request.method in {"diagnostics.count", "diagnostics.crash"}:
                pass
            else:
                await self._execute_diagnostics(request)
                return

        if request.method.startswith("fs."):
            await self._execute_fs(request)
            return

        if self._enable_test_methods and request.method == "diagnostics.count":
            await self._count(request)
            return

        if self._enable_test_methods and request.method == "diagnostics.crash":
            exit_code = self._bounded_int(request.params, "exit_code", 1, 255, 70)
            os._exit(exit_code)

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    def _jobs(self) -> JobQueue:
        return JobQueue(self._workspace.require_project_db())

    def _story(self) -> StoryService:
        current = self._workspace.current
        if current is None:
            raise ValueError("no project is open")
        return StoryService(
            self._workspace.require_project_db(),
            Path(current.root_path),
        )

    def _packs(self) -> CreativePackService:
        self._workspace.require_project_db()
        return CreativePackService(self._workspace.require_project_db())

    async def _execute_pack(self, request: Request) -> None:
        packs = self._packs()
        method = request.method
        params = request.params

        if method == "pack.register":
            name = params.get("name")
            pack_type = params.get("pack_type")
            scope = params.get("scope", "project")
            rules = params.get("rules") or {}
            resources = params.get("resources") or {}
            if not isinstance(name, str) or not isinstance(pack_type, str):
                raise ValueError("name and pack_type must be strings")
            if not isinstance(scope, str):
                raise ValueError("scope must be a string")
            if not isinstance(rules, dict) or not isinstance(resources, dict):
                raise ValueError("rules and resources must be objects")
            result = packs.register_pack(
                name=name,
                pack_type=pack_type,
                scope=scope,
                rules=rules,
                resources=resources,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "pack.list":
            self._emit(
                success_response(request.id, {"packs": packs.list_packs()})
            )
            return

        if method == "pack.publish_revision":
            pack_id = params.get("pack_id")
            rules = params.get("rules")
            resources = params.get("resources") or {}
            if not isinstance(pack_id, str):
                raise ValueError("pack_id must be a string")
            if not isinstance(rules, dict):
                raise ValueError("rules must be an object")
            if not isinstance(resources, dict):
                raise ValueError("resources must be an object")
            result = packs.publish_revision(
                pack_id, rules=rules, resources=resources
            )
            self._emit(success_response(request.id, result))
            return

        if method == "pack.compose":
            name = params.get("name")
            visual_revision_id = params.get("visual_revision_id")
            narrative_revision_id = params.get("narrative_revision_id")
            technique_revision_ids = params.get("technique_revision_ids") or []
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            if not isinstance(visual_revision_id, str) or not isinstance(
                narrative_revision_id, str
            ):
                raise ValueError("visual/narrative revision ids must be strings")
            if not isinstance(technique_revision_ids, list):
                raise ValueError("technique_revision_ids must be an array")
            result = packs.compose(
                name=name,
                visual_revision_id=visual_revision_id,
                narrative_revision_id=narrative_revision_id,
                technique_revision_ids=technique_revision_ids,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "pack.evaluate":
            composition_revision_id = params.get("composition_revision_id")
            suite_id = params.get("suite_id", "builtin-structure-v1")
            if not isinstance(composition_revision_id, str):
                raise ValueError("composition_revision_id must be a string")
            if not isinstance(suite_id, str):
                raise ValueError("suite_id must be a string")
            result = packs.evaluate(
                composition_revision_id, suite_id=suite_id
            )
            self._emit(success_response(request.id, result))
            return

        if method == "pack.lock":
            composition_revision_id = params.get("composition_revision_id")
            purpose = params.get("purpose", "production")
            if not isinstance(composition_revision_id, str):
                raise ValueError("composition_revision_id must be a string")
            if not isinstance(purpose, str):
                raise ValueError("purpose must be a string")
            result = packs.lock(composition_revision_id, purpose=purpose)
            self._emit(success_response(request.id, result))
            return

        if method == "pack.current_lock":
            self._emit(
                success_response(
                    request.id, {"lock": packs.current_lock()}
                )
            )
            return

        if method == "pack.list_compositions":
            self._emit(
                success_response(
                    request.id, {"compositions": packs.list_compositions()}
                )
            )
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_story(self, request: Request) -> None:
        story = self._story()
        method = request.method
        params = request.params

        if method == "story.import_source":
            source_type = params.get("source_type", "novel")
            title = params.get("title")
            text = params.get("text")
            if not isinstance(source_type, str):
                raise ValueError("source_type must be a string")
            if not isinstance(title, str):
                raise ValueError("title must be a string")
            if not isinstance(text, str):
                raise ValueError("text must be a string")
            record = story.import_source(
                source_type=source_type, title=title, text=text
            )
            self._emit(success_response(request.id, record.as_dict()))
            return

        if method == "story.list_sources":
            sources = story.list_sources()
            self._emit(
                success_response(
                    request.id, {"sources": [item.as_dict() for item in sources]}
                )
            )
            return

        if method == "story.split_chapters":
            source_id = params.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("source_id must be a non-empty string")
            chunks = story.split_chapters(source_id)
            self._emit(
                success_response(
                    request.id, {"chunks": [item.as_dict() for item in chunks]}
                )
            )
            return

        if method == "story.list_chunks":
            source_id = params.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("source_id must be a non-empty string")
            chunks = story.list_chunks(source_id)
            self._emit(
                success_response(
                    request.id, {"chunks": [item.as_dict() for item in chunks]}
                )
            )
            return

        if method == "story.create_event":
            title = params.get("title")
            summary = params.get("summary")
            order_key = params.get("order_key", 0)
            origin = params.get("origin", "extracted")
            story_source_id = params.get("story_source_id")
            char_start = params.get("char_start")
            char_end = params.get("char_end")
            confidence = params.get("confidence", 1.0)
            if not isinstance(title, str) or not isinstance(summary, str):
                raise ValueError("title and summary must be strings")
            if not isinstance(order_key, (int, float)) or isinstance(order_key, bool):
                raise ValueError("order_key must be a number")
            if not isinstance(origin, str):
                raise ValueError("origin must be a string")
            if story_source_id is not None and not isinstance(story_source_id, str):
                raise ValueError("story_source_id must be a string")
            if char_start is not None and (
                isinstance(char_start, bool) or not isinstance(char_start, int)
            ):
                raise ValueError("char_start must be an integer")
            if char_end is not None and (
                isinstance(char_end, bool) or not isinstance(char_end, int)
            ):
                raise ValueError("char_end must be an integer")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence must be a number")
            event_view = story.create_event(
                title=title,
                summary=summary,
                order_key=float(order_key),
                origin=origin,
                story_source_id=story_source_id,
                char_start=char_start,
                char_end=char_end,
                confidence=float(confidence),
            )
            self._emit(success_response(request.id, event_view.as_dict()))
            return

        if method == "story.list_events":
            events = story.list_events()
            self._emit(
                success_response(
                    request.id, {"events": [item.as_dict() for item in events]}
                )
            )
            return

        if method == "story.create_edge":
            from_event_id = params.get("from_event_id")
            to_event_id = params.get("to_event_id")
            relation = params.get("relation")
            confidence = params.get("confidence", 1.0)
            if not isinstance(from_event_id, str) or not isinstance(to_event_id, str):
                raise ValueError("from_event_id and to_event_id must be strings")
            if not isinstance(relation, str):
                raise ValueError("relation must be a string")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence must be a number")
            edge = story.create_edge(
                from_event_id=from_event_id,
                to_event_id=to_event_id,
                relation=relation,
                confidence=float(confidence),
            )
            self._emit(success_response(request.id, edge))
            return

        if method == "story.list_edges":
            edges = story.list_edges()
            self._emit(success_response(request.id, {"edges": edges}))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_job(self, request: Request) -> None:
        queue = self._jobs()
        method = request.method
        params = request.params

        if method == "job.enqueue":
            kind = params.get("kind")
            if not isinstance(kind, str):
                raise ValueError("kind must be a string")
            payload = params.get("payload") or {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            max_attempts = params.get("max_attempts", 3)
            if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
                raise ValueError("max_attempts must be an integer")
            job = queue.enqueue(kind, payload, max_attempts=max_attempts)
            self._emit(success_response(request.id, job.as_dict()))
            return

        if method == "job.get":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.get(job_id).as_dict()))
            return

        if method == "job.list":
            status = params.get("status")
            if status is not None and not isinstance(status, str):
                raise ValueError("status must be a string")
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            jobs = queue.list(status=status, limit=limit)
            self._emit(
                success_response(
                    request.id, {"jobs": [item.as_dict() for item in jobs]}
                )
            )
            return

        if method == "job.claim":
            worker_id = params.get("worker_id")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            lease_seconds = params.get("lease_seconds", 60)
            if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
                raise ValueError("lease_seconds must be an integer")
            kinds = params.get("kinds")
            if kinds is not None:
                if not isinstance(kinds, list) or not all(
                    isinstance(item, str) for item in kinds
                ):
                    raise ValueError("kinds must be a string array")
            job = queue.claim(
                worker_id, lease_seconds=lease_seconds, kinds=kinds
            )
            self._emit(
                success_response(
                    request.id, {"job": job.as_dict() if job else None}
                )
            )
            return

        if method == "job.complete":
            job_id = params.get("job_id")
            worker_id = params.get("worker_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            self._emit(
                success_response(
                    request.id, queue.complete(job_id, worker_id).as_dict()
                )
            )
            return

        if method == "job.fail":
            job_id = params.get("job_id")
            worker_id = params.get("worker_id")
            error = params.get("error", "failed")
            retry = params.get("retry", True)
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            if not isinstance(error, str):
                raise ValueError("error must be a string")
            if not isinstance(retry, bool):
                raise ValueError("retry must be a boolean")
            self._emit(
                success_response(
                    request.id,
                    queue.fail(job_id, worker_id, error, retry=retry).as_dict(),
                )
            )
            return

        if method == "job.cancel":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.cancel(job_id).as_dict()))
            return

        if method == "job.pause":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.pause(job_id).as_dict()))
            return

        if method == "job.resume":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.resume(job_id).as_dict()))
            return

        if method == "job.reclaim_expired":
            count = queue.reclaim_expired()
            self._emit(success_response(request.id, {"reclaimed": count}))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    def _project_root(self) -> Path | None:
        current = self._workspace.current
        if current is None:
            return None
        return Path(current.root_path)

    def _global_env_path(self) -> Path:
        return default_global_env_path(self._workspace.global_db_path)

    def _logger(self) -> JsonlLogger:
        project_root = self._project_root()
        if project_root is not None:
            return JsonlLogger(
                default_log_path(
                    project_root=project_root, global_db_path=self._global_db_path
                )
            )
        return self._app_logger

    async def _execute_diagnostics(self, request: Request) -> None:
        method = request.method
        params = request.params

        if method == "log.write":
            level = params.get("level", "info")
            message = params.get("message")
            fields = params.get("fields") or {}
            if not isinstance(level, str):
                raise ValueError("level must be a string")
            if not isinstance(message, str) or not message:
                raise ValueError("message must be a non-empty string")
            if not isinstance(fields, dict):
                raise ValueError("fields must be an object")
            record = self._logger().write(level, message, fields=fields)
            self._emit(success_response(request.id, {"record": record}))
            return

        if method == "log.tail":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            records = self._logger().tail(limit)
            self._emit(success_response(request.id, {"records": records}))
            return

        if method == "diagnostics.create_pack":
            project_root = self._project_root()
            current = self._workspace.current
            jobs: list[dict[str, Any]] = []
            if project_root is not None:
                jobs = [item.as_dict() for item in self._jobs().list(limit=50)]
            log_path = default_log_path(
                project_root=project_root, global_db_path=self._global_db_path
            )
            output_dir = (
                project_root / "temp" / "diagnostics"
                if project_root is not None
                else self._global_db_path.parent / "diagnostics"
            )
            pack = create_diagnostic_pack(
                output_dir=output_dir,
                global_db_path=self._global_db_path,
                project_root=project_root,
                project_schema_version=(
                    current.schema_version if current is not None else None
                ),
                job_summary=jobs,
                capability_status={
                    "sidecar": "ready",
                    "sqlite": "ready",
                },
                log_path=log_path if log_path.is_file() else None,
            )
            self._logger().write(
                "info",
                "diagnostic pack created",
                fields={"path": pack.path, "includes": pack.includes},
            )
            self._emit(success_response(request.id, pack.as_dict()))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_fs(self, request: Request) -> None:
        method = request.method
        params = request.params
        current = self._workspace.current
        if current is None:
            raise ValueError("no project is open")
        root = Path(current.root_path)

        if method == "fs.resolve":
            relative = params.get("relative")
            if not isinstance(relative, str):
                raise ValueError("relative must be a string")
            resolved = resolve_project_path(root, relative)
            self._emit(
                success_response(
                    request.id,
                    {
                        "relative": to_project_relative(root, resolved),
                        "exists": resolved.exists(),
                        "is_file": resolved.is_file(),
                        "is_dir": resolved.is_dir(),
                    },
                )
            )
            return

        if method == "fs.hash":
            relative = params.get("relative")
            if not isinstance(relative, str):
                raise ValueError("relative must be a string")
            resolved = resolve_project_path(root, relative)
            digest = file_sha256(resolved)
            self._emit(
                success_response(
                    request.id,
                    {
                        "relative": to_project_relative(root, resolved),
                        "sha256": digest,
                        "size_bytes": resolved.stat().st_size,
                    },
                )
            )
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_env(self, request: Request) -> None:
        method = request.method
        params = request.params
        project_root = self._project_root()
        global_env_path = self._global_env_path()

        if method == "env.summary":
            keys = params.get("keys")
            if keys is not None:
                if not isinstance(keys, list) or not all(
                    isinstance(item, str) for item in keys
                ):
                    raise ValueError("keys must be a string array")
            bindings = summarize_env(
                project_root=project_root,
                global_env_path=global_env_path,
                keys=keys,
            )
            self._emit(
                success_response(
                    request.id,
                    {
                        "bindings": [
                            {
                                "key": item.key,
                                "source": item.source,
                                "is_secret": item.is_secret,
                                "set": item.set,
                            }
                            for item in bindings
                        ]
                    },
                )
            )
            return

        if method == "env.resolve":
            # Only return explicitly allowed keys — never dump full environment.
            allow_keys = params.get("allow_keys")
            if not isinstance(allow_keys, list) or not allow_keys:
                raise ValueError("allow_keys must be a non-empty string array")
            if not all(isinstance(item, str) for item in allow_keys):
                raise ValueError("allow_keys must be a string array")
            values = resolve_task_env(
                project_root=project_root,
                global_env_path=global_env_path,
                allow_keys=allow_keys,
            )
            self._emit(success_response(request.id, {"values": values}))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_snapshot(self, request: Request) -> None:
        method = request.method
        params = request.params
        current = self._workspace.current
        if current is None:
            raise ValueError("no project is open")
        root = Path(current.root_path)

        if method == "snapshot.create":
            reason = params.get("reason", "manual")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("reason must be a non-empty string")
            info = create_db_snapshot(root, reason=reason.strip())
            self._emit(success_response(request.id, info.as_dict()))
            return

        if method == "snapshot.list":
            items = list_snapshots(root)
            self._emit(
                success_response(
                    request.id, {"snapshots": [item.as_dict() for item in items]}
                )
            )
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _count(self, request: Request) -> None:
        steps = self._bounded_int(request.params, "steps", 1, 10_000, 5)
        delay_ms = self._bounded_int(request.params, "delay_ms", 0, 10_000, 25)
        for current in range(1, steps + 1):
            self._emit(
                event(
                    "request.progress",
                    {"request_id": request.id, "current": current, "total": steps},
                )
            )
            await asyncio.sleep(delay_ms / 1000)
        self._emit(success_response(request.id, {"completed_steps": steps}))

    async def _cancel(self, request: Request) -> None:
        target_id = request.params.get("request_id")
        if not isinstance(target_id, str) or not target_id:
            self._emit(
                error_response(
                    request.id, "INVALID_PARAMS", "request_id must be a non-empty string"
                )
            )
            return

        target = self._active.get(target_id)
        cancelled = target is not None and not target.done()
        if cancelled:
            target.cancel()
            await asyncio.sleep(0)
        self._emit(
            success_response(
                request.id, {"request_id": target_id, "cancelled": cancelled}
            )
        )

    @staticmethod
    def _bounded_int(
        params: Message, key: str, minimum: int, maximum: int, default: int
    ) -> int:
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if value < minimum or value > maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value
