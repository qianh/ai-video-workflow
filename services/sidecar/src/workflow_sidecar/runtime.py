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
from .persistence.characters import CharacterService
from .persistence.creative_packs import CreativePackService
from .persistence.drafts import DraftService
from .persistence.episode_scripts import EpisodeScriptService
from .persistence.generation import GenerationService
from .persistence.identity_packs import IdentityPackService
from .persistence.locations import LocationService
from .persistence.story import StoryService
from .persistence.story_package import StoryPackageService
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

        if request.method.startswith("draft.") or request.method.startswith(
            "revision."
        ):
            await self._execute_draft(request)
            return

        if request.method.startswith("generation."):
            await self._execute_generation(request)
            return

        if (
            request.method.startswith("world.")
            or request.method.startswith("season.")
            or request.method.startswith("package.")
        ):
            await self._execute_story_package(request)
            return

        if request.method.startswith("script.") or request.method.startswith(
            "episode."
        ):
            await self._execute_episode_script(request)
            return

        if (
            request.method.startswith("character.")
            or request.method.startswith("relationship.")
            or request.method.startswith("voice.")
        ):
            await self._execute_characters(request)
            return

        if request.method.startswith("identity."):
            await self._execute_identity(request)
            return

        if (
            request.method.startswith("location.")
            or request.method.startswith("prop.")
            or request.method.startswith("spatial.")
        ):
            await self._execute_locations(request)
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

    def _drafts(self) -> DraftService:
        self._workspace.require_project_db()
        return DraftService(self._workspace.require_project_db())

    def _generation(self) -> GenerationService:
        self._workspace.require_project_db()
        return GenerationService(self._workspace.require_project_db())

    def _story_packages(self) -> StoryPackageService:
        self._workspace.require_project_db()
        return StoryPackageService(self._workspace.require_project_db())

    def _scripts(self) -> EpisodeScriptService:
        self._workspace.require_project_db()
        return EpisodeScriptService(self._workspace.require_project_db())

    def _characters(self) -> CharacterService:
        self._workspace.require_project_db()
        return CharacterService(self._workspace.require_project_db())

    def _identity(self) -> IdentityPackService:
        current = self._workspace.current
        if current is None:
            raise ValueError("no project is open")
        return IdentityPackService(
            self._workspace.require_project_db(),
            Path(current.root_path),
        )

    def _locations(self) -> LocationService:
        self._workspace.require_project_db()
        return LocationService(self._workspace.require_project_db())

    def _resolve_branch_id(self, params: dict[str, Any]) -> str:
        branch_id = params.get("branch_id")
        if branch_id is None:
            return self._story().primary_branch_id()
        if not isinstance(branch_id, str) or not branch_id:
            raise ValueError("branch_id must be a non-empty string")
        return branch_id

    async def _execute_story_package(self, request: Request) -> None:
        svc = self._story_packages()
        method = request.method
        params = request.params

        if method == "world.add_rule":
            category = params.get("category")
            rule_text = params.get("rule_text")
            force_level = params.get("force_level", "soft")
            scope = params.get("scope")
            if not isinstance(category, str) or not isinstance(rule_text, str):
                raise ValueError("category and rule_text must be strings")
            if not isinstance(force_level, str):
                raise ValueError("force_level must be a string")
            if scope is not None and not isinstance(scope, dict):
                raise ValueError("scope must be an object")
            result = svc.add_world_rule(
                branch_id=self._resolve_branch_id(params),
                category=category,
                rule_text=rule_text,
                force_level=force_level,
                scope=scope,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "world.list_rules":
            result = svc.list_world_rules(self._resolve_branch_id(params))
            self._emit(success_response(request.id, {"rules": result}))
            return

        if method == "world.check_conflicts":
            claims = params.get("claims")
            if not isinstance(claims, list):
                raise ValueError("claims must be an array")
            conflicts = svc.check_hard_rule_conflicts(
                self._resolve_branch_id(params), claims
            )
            self._emit(
                success_response(
                    request.id,
                    {"conflicts": conflicts, "blocked": len(conflicts) > 0},
                )
            )
            return

        if method == "season.add_beat":
            beat_no = params.get("beat_no")
            title = params.get("title")
            summary = params.get("summary")
            story_time = params.get("story_time")
            arc_tag = params.get("arc_tag")
            episode_nos = params.get("episode_nos")
            if isinstance(beat_no, bool) or not isinstance(beat_no, int):
                raise ValueError("beat_no must be an integer")
            if not isinstance(title, str) or not isinstance(summary, str):
                raise ValueError("title and summary must be strings")
            if story_time is not None and not isinstance(story_time, str):
                raise ValueError("story_time must be a string")
            if arc_tag is not None and not isinstance(arc_tag, str):
                raise ValueError("arc_tag must be a string")
            if episode_nos is not None and not isinstance(episode_nos, list):
                raise ValueError("episode_nos must be an array")
            result = svc.add_timeline_beat(
                branch_id=self._resolve_branch_id(params),
                beat_no=beat_no,
                title=title,
                summary=summary,
                story_time=story_time,
                arc_tag=arc_tag,
                episode_nos=episode_nos,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "season.list_beats":
            result = svc.list_timeline(self._resolve_branch_id(params))
            self._emit(success_response(request.id, {"beats": result}))
            return

        if method == "season.ensure_episodes":
            count = params.get("count")
            title_prefix = params.get("title_prefix", "第")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("count must be an integer")
            if not isinstance(title_prefix, str):
                raise ValueError("title_prefix must be a string")
            result = svc.ensure_episodes(
                branch_id=self._resolve_branch_id(params),
                count=count,
                title_prefix=title_prefix,
            )
            self._emit(success_response(request.id, {"episodes": result}))
            return

        if method == "season.list_episodes":
            result = svc.list_episodes(self._resolve_branch_id(params))
            self._emit(success_response(request.id, {"episodes": result}))
            return

        if method == "season.overview":
            result = svc.season_overview(self._resolve_branch_id(params))
            self._emit(success_response(request.id, result))
            return

        if method == "package.create":
            name = params.get("name")
            positioning = params.get("positioning")
            world_rule_ids = params.get("world_rule_ids")
            timeline_beat_ids = params.get("timeline_beat_ids")
            episode_ids = params.get("episode_ids")
            pack_lock_id = params.get("pack_lock_id")
            notes = params.get("notes")
            claims_for_rules = params.get("claims_for_rules")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            if not isinstance(positioning, dict):
                raise ValueError("positioning must be an object")
            if world_rule_ids is not None and not isinstance(world_rule_ids, list):
                raise ValueError("world_rule_ids must be an array")
            if timeline_beat_ids is not None and not isinstance(
                timeline_beat_ids, list
            ):
                raise ValueError("timeline_beat_ids must be an array")
            if episode_ids is not None and not isinstance(episode_ids, list):
                raise ValueError("episode_ids must be an array")
            if pack_lock_id is not None and not isinstance(pack_lock_id, str):
                raise ValueError("pack_lock_id must be a string")
            if notes is not None and not isinstance(notes, str):
                raise ValueError("notes must be a string")
            if claims_for_rules is not None and not isinstance(
                claims_for_rules, list
            ):
                raise ValueError("claims_for_rules must be an array")
            result = svc.create_package_revision(
                branch_id=self._resolve_branch_id(params),
                name=name,
                positioning=positioning,
                world_rule_ids=world_rule_ids,
                timeline_beat_ids=timeline_beat_ids,
                episode_ids=episode_ids,
                pack_lock_id=pack_lock_id,
                notes=notes,
                claims_for_rules=claims_for_rules,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "package.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            result = svc.approve_package_revision(revision_id)
            self._emit(success_response(request.id, result))
            return

        if method == "package.get":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(request.id, svc.get_package_revision(revision_id))
            )
            return

        if method == "package.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = svc.list_package_revisions(limit=limit)
            self._emit(success_response(request.id, {"revisions": result}))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_locations(self, request: Request) -> None:
        svc = self._locations()
        method = request.method
        params = request.params

        if method == "location.create":
            name = params.get("name")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            result = svc.create_location(
                branch_id=self._resolve_branch_id(params),
                name=name,
                location_type=params.get("location_type", "interior"),
                description=params.get("description"),
                is_core=bool(params.get("is_core", False)),
                slug=params.get("slug"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "location.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            branch_id = params.get("branch_id")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = svc.list_locations(branch_id=branch_id, limit=limit)
            self._emit(success_response(request.id, {"locations": result}))
            return

        if method == "location.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(
                    request.id, svc.approve_location_revision(revision_id)
                )
            )
            return

        if method == "location.mark_core":
            location_id = params.get("location_id")
            if not isinstance(location_id, str):
                raise ValueError("location_id must be a string")
            result = svc.mark_core(
                location_id, is_core=bool(params.get("is_core", True))
            )
            self._emit(success_response(request.id, result))
            return

        if method == "location.create_pack":
            location_id = params.get("location_id")
            if not isinstance(location_id, str):
                raise ValueError("location_id must be a string")
            result = svc.create_pack(
                location_id=location_id,
                layout=params.get("layout"),
                direction_axis=params.get("direction_axis"),
                primary_view=params.get("primary_view"),
                camera_angles=params.get("camera_angles"),
                entrances=params.get("entrances"),
                furniture_anchors=params.get("furniture_anchors"),
                day_variant=params.get("day_variant"),
                night_variant=params.get("night_variant"),
                reference_asset_ids=params.get("reference_asset_ids"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "location.update_pack":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            result = svc.update_pack_revision(
                revision_id,
                layout=params.get("layout"),
                direction_axis=params.get("direction_axis"),
                primary_view=params.get("primary_view"),
                camera_angles=params.get("camera_angles"),
                entrances=params.get("entrances"),
                furniture_anchors=params.get("furniture_anchors"),
                day_variant=params.get("day_variant"),
                night_variant=params.get("night_variant"),
                reference_asset_ids=params.get("reference_asset_ids"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "location.validate_pack":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.validate_pack(revision_id)))
            return

        if method == "location.approve_pack":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.approve_pack(revision_id)))
            return

        if method == "location.confirm_pack":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.confirm_pack(revision_id)))
            return

        if method == "location.list_packs":
            location_id = params.get("location_id")
            limit = params.get("limit", 50)
            if location_id is not None and not isinstance(location_id, str):
                raise ValueError("location_id must be a string")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = svc.list_packs(location_id=location_id, limit=limit)
            self._emit(success_response(request.id, {"packs": result}))
            return

        if method == "location.gate":
            location_id = params.get("location_id")
            if not isinstance(location_id, str):
                raise ValueError("location_id must be a string")
            self._emit(
                success_response(request.id, svc.production_gate(location_id))
            )
            return

        if method == "location.overview":
            result = svc.world_overview(self._resolve_branch_id(params))
            self._emit(success_response(request.id, result))
            return

        if method == "spatial.add_link":
            source = params.get("source_location_id")
            target = params.get("target_location_id")
            link_type = params.get("link_type")
            if not all(isinstance(x, str) for x in (source, target, link_type)):
                raise ValueError(
                    "source_location_id, target_location_id and link_type must be strings"
                )
            result = svc.add_spatial_link(
                branch_id=self._resolve_branch_id(params),
                source_location_id=source,
                target_location_id=target,
                link_type=link_type,
                description=params.get("description"),
                bidirectional=bool(params.get("bidirectional", True)),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "spatial.list":
            limit = params.get("limit", 100)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            branch_id = params.get("branch_id")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = svc.list_spatial_links(branch_id=branch_id, limit=limit)
            self._emit(success_response(request.id, {"links": result}))
            return

        if method == "prop.create":
            name = params.get("name")
            appearance = params.get("appearance")
            if not isinstance(name, str) or not isinstance(appearance, str):
                raise ValueError("name and appearance must be strings")
            result = svc.create_prop(
                branch_id=self._resolve_branch_id(params),
                name=name,
                appearance=appearance,
                owner_character_id=params.get("owner_character_id"),
                state_notes=params.get("state_notes"),
                is_key_prop=bool(params.get("is_key_prop", True)),
                slug=params.get("slug"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "prop.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            branch_id = params.get("branch_id")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = svc.list_props(branch_id=branch_id, limit=limit)
            self._emit(success_response(request.id, {"props": result}))
            return

        if method == "prop.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(request.id, svc.approve_prop_revision(revision_id))
            )
            return

        if method == "location.anchor_prop":
            revision_id = params.get("revision_id")
            prop_id = params.get("prop_id")
            anchor_label = params.get("anchor_label")
            if not all(
                isinstance(x, str) for x in (revision_id, prop_id, anchor_label)
            ):
                raise ValueError(
                    "revision_id, prop_id and anchor_label must be strings"
                )
            result = svc.anchor_prop(
                location_pack_revision_id=revision_id,
                prop_id=prop_id,
                anchor_label=anchor_label,
                position=params.get("position"),
                visibility=params.get("visibility", "visible"),
            )
            self._emit(success_response(request.id, result))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_identity(self, request: Request) -> None:
        svc = self._identity()
        method = request.method
        params = request.params

        if method == "identity.create":
            character_id = params.get("character_id")
            if not isinstance(character_id, str):
                raise ValueError("character_id must be a string")
            result = svc.create_pack(
                character_id=character_id,
                positive_prompt=params.get("positive_prompt", ""),
                negative_prompt=params.get("negative_prompt", ""),
                height_cm=params.get("height_cm"),
                proportion_notes=params.get("proportion_notes"),
                voice_profile_id=params.get("voice_profile_id"),
                multi_view_asset_ids=params.get("multi_view_asset_ids"),
                shot_size_asset_ids=params.get("shot_size_asset_ids"),
                expression_asset_ids=params.get("expression_asset_ids"),
                outfit_asset_ids=params.get("outfit_asset_ids"),
                reference_priority=params.get("reference_priority"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "identity.get":
            pack_id = params.get("pack_id")
            if not isinstance(pack_id, str):
                raise ValueError("pack_id must be a string")
            self._emit(success_response(request.id, svc.get_pack(pack_id)))
            return

        if method == "identity.list":
            character_id = params.get("character_id")
            limit = params.get("limit", 50)
            if character_id is not None and not isinstance(character_id, str):
                raise ValueError("character_id must be a string")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = svc.list_packs(character_id=character_id, limit=limit)
            self._emit(success_response(request.id, {"packs": result}))
            return

        if method == "identity.update":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            result = svc.update_revision(
                revision_id,
                positive_prompt=params.get("positive_prompt"),
                negative_prompt=params.get("negative_prompt"),
                height_cm=params.get("height_cm"),
                proportion_notes=params.get("proportion_notes"),
                voice_profile_id=params.get("voice_profile_id"),
                multi_view_asset_ids=params.get("multi_view_asset_ids"),
                shot_size_asset_ids=params.get("shot_size_asset_ids"),
                expression_asset_ids=params.get("expression_asset_ids"),
                outfit_asset_ids=params.get("outfit_asset_ids"),
                reference_priority=params.get("reference_priority"),
                notes=params.get("notes"),
                clear_voice=bool(params.get("clear_voice", False)),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "identity.generate_looks":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            count = params.get("count", 3)
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("count must be an integer")
            result = svc.generate_looks(
                revision_id,
                count=count,
                prompt_override=params.get("prompt_override"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "identity.select_look":
            candidate_id = params.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise ValueError("candidate_id must be a string")
            self._emit(success_response(request.id, svc.select_look(candidate_id)))
            return

        if method == "identity.validate":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.validate(revision_id)))
            return

        if method == "identity.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.approve(revision_id)))
            return

        if method == "identity.confirm":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(success_response(request.id, svc.confirm(revision_id)))
            return

        if method == "identity.gate":
            character_id = params.get("character_id")
            if not isinstance(character_id, str):
                raise ValueError("character_id must be a string")
            self._emit(
                success_response(request.id, svc.production_gate(character_id))
            )
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_characters(self, request: Request) -> None:
        svc = self._characters()
        method = request.method
        params = request.params

        if method == "character.create":
            name = params.get("name")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            result = svc.create_character(
                branch_id=self._resolve_branch_id(params),
                name=name,
                role=params.get("role", "supporting"),
                age_feel=params.get("age_feel"),
                body_type=params.get("body_type"),
                appearance_rules=params.get("appearance_rules"),
                personality=params.get("personality"),
                goals=params.get("goals"),
                immutable_traits=params.get("immutable_traits"),
                slug=params.get("slug"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "character.get":
            character_id = params.get("character_id")
            if not isinstance(character_id, str):
                raise ValueError("character_id must be a string")
            self._emit(success_response(request.id, svc.get_character(character_id)))
            return

        if method == "character.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            branch_id = params.get("branch_id")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = svc.list_characters(branch_id=branch_id, limit=limit)
            self._emit(success_response(request.id, {"characters": result}))
            return

        if method == "character.update_revision":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            result = svc.update_character_revision(
                revision_id,
                name=params.get("name"),
                role=params.get("role"),
                age_feel=params.get("age_feel"),
                body_type=params.get("body_type"),
                appearance_rules=params.get("appearance_rules"),
                personality=params.get("personality"),
                goals=params.get("goals"),
                immutable_traits=params.get("immutable_traits"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "character.validate":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(
                    request.id, svc.validate_character_revision(revision_id)
                )
            )
            return

        if method == "character.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(
                    request.id, svc.approve_character_revision(revision_id)
                )
            )
            return

        if method == "relationship.create":
            source = params.get("source_character_id")
            target = params.get("target_character_id")
            rel_type = params.get("relationship_type")
            description = params.get("description")
            if not all(isinstance(x, str) for x in (source, target, rel_type, description)):
                raise ValueError(
                    "source_character_id, target_character_id, "
                    "relationship_type and description must be strings"
                )
            result = svc.create_relationship(
                branch_id=self._resolve_branch_id(params),
                source_character_id=source,
                target_character_id=target,
                relationship_type=rel_type,
                description=description,
                story_time_from=params.get("story_time_from"),
                story_time_to=params.get("story_time_to"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "relationship.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            branch_id = params.get("branch_id")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = svc.list_relationships(branch_id=branch_id, limit=limit)
            self._emit(success_response(request.id, {"relationships": result}))
            return

        if method == "relationship.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(
                    request.id, svc.approve_relationship_revision(revision_id)
                )
            )
            return

        if method == "voice.create":
            result = svc.create_voice_profile(
                character_id=params.get("character_id"),
                label=params.get("label"),
                engine_adapter_id=params.get("engine_adapter_id", "local-tts"),
                speed=params.get("speed", 1.0),
                emotion_range=params.get("emotion_range"),
                pronunciation_rules=params.get("pronunciation_rules"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "voice.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            character_id = params.get("character_id")
            if character_id is not None and not isinstance(character_id, str):
                raise ValueError("character_id must be a string")
            result = svc.list_voice_profiles(
                character_id=character_id, limit=limit
            )
            self._emit(success_response(request.id, {"voice_profiles": result}))
            return

        if method == "voice.approve":
            revision_id = params.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("revision_id must be a string")
            self._emit(
                success_response(request.id, svc.approve_voice_revision(revision_id))
            )
            return

        if method == "character.overview":
            result = svc.continuity_overview(self._resolve_branch_id(params))
            self._emit(success_response(request.id, result))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_episode_script(self, request: Request) -> None:
        svc = self._scripts()
        method = request.method
        params = request.params

        if method == "episode.get":
            episode_id = params.get("episode_id")
            if not isinstance(episode_id, str):
                raise ValueError("episode_id must be a string")
            self._emit(success_response(request.id, svc.get_episode(episode_id)))
            return

        if method == "episode.update_title":
            episode_id = params.get("episode_id")
            title = params.get("title")
            if not isinstance(episode_id, str) or not isinstance(title, str):
                raise ValueError("episode_id and title must be strings")
            self._emit(
                success_response(
                    request.id, svc.update_episode_title(episode_id, title)
                )
            )
            return

        if method == "script.create":
            episode_id = params.get("episode_id")
            if not isinstance(episode_id, str):
                raise ValueError("episode_id must be a string")
            result = svc.create_script(
                episode_id=episode_id,
                title=params.get("title"),
                goal=params.get("goal", ""),
                main_conflict=params.get("main_conflict", ""),
                twist=params.get("twist"),
                opening_hook=params.get("opening_hook", ""),
                ending_hook=params.get("ending_hook", ""),
                estimated_duration_ms=params.get("estimated_duration_ms"),
                notes=params.get("notes"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.update":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            result = svc.update_script(
                script_id,
                title=params.get("title"),
                goal=params.get("goal"),
                main_conflict=params.get("main_conflict"),
                twist=params.get("twist"),
                opening_hook=params.get("opening_hook"),
                ending_hook=params.get("ending_hook"),
                estimated_duration_ms=params.get("estimated_duration_ms"),
                notes=params.get("notes"),
                clear_twist=bool(params.get("clear_twist", False)),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.get":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(success_response(request.id, svc.get_script(script_id)))
            return

        if method == "script.tree":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(success_response(request.id, svc.get_script_tree(script_id)))
            return

        if method == "script.list":
            episode_id = params.get("episode_id")
            limit = params.get("limit", 50)
            if episode_id is not None and not isinstance(episode_id, str):
                raise ValueError("episode_id must be a string")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = svc.list_scripts(episode_id=episode_id, limit=limit)
            self._emit(success_response(request.id, {"scripts": result}))
            return

        if method == "script.add_scene":
            script_id = params.get("script_id")
            purpose = params.get("purpose")
            action_text = params.get("action_text")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            if not isinstance(purpose, str) or not isinstance(action_text, str):
                raise ValueError("purpose and action_text must be strings")
            result = svc.add_scene(
                script_revision_id=script_id,
                scene_no=params.get("scene_no"),
                purpose=purpose,
                action_text=action_text,
                time_of_day=params.get("time_of_day", "night"),
                location_ref=params.get("location_ref"),
                story_time_start=params.get("story_time_start"),
                estimated_duration_ms=params.get("estimated_duration_ms"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.update_scene":
            scene_id = params.get("scene_id")
            if not isinstance(scene_id, str):
                raise ValueError("scene_id must be a string")
            result = svc.update_scene(
                scene_id,
                purpose=params.get("purpose"),
                action_text=params.get("action_text"),
                time_of_day=params.get("time_of_day"),
                location_ref=params.get("location_ref"),
                story_time_start=params.get("story_time_start"),
                estimated_duration_ms=params.get("estimated_duration_ms"),
                clear_location=bool(params.get("clear_location", False)),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.list_scenes":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(
                success_response(
                    request.id, {"scenes": svc.list_scenes(script_id)}
                )
            )
            return

        if method == "script.add_dialogue":
            scene_id = params.get("scene_id")
            text = params.get("text")
            if not isinstance(scene_id, str) or not isinstance(text, str):
                raise ValueError("scene_id and text must be strings")
            result = svc.add_dialogue(
                scene_revision_id=scene_id,
                text=text,
                line_type=params.get("line_type", "dialogue"),
                speaker_name=params.get("speaker_name"),
                emotion=params.get("emotion"),
                action_intent=params.get("action_intent"),
                pronunciation=params.get("pronunciation"),
                sort_order=params.get("sort_order"),
                estimated_duration_ms=params.get("estimated_duration_ms"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.revise_dialogue":
            line_id = params.get("line_id")
            if not isinstance(line_id, str):
                raise ValueError("line_id must be a string")
            result = svc.revise_dialogue(
                line_id,
                text=params.get("text"),
                speaker_name=params.get("speaker_name"),
                line_type=params.get("line_type"),
                emotion=params.get("emotion"),
                action_intent=params.get("action_intent"),
                pronunciation=params.get("pronunciation"),
                sort_order=params.get("sort_order"),
                estimated_duration_ms=params.get("estimated_duration_ms"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.list_dialogue":
            scene_id = params.get("scene_id")
            script_id = params.get("script_id")
            if scene_id is not None and not isinstance(scene_id, str):
                raise ValueError("scene_id must be a string")
            if script_id is not None and not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            result = svc.list_dialogue(
                scene_revision_id=scene_id, script_revision_id=script_id
            )
            self._emit(success_response(request.id, {"lines": result}))
            return

        if method == "script.add_hook":
            script_id = params.get("script_id")
            hook_type = params.get("hook_type")
            text = params.get("text")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            if not isinstance(hook_type, str) or not isinstance(text, str):
                raise ValueError("hook_type and text must be strings")
            result = svc.add_hook(
                script_revision_id=script_id,
                hook_type=hook_type,
                text=text,
                position_scene_no=params.get("position_scene_no"),
                sort_order=params.get("sort_order"),
            )
            self._emit(success_response(request.id, result))
            return

        if method == "script.list_hooks":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(
                success_response(request.id, {"hooks": svc.list_hooks(script_id)})
            )
            return

        if method == "script.validate":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(success_response(request.id, svc.validate_script(script_id)))
            return

        if method == "script.approve":
            script_id = params.get("script_id")
            if not isinstance(script_id, str):
                raise ValueError("script_id must be a string")
            self._emit(success_response(request.id, svc.approve_script(script_id)))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_generation(self, request: Request) -> None:
        gen = self._generation()
        method = request.method
        params = request.params

        if method == "generation.create":
            title = params.get("title")
            schema_id = params.get("schema_id")
            intent = params.get("intent") or {}
            target_type = params.get("target_type", "episode_outline")
            target_id = params.get("target_id")
            branch_id = params.get("branch_id")
            pack_lock_id = params.get("pack_lock_id")
            pack_lock_hash = params.get("pack_lock_hash")
            if not isinstance(title, str) or not isinstance(schema_id, str):
                raise ValueError("title and schema_id must be strings")
            if not isinstance(intent, dict):
                raise ValueError("intent must be an object")
            if branch_id is None:
                branch_id = self._story().primary_branch_id()
            # Attach current pack lock if present.
            if pack_lock_id is None:
                lock = self._packs().current_lock()
                if lock is not None:
                    pack_lock_id = lock["id"]
                    pack_lock_hash = lock["composition_content_hash"]
            result = gen.create_run(
                title=title,
                schema_id=schema_id,
                intent=intent,
                target_type=str(target_type),
                target_id=target_id if isinstance(target_id, str) else None,
                branch_id=branch_id if isinstance(branch_id, str) else None,
                pack_lock_id=pack_lock_id if isinstance(pack_lock_id, str) else None,
                pack_lock_hash=pack_lock_hash if isinstance(pack_lock_hash, str) else None,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "generation.plan":
            run_id = params.get("run_id")
            plan = params.get("plan")
            if not isinstance(run_id, str):
                raise ValueError("run_id must be a string")
            if plan is not None and not isinstance(plan, dict):
                raise ValueError("plan must be an object")
            result = gen.plan(run_id, plan=plan)
            self._emit(success_response(request.id, result))
            return

        if method == "generation.execute":
            run_id = params.get("run_id")
            output = params.get("output")
            executor = params.get("executor", "stub.structured")
            if not isinstance(run_id, str):
                raise ValueError("run_id must be a string")
            if not isinstance(output, dict):
                raise ValueError("output must be an object")
            if not isinstance(executor, str):
                raise ValueError("executor must be a string")
            result = gen.execute(run_id, output=output, executor=executor)
            self._emit(success_response(request.id, result))
            return

        if method == "generation.review":
            run_id = params.get("run_id")
            verdict = params.get("verdict")
            findings = params.get("findings") or []
            if not isinstance(run_id, str) or not isinstance(verdict, str):
                raise ValueError("run_id and verdict must be strings")
            if not isinstance(findings, list):
                raise ValueError("findings must be an array")
            result = gen.review(run_id, verdict=verdict, findings=findings)
            self._emit(success_response(request.id, result))
            return

        if method == "generation.accept_human":
            run_id = params.get("run_id")
            reason = params.get("reason")
            if not isinstance(run_id, str) or not isinstance(reason, str):
                raise ValueError("run_id and reason must be strings")
            result = gen.accept_human_review(run_id, reason=reason)
            self._emit(success_response(request.id, result))
            return

        if method == "generation.open_draft_gate":
            run_id = params.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError("run_id must be a string")
            result = gen.open_draft_gate(run_id)
            self._emit(success_response(request.id, result))
            return

        if method == "generation.get":
            run_id = params.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError("run_id must be a string")
            self._emit(success_response(request.id, gen.get_run(run_id)))
            return

        if method == "generation.history":
            run_id = params.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError("run_id must be a string")
            self._emit(success_response(request.id, gen.get_history(run_id)))
            return

        if method == "generation.list":
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            self._emit(
                success_response(
                    request.id, {"runs": gen.list_runs(limit=limit)}
                )
            )
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _execute_draft(self, request: Request) -> None:
        drafts = self._drafts()
        method = request.method
        params = request.params

        if method == "draft.list_schemas":
            self._emit(
                success_response(request.id, {"schemas": drafts.list_schemas()})
            )
            return

        if method == "draft.create":
            schema_id = params.get("schema_id")
            title = params.get("title")
            payload = params.get("payload")
            target_type = params.get("target_type", "generic")
            target_id = params.get("target_id")
            branch_id = params.get("branch_id")
            if not isinstance(schema_id, str) or not isinstance(title, str):
                raise ValueError("schema_id and title must be strings")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            if not isinstance(target_type, str):
                raise ValueError("target_type must be a string")
            if target_id is not None and not isinstance(target_id, str):
                raise ValueError("target_id must be a string")
            if branch_id is None:
                # Default drafts to primary production branch.
                branch_id = self._story().primary_branch_id()
            elif not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            result = drafts.create(
                schema_id=schema_id,
                title=title,
                payload=payload,
                target_type=target_type,
                target_id=target_id,
                branch_id=branch_id,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "draft.update":
            draft_id = params.get("draft_id")
            payload = params.get("payload")
            title = params.get("title")
            if not isinstance(draft_id, str):
                raise ValueError("draft_id must be a string")
            if payload is not None and not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            if title is not None and not isinstance(title, str):
                raise ValueError("title must be a string")
            result = drafts.update(draft_id, payload=payload, title=title)
            self._emit(success_response(request.id, result))
            return

        if method == "draft.validate":
            draft_id = params.get("draft_id")
            if not isinstance(draft_id, str):
                raise ValueError("draft_id must be a string")
            result = drafts.validate(draft_id)
            self._emit(success_response(request.id, result))
            return

        if method == "draft.promote":
            draft_id = params.get("draft_id")
            if not isinstance(draft_id, str):
                raise ValueError("draft_id must be a string")
            primary = self._story().primary_branch_id()
            result = drafts.promote(
                draft_id,
                require_primary_branch=True,
                primary_branch_id=primary,
            )
            self._emit(success_response(request.id, result))
            return

        if method == "draft.get":
            draft_id = params.get("draft_id")
            if not isinstance(draft_id, str):
                raise ValueError("draft_id must be a string")
            self._emit(success_response(request.id, drafts.get(draft_id)))
            return

        if method == "draft.list":
            status = params.get("status")
            limit = params.get("limit", 50)
            if status is not None and not isinstance(status, str):
                raise ValueError("status must be a string")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = drafts.list_drafts(status=status, limit=limit)
            self._emit(success_response(request.id, {"drafts": result}))
            return

        if method == "revision.list":
            target_type = params.get("target_type")
            limit = params.get("limit", 50)
            if target_type is not None and not isinstance(target_type, str):
                raise ValueError("target_type must be a string")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            result = drafts.list_revisions(target_type=target_type, limit=limit)
            self._emit(success_response(request.id, {"revisions": result}))
            return

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

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
            branch_id = params.get("branch_id")
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
            if branch_id is not None and not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            event_view = story.create_event(
                title=title,
                summary=summary,
                order_key=float(order_key),
                origin=origin,
                story_source_id=story_source_id,
                char_start=char_start,
                char_end=char_end,
                confidence=float(confidence),
                branch_id=branch_id,
            )
            self._emit(success_response(request.id, event_view.as_dict()))
            return

        if method == "story.list_events":
            branch_id = params.get("branch_id")
            if branch_id is not None and not isinstance(branch_id, str):
                raise ValueError("branch_id must be a string")
            events = story.list_events(branch_id)
            self._emit(
                success_response(
                    request.id, {"events": [item.as_dict() for item in events]}
                )
            )
            return

        if method == "story.list_branches":
            self._emit(
                success_response(
                    request.id, {"branches": story.list_branches()}
                )
            )
            return

        if method == "story.create_branch":
            name = params.get("name")
            status = params.get("status", "exploring")
            parent_branch_id = params.get("parent_branch_id")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            if not isinstance(status, str):
                raise ValueError("status must be a string")
            if parent_branch_id is not None and not isinstance(parent_branch_id, str):
                raise ValueError("parent_branch_id must be a string")
            branch = story.create_branch(
                name=name, status=status, parent_branch_id=parent_branch_id
            )
            self._emit(success_response(request.id, branch))
            return

        if method == "story.fork_branch":
            from_branch_id = params.get("from_branch_id")
            name = params.get("name")
            if not isinstance(from_branch_id, str) or not isinstance(name, str):
                raise ValueError("from_branch_id and name must be strings")
            branch = story.fork_branch(from_branch_id=from_branch_id, name=name)
            self._emit(success_response(request.id, branch))
            return

        if method == "story.set_primary":
            branch_id = params.get("branch_id")
            if not isinstance(branch_id, str) or not branch_id:
                raise ValueError("branch_id must be a non-empty string")
            branch = story.set_primary(branch_id)
            self._emit(success_response(request.id, branch))
            return

        if method == "story.archive_branch":
            branch_id = params.get("branch_id")
            if not isinstance(branch_id, str) or not branch_id:
                raise ValueError("branch_id must be a non-empty string")
            branch = story.archive_branch(branch_id)
            self._emit(success_response(request.id, branch))
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
