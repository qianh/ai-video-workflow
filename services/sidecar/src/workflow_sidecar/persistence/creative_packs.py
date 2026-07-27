"""Creative Pack register / compose / lock / evaluate (M2-03)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .database import Database
from .timeutil import utc_now

PACK_TYPES = frozenset({"visual_style", "narrative_genre", "model_technique"})
SCOPES = frozenset({"builtin", "global", "project"})
COMP_STATUSES = frozenset(
    {"draft", "evaluating", "eligible", "rejected", "deprecated"}
)
DEFAULT_SUITE = "builtin-structure-v1"


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


@dataclass
class PackRecord:
    id: str
    name: str
    pack_type: str
    scope: str
    archived: bool
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackRevisionRecord:
    id: str
    pack_id: str
    version: int
    rules: dict[str, Any]
    resources: dict[str, Any]
    content_hash: str
    status: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


class CreativePackService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def register_pack(
        self,
        *,
        name: str,
        pack_type: str,
        scope: str = "project",
        rules: dict[str, Any] | None = None,
        resources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_name = name.strip()
        if not safe_name:
            raise ValueError("name must be a non-empty string")
        if pack_type not in PACK_TYPES:
            raise ValueError(f"invalid pack_type: {pack_type}")
        if scope not in SCOPES:
            raise ValueError(f"invalid scope: {scope}")
        rules = rules or {}
        resources = resources or {}
        if not isinstance(rules, dict) or not isinstance(resources, dict):
            raise ValueError("rules and resources must be objects")

        pack_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = utc_now()
        content_hash = _hash_payload(
            {"rules": rules, "resources": resources, "pack_type": pack_type}
        )
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO creative_packs(id, name, pack_type, scope, archived, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (pack_id, safe_name, pack_type, scope, now, now),
            )
            self._db.execute(
                """
                INSERT INTO creative_pack_revisions(
                    id, pack_id, version, rules_json, resources_json, content_hash, status, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, 'published', ?)
                """,
                (
                    revision_id,
                    pack_id,
                    _stable_json(rules),
                    _stable_json(resources),
                    content_hash,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return {
            "pack": PackRecord(
                id=pack_id,
                name=safe_name,
                pack_type=pack_type,
                scope=scope,
                archived=False,
                created_at=now,
                updated_at=now,
            ).as_dict(),
            "revision": PackRevisionRecord(
                id=revision_id,
                pack_id=pack_id,
                version=1,
                rules=rules,
                resources=resources,
                content_hash=content_hash,
                status="published",
                created_at=now,
            ).as_dict(),
        }

    def publish_revision(
        self,
        pack_id: str,
        *,
        rules: dict[str, Any],
        resources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pack = self.get_pack(pack_id)
        if pack["archived"]:
            raise ValueError("pack is archived")
        if not isinstance(rules, dict):
            raise ValueError("rules must be an object")
        resources = resources or {}
        if not isinstance(resources, dict):
            raise ValueError("resources must be an object")

        row = self._db.fetchone(
            "SELECT COALESCE(MAX(version), 0) AS v FROM creative_pack_revisions WHERE pack_id = ?",
            (pack_id,),
        )
        next_version = int(row["v"]) + 1 if row else 1
        revision_id = str(uuid.uuid4())
        now = utc_now()
        content_hash = _hash_payload(
            {
                "rules": rules,
                "resources": resources,
                "pack_type": pack["pack_type"],
            }
        )
        # Published revisions are immutable; only insert a new version.
        self._db.execute(
            """
            INSERT INTO creative_pack_revisions(
                id, pack_id, version, rules_json, resources_json, content_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'published', ?)
            """,
            (
                revision_id,
                pack_id,
                next_version,
                _stable_json(rules),
                _stable_json(resources),
                content_hash,
                now,
            ),
        )
        self._db.execute(
            "UPDATE creative_packs SET updated_at = ? WHERE id = ?",
            (now, pack_id),
        )
        self._db.commit()
        return {
            "id": revision_id,
            "pack_id": pack_id,
            "version": next_version,
            "rules": rules,
            "resources": resources,
            "content_hash": content_hash,
            "status": "published",
            "created_at": now,
        }

    def get_pack(self, pack_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM creative_packs WHERE id = ?", (pack_id,)
        )
        if row is None:
            raise ValueError(f"pack not found: {pack_id}")
        return {
            "id": row["id"],
            "name": row["name"],
            "pack_type": row["pack_type"],
            "scope": row["scope"],
            "archived": bool(row["archived"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_packs(self) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            "SELECT * FROM creative_packs WHERE archived = 0 ORDER BY created_at DESC"
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "pack_type": row["pack_type"],
                "scope": row["scope"],
                "archived": bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM creative_pack_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"revision not found: {revision_id}")
        return {
            "id": row["id"],
            "pack_id": row["pack_id"],
            "version": int(row["version"]),
            "rules": json.loads(row["rules_json"]),
            "resources": json.loads(row["resources_json"]),
            "content_hash": row["content_hash"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def compose(
        self,
        *,
        name: str,
        visual_revision_id: str,
        narrative_revision_id: str,
        technique_revision_ids: list[str] | None = None,
        resolution_order: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_name = name.strip()
        if not safe_name:
            raise ValueError("name must be a non-empty string")
        technique_revision_ids = technique_revision_ids or []
        if not isinstance(technique_revision_ids, list):
            raise ValueError("technique_revision_ids must be an array")
        resolution_order = resolution_order or [
            "visual_style",
            "narrative_genre",
            "model_technique",
        ]

        visual = self.get_revision(visual_revision_id)
        narrative = self.get_revision(narrative_revision_id)
        techniques = [self.get_revision(item) for item in technique_revision_ids]

        visual_pack = self.get_pack(visual["pack_id"])
        narrative_pack = self.get_pack(narrative["pack_id"])
        if visual_pack["pack_type"] != "visual_style":
            raise ValueError("visual_revision_id must reference a visual_style pack")
        if narrative_pack["pack_type"] != "narrative_genre":
            raise ValueError("narrative_revision_id must reference a narrative_genre pack")
        for item in techniques:
            pack = self.get_pack(item["pack_id"])
            if pack["pack_type"] != "model_technique":
                raise ValueError("technique revisions must be model_technique packs")

        # Resolve rules by type order; later types override keys.
        type_to_rules: dict[str, dict[str, Any]] = {
            "visual_style": visual["rules"],
            "narrative_genre": narrative["rules"],
            "model_technique": {},
        }
        for tech in techniques:
            type_to_rules["model_technique"] = {
                **type_to_rules["model_technique"],
                **tech["rules"],
            }

        resolved: dict[str, Any] = {}
        for pack_type in resolution_order:
            rules = type_to_rules.get(pack_type, {})
            if not isinstance(rules, dict):
                raise ValueError("rules must be objects")
            resolved.update(rules)

        hard_conflicts = self._detect_hard_conflicts(
            visual["rules"], narrative["rules"], type_to_rules["model_technique"]
        )
        missing_resources = self._missing_resources(
            [visual, narrative, *techniques]
        )
        status = "eligible"
        if hard_conflicts or missing_resources:
            status = "rejected"

        resource_hashes = {
            rev["id"]: rev["content_hash"]
            for rev in [visual, narrative, *techniques]
        }
        content_hash = _hash_payload(
            {
                "visual": visual_revision_id,
                "narrative": narrative_revision_id,
                "techniques": technique_revision_ids,
                "order": resolution_order,
                "resolved": resolved,
                "resources": resource_hashes,
            }
        )

        composition_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO creative_pack_compositions(id, name, created_at)
                VALUES (?, ?, ?)
                """,
                (composition_id, safe_name, now),
            )
            self._db.execute(
                """
                INSERT INTO creative_pack_composition_revisions(
                    id, composition_id, version, visual_revision_id, narrative_revision_id,
                    technique_revision_ids_json, resolution_order_json, resolved_rules_json,
                    resource_hashes_json, content_hash, status, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    composition_id,
                    visual_revision_id,
                    narrative_revision_id,
                    _stable_json(technique_revision_ids),
                    _stable_json(resolution_order),
                    _stable_json(resolved),
                    _stable_json(resource_hashes),
                    content_hash,
                    status,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise

        return {
            "composition_id": composition_id,
            "composition_revision_id": revision_id,
            "version": 1,
            "status": status,
            "content_hash": content_hash,
            "resolved_rules": resolved,
            "hard_conflicts": hard_conflicts,
            "missing_resources": missing_resources,
            "created_at": now,
        }

    def evaluate(
        self,
        composition_revision_id: str,
        *,
        suite_id: str = DEFAULT_SUITE,
    ) -> dict[str, Any]:
        composition = self.get_composition_revision(composition_revision_id)
        if composition["status"] not in {"eligible", "rejected", "evaluating", "draft"}:
            raise ValueError(f"cannot evaluate status: {composition['status']}")

        structural_ok = isinstance(composition["resolved_rules"], dict)
        rules_ok = composition["status"] == "eligible"
        # Fixed suite: eligible only when structure ok and no hard reject state.
        result = "pass" if structural_ok and rules_ok else "fail"
        now = utc_now()
        evaluation_id = str(uuid.uuid4())
        notes = {
            "suite": suite_id,
            "status_before": composition["status"],
            "structural_ok": structural_ok,
            "rules_ok": rules_ok,
        }
        next_status = "eligible" if result == "pass" else "rejected"
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO creative_pack_evaluations(
                    id, composition_revision_id, suite_id, result,
                    structural_ok, rules_ok, notes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    composition_revision_id,
                    suite_id,
                    result,
                    1 if structural_ok else 0,
                    1 if rules_ok else 0,
                    _stable_json(notes),
                    now,
                ),
            )
            # Evaluation grants eligibility; never auto-locks projects.
            self._db.execute(
                """
                UPDATE creative_pack_composition_revisions
                SET status = ?
                WHERE id = ?
                """,
                (next_status, composition_revision_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return {
            "id": evaluation_id,
            "composition_revision_id": composition_revision_id,
            "suite_id": suite_id,
            "result": result,
            "structural_ok": structural_ok,
            "rules_ok": rules_ok,
            "status_after": next_status,
            "notes": notes,
            "created_at": now,
        }

    def lock(
        self,
        composition_revision_id: str,
        *,
        purpose: str = "production",
    ) -> dict[str, Any]:
        composition = self.get_composition_revision(composition_revision_id)
        if composition["status"] != "eligible":
            raise ValueError("only eligible compositions can be locked")
        safe_purpose = purpose.strip() or "production"
        lock_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO project_creative_pack_locks(
                id, composition_revision_id, composition_content_hash, purpose, locked_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                lock_id,
                composition_revision_id,
                composition["content_hash"],
                safe_purpose,
                now,
            ),
        )
        self._db.commit()
        return {
            "id": lock_id,
            "composition_revision_id": composition_revision_id,
            "composition_content_hash": composition["content_hash"],
            "purpose": safe_purpose,
            "locked_at": now,
        }

    def current_lock(self) -> dict[str, Any] | None:
        row = self._db.fetchone(
            """
            SELECT * FROM project_creative_pack_locks
            ORDER BY locked_at DESC
            LIMIT 1
            """
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "composition_revision_id": row["composition_revision_id"],
            "composition_content_hash": row["composition_content_hash"],
            "purpose": row["purpose"],
            "locked_at": row["locked_at"],
        }

    def get_composition_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM creative_pack_composition_revisions WHERE id = ?",
            (revision_id,),
        )
        if row is None:
            raise ValueError(f"composition revision not found: {revision_id}")
        return {
            "id": row["id"],
            "composition_id": row["composition_id"],
            "version": int(row["version"]),
            "visual_revision_id": row["visual_revision_id"],
            "narrative_revision_id": row["narrative_revision_id"],
            "technique_revision_ids": json.loads(row["technique_revision_ids_json"]),
            "resolution_order": json.loads(row["resolution_order_json"]),
            "resolved_rules": json.loads(row["resolved_rules_json"]),
            "resource_hashes": json.loads(row["resource_hashes_json"]),
            "content_hash": row["content_hash"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def list_compositions(self) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT c.id AS composition_id, c.name, r.id AS composition_revision_id,
                   r.version, r.status, r.content_hash, r.created_at
            FROM creative_pack_compositions c
            JOIN creative_pack_composition_revisions r
              ON r.composition_id = c.id
            WHERE r.version = (
                SELECT MAX(version) FROM creative_pack_composition_revisions
                WHERE composition_id = c.id
            )
            ORDER BY r.created_at DESC
            """
        )
        return [
            {
                "composition_id": row["composition_id"],
                "name": row["name"],
                "composition_revision_id": row["composition_revision_id"],
                "version": int(row["version"]),
                "status": row["status"],
                "content_hash": row["content_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _detect_hard_conflicts(
        visual: dict[str, Any],
        narrative: dict[str, Any],
        technique: dict[str, Any],
    ) -> list[str]:
        conflicts: list[str] = []
        # Explicit hard-conflict marker used by suites / authors.
        for source_name, rules in (
            ("visual_style", visual),
            ("narrative_genre", narrative),
            ("model_technique", technique),
        ):
            banned = rules.get("forbidden_keys")
            if isinstance(banned, list):
                for key in banned:
                    if key in visual or key in narrative or key in technique:
                        # A pack forbids a key that another pack defines.
                        if source_name == "visual_style" and key in visual:
                            continue
                        if source_name == "narrative_genre" and key in narrative:
                            continue
                        if source_name == "model_technique" and key in technique:
                            continue
                        conflicts.append(f"{source_name} forbids key '{key}'")
        # Direct contradiction: same key, values both set and differ, and marked hard.
        for key, value in visual.items():
            if key in narrative and narrative[key] != value and key.startswith("hard_"):
                conflicts.append(f"hard conflict on '{key}'")
        return conflicts

    @staticmethod
    def _missing_resources(revisions: list[dict[str, Any]]) -> list[str]:
        missing: list[str] = []
        for revision in revisions:
            resources = revision.get("resources") or {}
            required = resources.get("required")
            if not isinstance(required, list):
                continue
            available = resources.get("available")
            available_set = set(available) if isinstance(available, list) else set()
            for item in required:
                if item not in available_set:
                    missing.append(str(item))
        return missing
