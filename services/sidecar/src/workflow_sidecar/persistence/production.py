"""Production items, fingerprints, dependency/stale propagation, QC (M3)."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .assets import AssetService
from .awap import AwapService
from .database import Database
from .storyboard import StoryboardService
from .timeutil import utc_now


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class ProductionService:
    def __init__(self, db: Database, project_root: Path) -> None:
        self._db = db
        self._root = Path(project_root)
        self._storyboards = StoryboardService(db)
        self._assets = AssetService(db, self._root)
        self._awap = AwapService(db)

    def plan_shot_item(
        self,
        shot_revision_id: str,
        *,
        kind: str = "image",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        srev = self._storyboards.get_shot_revision(shot_revision_id)
        capability = {
            "image": "image.generate",
            "video": "video.image_to_video",
            "audio": "tts.synthesize",
        }.get(kind, "image.generate")
        route = self._awap.route(capability=capability)
        fingerprint = _hash(
            {
                "shot_revision_id": shot_revision_id,
                "shot_hash": srev.get("content_hash"),
                "kind": kind,
                "params": params or {},
                "generation_mode": srev.get("generation_mode"),
            }
        )
        item_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO production_items(
                id, shot_revision_id, kind, status, input_fingerprint,
                adapter_id, capability, params_json, output_asset_id,
                stale, locked, created_at, updated_at
            ) VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, NULL, 0, 0, ?, ?)
            """,
            (
                item_id,
                shot_revision_id,
                kind,
                fingerprint,
                route["adapter"],
                capability,
                _stable_json(params or {}),
                now,
                now,
            ),
        )
        # Dependency: shot revision → production item
        self._db.execute(
            """
            INSERT INTO dependency_edges(
                id, upstream_type, upstream_id, downstream_type, downstream_id, created_at
            ) VALUES (?, 'shot_revision', ?, 'production_item', ?, ?)
            """,
            (str(uuid.uuid4()), shot_revision_id, item_id, now),
        )
        self._db.commit()
        return self.get_item(item_id)

    def execute_item(self, item_id: str) -> dict[str, Any]:
        item = self.get_item(item_id)
        if item["locked"]:
            raise ValueError("production item is locked")
        if item["status"] == "succeeded" and not item["stale"]:
            return item
        # Mock media output into assets
        kind = item["kind"]
        asset_type = "image" if kind == "image" else ("video" if kind == "video" else "audio")
        payload = f"mock-{kind}-{item_id[:8]}".encode("utf-8")
        asset = self._assets.create_asset(
            title=f"{kind} for {item['shot_revision_id'][:8]}",
            asset_type=asset_type,
            role=f"shot_{kind}",
            bytes_data=payload,
            mime_type="application/octet-stream",
            license_status="confirmed_by_user",
        )
        now = utc_now()
        manifest_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO generation_manifests(
                id, production_item_id, tool, params_json, inputs_json,
                outputs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest_id,
                item_id,
                item["adapter_id"] or "mock",
                item["params_json"] if isinstance(item.get("params_json"), str) else _stable_json(item.get("params") or {}),
                _stable_json({"shot_revision_id": item["shot_revision_id"]}),
                _stable_json({"asset_id": asset["id"]}),
                now,
            ),
        )
        self._db.execute(
            """
            UPDATE production_items
            SET status = 'succeeded', output_asset_id = ?, stale = 0, updated_at = ?
            WHERE id = ?
            """,
            (asset["id"], now, item_id),
        )
        self._db.commit()
        # QC sample rule
        self._maybe_qc(item_id, asset)
        return self.get_item(item_id)

    def batch_plan_and_execute(
        self, storyboard_revision_id: str, *, kind: str = "image"
    ) -> dict[str, Any]:
        shots = self._storyboards.list_shots(storyboard_revision_id)
        items = []
        for shot in shots:
            srev = shot.get("current_revision")
            if not srev:
                continue
            # default one candidate per ordinary shot
            item = self.plan_shot_item(srev["id"], kind=kind)
            items.append(self.execute_item(item["id"]))
        return {
            "storyboard_revision_id": storyboard_revision_id,
            "items": items,
            "count": len(items),
        }

    def mark_upstream_changed(
        self, *, upstream_type: str, upstream_id: str
    ) -> dict[str, Any]:
        """Propagate stale to dependent production items (not locked)."""

        edges = self._db.fetchall(
            """
            SELECT * FROM dependency_edges
            WHERE upstream_type = ? AND upstream_id = ?
            """,
            (upstream_type, upstream_id),
        )
        stale_ids = []
        now = utc_now()
        for edge in edges:
            if edge["downstream_type"] != "production_item":
                continue
            item = self.get_item(edge["downstream_id"])
            if item["locked"]:
                continue
            self._db.execute(
                """
                UPDATE production_items
                SET stale = 1, status = 'stale', updated_at = ?
                WHERE id = ?
                """,
                (now, item["id"]),
            )
            stale_ids.append(item["id"])
            # cascade one level
            child_edges = self._db.fetchall(
                """
                SELECT * FROM dependency_edges
                WHERE upstream_type = 'production_item' AND upstream_id = ?
                """,
                (item["id"],),
            )
            for child in child_edges:
                if child["downstream_type"] == "production_item":
                    self._db.execute(
                        """
                        UPDATE production_items
                        SET stale = 1, status = 'stale', updated_at = ?
                        WHERE id = ? AND locked = 0
                        """,
                        (now, child["downstream_id"]),
                    )
                    stale_ids.append(child["downstream_id"])
        self._db.commit()
        return {"stale_item_ids": list(dict.fromkeys(stale_ids)), "count": len(set(stale_ids))}

    def lock_item(self, item_id: str, locked: bool = True) -> dict[str, Any]:
        self.get_item(item_id)
        self._db.execute(
            "UPDATE production_items SET locked = ?, updated_at = ? WHERE id = ?",
            (1 if locked else 0, utc_now(), item_id),
        )
        self._db.commit()
        return self.get_item(item_id)

    def get_item(self, item_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM production_items WHERE id = ?", (item_id,)
        )
        if row is None:
            raise ValueError(f"production item not found: {item_id}")
        return {
            "id": row["id"],
            "shot_revision_id": row["shot_revision_id"],
            "kind": row["kind"],
            "status": row["status"],
            "input_fingerprint": row["input_fingerprint"],
            "adapter_id": row["adapter_id"],
            "capability": row["capability"],
            "params": json.loads(row["params_json"]),
            "output_asset_id": row["output_asset_id"],
            "stale": bool(row["stale"]),
            "locked": bool(row["locked"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_items(
        self, *, storyboard_revision_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if storyboard_revision_id:
            shots = self._storyboards.list_shots(storyboard_revision_id)
            srev_ids = [
                s["current_revision"]["id"]
                for s in shots
                if s.get("current_revision")
            ]
            items = []
            for sid in srev_ids:
                rows = self._db.fetchall(
                    "SELECT id FROM production_items WHERE shot_revision_id = ?",
                    (sid,),
                )
                items.extend(self.get_item(r["id"]) for r in rows)
            return items[:limit]
        rows = self._db.fetchall(
            "SELECT id FROM production_items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self.get_item(row["id"]) for row in rows]

    def list_review_queue(self, *, open_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        if open_only:
            rows = self._db.fetchall(
                """
                SELECT * FROM review_queue_items
                WHERE status = 'open'
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM review_queue_items
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    def resolve_review(
        self, item_id: str, *, status: str, note: str | None = None
    ) -> dict[str, Any]:
        if status not in {"approved", "rejected", "needs_changes", "waived"}:
            raise ValueError("invalid review status")
        now = utc_now()
        self._db.execute(
            """
            UPDATE review_queue_items
            SET status = ?, decision_note = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, note, now, item_id),
        )
        self._db.commit()
        row = self._db.fetchone(
            "SELECT * FROM review_queue_items WHERE id = ?", (item_id,)
        )
        if row is None:
            raise ValueError("review item not found")
        return dict(row)

    def _maybe_qc(self, item_id: str, asset: dict[str, Any]) -> None:
        # Simple rule: empty/tiny mock files are info-level; never infinite regen.
        finding_id = str(uuid.uuid4())
        now = utc_now()
        size = asset["files"][0]["byte_size"] if asset.get("files") else 0
        if size < 32:
            self._db.execute(
                """
                INSERT INTO qc_findings(
                    id, subject_type, subject_id, rule_id, severity,
                    message, status, created_at
                ) VALUES (?, 'production_item', ?, 'tiny_output', 'info', ?, 'open', ?)
                """,
                (
                    finding_id,
                    item_id,
                    "output is a mock placeholder; replace before master",
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO review_queue_items(
                    id, subject_type, subject_id, reason, status,
                    decision_note, created_at, resolved_at
                ) VALUES (?, 'production_item', ?, ?, 'open', NULL, ?, NULL)
                """,
                (
                    str(uuid.uuid4()),
                    item_id,
                    "QC:tiny_output needs human acknowledgement before master",
                    now,
                ),
            )
            self._db.commit()
