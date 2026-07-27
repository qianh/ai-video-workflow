"""Approval gates for story package and look/asset confirmation (M2-14).

Gate types (M2 scope):
- story_package
- identity_and_locations

Each gate snapshots a target revision set hash. After confirmation, any
change to the live target set invalidates the gate.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .characters import CharacterService
from .database import Database
from .director import DirectorService
from .identity_packs import IdentityPackService
from .locations import LocationService
from .story import StoryService
from .story_package import StoryPackageService
from .timeutil import utc_now

GATE_TYPES = frozenset({"story_package", "identity_and_locations"})
GATE_STATUSES = frozenset({"pending", "confirmed", "invalidated"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class GateService:
    def __init__(self, db: Database, project_root: Path | None = None) -> None:
        self._db = db
        self._root = Path(project_root) if project_root else None
        self._story_packages = StoryPackageService(db)
        self._characters = CharacterService(db)
        self._identity = IdentityPackService(
            db, self._root or Path(".")
        )
        self._locations = LocationService(db)
        self._director = DirectorService(db)

    # --- public API ---

    def evaluate(
        self, *, branch_id: str, gate_type: str
    ) -> dict[str, Any]:
        if gate_type not in GATE_TYPES:
            raise ValueError(f"gate_type must be one of {sorted(GATE_TYPES)}")

        target_set, blockers = self._build_target_set(branch_id, gate_type)
        target_hash = _hash(target_set)
        now = utc_now()

        # Invalidate previous confirmed gates whose hash no longer matches.
        confirmed = self._latest_gate(branch_id, gate_type, status="confirmed")
        if confirmed is not None and confirmed["confirmed_hash"] != target_hash:
            self._db.execute(
                """
                UPDATE approval_gates
                SET status = 'invalidated',
                    invalidated_at = ?,
                    invalidate_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    "target revision set changed after confirmation",
                    now,
                    confirmed["id"],
                ),
            )
            self._db.commit()

        # Upsert pending gate for current target hash.
        existing_pending = self._db.fetchone(
            """
            SELECT id FROM approval_gates
            WHERE branch_id = ? AND gate_type = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (branch_id, gate_type),
        )
        ready = len(blockers) == 0
        if existing_pending is not None:
            gate_id = existing_pending["id"]
            self._db.execute(
                """
                UPDATE approval_gates
                SET target_set_json = ?, target_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (_stable_json(target_set), target_hash, now, gate_id),
            )
            self._db.commit()
        else:
            # If already confirmed with same hash, return that gate.
            if (
                confirmed is not None
                and confirmed["confirmed_hash"] == target_hash
            ):
                gate = self.get_gate(confirmed["id"])
                return self._enrich(gate, target_set, blockers, ready=True)

            gate_id = str(uuid.uuid4())
            self._db.execute(
                """
                INSERT INTO approval_gates(
                    id, branch_id, gate_type, status, target_set_json,
                    target_hash, confirmed_hash, confirmation_note,
                    confirmed_at, invalidated_at, invalidate_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    gate_id,
                    branch_id,
                    gate_type,
                    _stable_json(target_set),
                    target_hash,
                    now,
                    now,
                ),
            )
            self._db.commit()

        gate = self.get_gate(gate_id)
        return self._enrich(gate, target_set, blockers, ready=ready)

    def confirm(
        self,
        gate_id: str,
        *,
        confirmation_note: str | None = None,
    ) -> dict[str, Any]:
        gate = self.get_gate(gate_id)
        if gate["status"] == "confirmed":
            # Re-validate still matches live targets.
            live = self.evaluate(
                branch_id=gate["branch_id"], gate_type=gate["gate_type"]
            )
            if live["status"] == "confirmed" and live["valid"]:
                return live
            raise ValueError("gate was invalidated; re-evaluate and confirm again")
        if gate["status"] != "pending":
            raise ValueError(f"cannot confirm status: {gate['status']}")

        target_set, blockers = self._build_target_set(
            gate["branch_id"], gate["gate_type"]
        )
        live_hash = _hash(target_set)
        if live_hash != gate["target_hash"]:
            # Refresh pending targets then fail if still blocked.
            refreshed = self.evaluate(
                branch_id=gate["branch_id"], gate_type=gate["gate_type"]
            )
            if refreshed["blockers"]:
                raise ValueError(
                    f"gate not ready: {refreshed['blockers'][0]['message']}"
                )
            gate = refreshed
            live_hash = gate["target_hash"]
            target_set = gate["target_set"]
            blockers = gate["blockers"]

        if blockers:
            raise ValueError(f"gate not ready: {blockers[0]['message']}")

        now = utc_now()
        self._db.execute(
            """
            UPDATE approval_gates
            SET status = 'confirmed',
                confirmed_hash = ?,
                target_set_json = ?,
                target_hash = ?,
                confirmation_note = ?,
                confirmed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                live_hash,
                _stable_json(target_set),
                live_hash,
                confirmation_note,
                now,
                now,
                gate["id"] if "id" in gate else gate_id,
            ),
        )
        self._db.commit()
        confirmed = self.get_gate(gate["id"] if "id" in gate else gate_id)
        return self._enrich(confirmed, target_set, [], ready=True)

    def get_gate(self, gate_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM approval_gates WHERE id = ?", (gate_id,)
        )
        if row is None:
            raise ValueError(f"approval gate not found: {gate_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "gate_type": row["gate_type"],
            "status": row["status"],
            "target_set": json.loads(row["target_set_json"]),
            "target_hash": row["target_hash"],
            "confirmed_hash": row["confirmed_hash"],
            "confirmation_note": row["confirmation_note"],
            "confirmed_at": row["confirmed_at"],
            "invalidated_at": row["invalidated_at"],
            "invalidate_reason": row["invalidate_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_gates(
        self, *, branch_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._db.fetchall(
            """
            SELECT id FROM approval_gates
            WHERE branch_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (branch_id, limit),
        )
        return [self.get_gate(row["id"]) for row in rows]

    def status(self, *, branch_id: str) -> dict[str, Any]:
        """Evaluate both M2 gates and report production readiness."""

        story = self.evaluate(branch_id=branch_id, gate_type="story_package")
        looks = self.evaluate(
            branch_id=branch_id, gate_type="identity_and_locations"
        )
        # After evaluate, re-read confirmed validity.
        story = self._refresh_valid(story)
        looks = self._refresh_valid(looks)
        ready = (
            story["status"] == "confirmed"
            and story["valid"]
            and looks["status"] == "confirmed"
            and looks["valid"]
        )
        return {
            "branch_id": branch_id,
            "gates": {
                "story_package": story,
                "identity_and_locations": looks,
            },
            "ready_for_batch_production": ready,
            "required_gates": sorted(GATE_TYPES),
            "note": (
                "M2 gates cover story package + identity/locations only; "
                "storyboard and rough-cut gates are M3/M4."
            ),
        }

    def bootstrap_trial(
        self,
        *,
        branch_id: str,
        story_root: Path | None = None,
    ) -> dict[str, Any]:
        """Create a minimal confirmable trial project slice (M2-15 automation)."""

        root = story_root or self._root or Path(".")
        packages = StoryPackageService(self._db)
        characters = CharacterService(self._db)
        identity = IdentityPackService(self._db, root)
        locations = LocationService(self._db)
        director = DirectorService(self._db)

        # Episodes + package
        episodes = packages.ensure_episodes(branch_id=branch_id, count=3)
        hard = packages.add_world_rule(
            branch_id=branch_id,
            category="continuity",
            rule_text="forbid:时间旅行",
            force_level="hard",
        )
        soft = packages.add_world_rule(
            branch_id=branch_id,
            category="tone",
            rule_text="保持冷色夜市氛围",
            force_level="soft",
        )
        beat1 = packages.add_timeline_beat(
            branch_id=branch_id,
            beat_no=1,
            title="发现",
            summary="雨夜捡到发光 U 盘",
            arc_tag="setup",
            episode_nos=[1],
        )
        beat2 = packages.add_timeline_beat(
            branch_id=branch_id,
            beat_no=2,
            title="追索",
            summary="追查失踪消息",
            arc_tag="rising",
            episode_nos=[2, 3],
        )
        pkg = packages.create_package_revision(
            branch_id=branch_id,
            name="试验项目故事包",
            positioning={"theme": "都市悬疑", "audience": "短剧"},
            world_rule_ids=[hard["id"], soft["id"]],
            timeline_beat_ids=[beat1["id"], beat2["id"]],
            episode_ids=[e["id"] for e in episodes],
            notes="M2-15 trial bootstrap",
            claims_for_rules=["雨夜追逐"],
        )
        packages.approve_package_revision(pkg["id"])

        # Character + identity look
        hero = characters.create_character(
            branch_id=branch_id,
            name="阿宁",
            role="protagonist",
            appearance_rules="短发雨衣冷色调",
            personality=["冷静", "好奇"],
            goals="查清失踪真相",
            immutable_traits=["左眉疤"],
        )
        characters.approve_character_revision(hero["current_revision"]["id"])
        pack = identity.create_pack(
            character_id=hero["id"],
            positive_prompt="cold-tone night market girl, short hair, raincoat",
            negative_prompt="blurry",
            height_cm=165,
        )
        rev_id = pack["current_revision"]["id"]
        looks = identity.generate_looks(rev_id, count=2)
        identity.select_look(looks["candidates"][0]["id"])
        identity.confirm(rev_id)

        # Core location pack
        market = locations.create_location(
            branch_id=branch_id,
            name="夜市东口",
            location_type="exterior",
            is_core=True,
            description="雨夜摊位",
        )
        locations.approve_location_revision(market["current_revision"]["id"])
        loc_pack = locations.create_pack(
            location_id=market["id"],
            layout={"zones": ["stalls", "entrance"]},
            direction_axis="east-west",
            primary_view="entrance",
            camera_angles=["wide"],
            night_variant={"rain": True},
            day_variant={"overcast": True},
        )
        locations.confirm_pack(loc_pack["current_revision"]["id"])

        # Visual bible + director preset (project)
        bible = director.create_visual_bible(
            branch_id=branch_id,
            name="试验视觉圣经",
            style_name="现代国漫半写实",
            payload={
                "line_work": "clean",
                "palette": {"primary": "teal"},
                "forbidden": ["photoreal"],
            },
            locked_fields=["style_name", "forbidden"],
        )
        director.approve_visual_revision(bible["current_revision"]["id"])
        preset = director.create_director_preset(
            branch_id=branch_id,
            name="试验导演预设",
            payload={"motion_intensity": "low", "forbidden_moves": ["whip pan"]},
            locked_fields=["forbidden_moves"],
        )
        director.approve_director_revision(preset["current_revision"]["id"])

        # Confirm both M2 gates
        story_gate = self.evaluate(
            branch_id=branch_id, gate_type="story_package"
        )
        story_gate = self.confirm(
            story_gate["id"], confirmation_note="M2-15 trial story package"
        )
        look_gate = self.evaluate(
            branch_id=branch_id, gate_type="identity_and_locations"
        )
        look_gate = self.confirm(
            look_gate["id"], confirmation_note="M2-15 trial looks and locations"
        )
        status = self.status(branch_id=branch_id)
        return {
            "branch_id": branch_id,
            "package_revision_id": pkg["id"],
            "character_id": hero["id"],
            "location_id": market["id"],
            "gates": status["gates"],
            "ready_for_batch_production": status["ready_for_batch_production"],
            "bootstrap": "trial_m2",
        }

    # --- internals ---

    def _enrich(
        self,
        gate: dict[str, Any],
        target_set: dict[str, Any],
        blockers: list[dict[str, str]],
        *,
        ready: bool,
    ) -> dict[str, Any]:
        valid = True
        if gate["status"] == "confirmed":
            valid = gate.get("confirmed_hash") == _hash(target_set)
        elif gate["status"] == "invalidated":
            valid = False
        result = {
            **gate,
            "target_set": target_set,
            "blockers": blockers,
            "ready": ready and gate["status"] in {"pending", "confirmed"},
            "valid": valid if gate["status"] == "confirmed" else ready,
        }
        return result

    def _refresh_valid(self, gate: dict[str, Any]) -> dict[str, Any]:
        if gate["status"] != "confirmed":
            return gate
        live_set, blockers = self._build_target_set(
            gate["branch_id"], gate["gate_type"]
        )
        live_hash = _hash(live_set)
        if gate.get("confirmed_hash") != live_hash:
            # Force re-evaluate to invalidate.
            return self.evaluate(
                branch_id=gate["branch_id"], gate_type=gate["gate_type"]
            )
        gate = {**gate, "target_set": live_set, "blockers": blockers, "valid": True}
        return gate

    def _latest_gate(
        self, branch_id: str, gate_type: str, *, status: str
    ) -> dict[str, Any] | None:
        row = self._db.fetchone(
            """
            SELECT id FROM approval_gates
            WHERE branch_id = ? AND gate_type = ? AND status = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (branch_id, gate_type, status),
        )
        if row is None:
            return None
        return self.get_gate(row["id"])

    def _build_target_set(
        self, branch_id: str, gate_type: str
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        if gate_type == "story_package":
            return self._story_package_targets(branch_id)
        return self._identity_location_targets(branch_id)

    def _story_package_targets(
        self, branch_id: str
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        blockers: list[dict[str, str]] = []
        packages = [
            p
            for p in self._story_packages.list_package_revisions(limit=50)
            if p["branch_id"] == branch_id and p["status"] == "approved"
        ]
        if not packages:
            blockers.append(
                {
                    "code": "missing_story_package",
                    "message": "no approved story package revision on branch",
                }
            )
            target = {"branch_id": branch_id, "package": None}
            return target, blockers

        # Newest approved by created_at order already DESC in list.
        pkg = packages[0]
        if not pkg.get("timeline_beat_ids") or not pkg.get("episode_ids"):
            blockers.append(
                {
                    "code": "incomplete_package",
                    "message": "approved package missing timeline or episodes",
                }
            )
        if len(pkg.get("episode_ids") or []) < 1:
            blockers.append(
                {
                    "code": "no_episodes",
                    "message": "story package has no episodes",
                }
            )
        target = {
            "branch_id": branch_id,
            "package": {
                "revision_id": pkg["id"],
                "package_id": pkg["package_id"],
                "content_hash": pkg.get("content_hash"),
                "episode_ids": pkg.get("episode_ids"),
                "timeline_beat_ids": pkg.get("timeline_beat_ids"),
                "world_rule_ids": pkg.get("world_rule_ids"),
                "positioning": pkg.get("positioning"),
            },
        }
        return target, blockers

    def _identity_location_targets(
        self, branch_id: str
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        blockers: list[dict[str, str]] = []
        characters = self._characters.list_characters(branch_id=branch_id, limit=100)
        main_chars = [
            c
            for c in characters
            if (c.get("current_revision") or {}).get("role")
            in {"protagonist", "antagonist"}
        ]
        identity_targets: list[dict[str, Any]] = []
        if not main_chars:
            blockers.append(
                {
                    "code": "missing_main_character",
                    "message": "need at least one approved protagonist or antagonist",
                }
            )
        for char in main_chars:
            gate = self._identity.production_gate(char["id"])
            if not gate["ready_for_production"]:
                blockers.append(
                    {
                        "code": "identity_unconfirmed",
                        "message": (
                            f"character {char['id']} identity pack not confirmed"
                        ),
                    }
                )
            identity_targets.append(
                {
                    "character_id": char["id"],
                    "role": (char.get("current_revision") or {}).get("role"),
                    "confirmed_revision_id": gate.get("confirmed_revision_id"),
                    "confirmed_pack_id": gate.get("confirmed_pack_id"),
                }
            )

        locations = self._locations.list_locations(branch_id=branch_id, limit=100)
        core_locs = [loc for loc in locations if loc.get("is_core")]
        location_targets: list[dict[str, Any]] = []
        if not core_locs:
            blockers.append(
                {
                    "code": "missing_core_location",
                    "message": "need at least one core location with confirmed pack",
                }
            )
        for loc in core_locs:
            gate = self._locations.production_gate(loc["id"])
            if not gate["ready_for_production"]:
                blockers.append(
                    {
                        "code": "location_unconfirmed",
                        "message": f"core location {loc['id']} pack not confirmed",
                    }
                )
            location_targets.append(
                {
                    "location_id": loc["id"],
                    "confirmed_revision_id": gate.get("confirmed_revision_id"),
                    "confirmed_pack_id": gate.get("confirmed_pack_id"),
                }
            )

        # Optional but included in hash when present: project visual/director.
        bibles = self._director.list_visual_bibles(branch_id=branch_id, limit=20)
        presets = self._director.list_director_presets(branch_id=branch_id, limit=20)
        visual = None
        for bible in bibles:
            cur = bible.get("current_revision")
            if cur and cur.get("status") == "approved":
                visual = {
                    "bible_id": bible["id"],
                    "revision_id": cur["id"],
                    "content_hash": cur.get("content_hash"),
                    "style_name": cur.get("style_name"),
                }
                break
        director = None
        for preset in presets:
            cur = preset.get("current_revision")
            if cur and cur.get("status") == "approved":
                director = {
                    "preset_id": preset["id"],
                    "revision_id": cur["id"],
                    "content_hash": cur.get("content_hash"),
                }
                break
        if visual is None:
            blockers.append(
                {
                    "code": "missing_visual_bible",
                    "message": "project visual bible not approved",
                }
            )
        if director is None:
            blockers.append(
                {
                    "code": "missing_director_preset",
                    "message": "project director preset not approved",
                }
            )

        target = {
            "branch_id": branch_id,
            "identities": identity_targets,
            "locations": location_targets,
            "visual_bible": visual,
            "director_preset": director,
        }
        return target, blockers
