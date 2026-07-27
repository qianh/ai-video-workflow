"""AWAP capability catalog, adapter registry, routing and budget gates (M3)."""

from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

COST_CLASSES = frozenset({"free_local", "subscription_cli", "paid_api", "unknown"})
DEFAULT_CAPABILITIES = [
    ("text.structured_generate", "1.0", "subscription_cli", "codex-or-grok"),
    ("image.generate", "1.0", "subscription_cli", "grok"),
    ("image.edit", "1.0", "subscription_cli", "grok"),
    ("video.image_to_video", "1.0", "subscription_cli", "grok"),
    ("ffmpeg.transcode", "1.0", "free_local", "ffmpeg"),
    ("ffmpeg.probe", "1.0", "free_local", "ffprobe"),
    ("tts.synthesize", "1.0", "free_local", "cosyvoice3"),
    ("lipsync.apply", "1.0", "free_local", "musetalk"),
    ("music.download", "1.0", "free_local", "music-downloader"),
]


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AwapService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        now = utc_now()
        adapters = [
            ("codex", "cli", "openai-codex", ["text.structured_generate"]),
            ("grok", "cli", "xai-grok", [
                "text.structured_generate",
                "image.generate",
                "image.edit",
                "video.image_to_video",
            ]),
            ("ffmpeg", "cli", "ffmpeg", ["ffmpeg.transcode", "ffmpeg.probe"]),
            ("cosyvoice3", "local_model", "cosyvoice3", ["tts.synthesize"]),
            ("musetalk", "local_model", "musetalk-mlx", ["lipsync.apply"]),
            ("music-downloader", "cli", "music-downloader", ["music.download"]),
            ("mock", "inprocess", "mock", [c[0] for c in DEFAULT_CAPABILITIES]),
        ]
        for name, kind, provider, caps in adapters:
            row = self._db.fetchone(
                "SELECT id FROM awap_adapters WHERE name = ?", (name,)
            )
            if row is None:
                self._db.execute(
                    """
                    INSERT INTO awap_adapters(
                        id, name, kind, provider, status, capabilities_json,
                        config_json, created_at
                    ) VALUES (?, ?, ?, ?, 'registered', ?, '{}', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        name,
                        kind,
                        provider,
                        _stable_json(caps),
                        now,
                    ),
                )
        for capability, version, cost_class, _ in DEFAULT_CAPABILITIES:
            row = self._db.fetchone(
                "SELECT id FROM awap_capabilities WHERE capability = ?",
                (capability,),
            )
            if row is None:
                self._db.execute(
                    """
                    INSERT INTO awap_capabilities(
                        id, capability, version, cost_class, status,
                        adapter_id, probe_detail_json, updated_at
                    ) VALUES (?, ?, ?, ?, 'unknown', NULL, '{}', ?)
                    """,
                    (str(uuid.uuid4()), capability, version, cost_class, now),
                )
        self._db.commit()

    def list_capabilities(self) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            "SELECT * FROM awap_capabilities ORDER BY capability ASC"
        )
        return [self._cap_row(row) for row in rows]

    def list_adapters(self) -> list[dict[str, Any]]:
        rows = self._db.fetchall("SELECT * FROM awap_adapters ORDER BY name ASC")
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "provider": row["provider"],
                "status": row["status"],
                "capabilities": json.loads(row["capabilities_json"]),
                "config": json.loads(row["config_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def probe(self, capability: str | None = None) -> dict[str, Any]:
        caps = self.list_capabilities()
        if capability:
            caps = [c for c in caps if c["capability"] == capability]
        results = []
        now = utc_now()
        for cap in caps:
            status, detail = self._probe_one(cap["capability"], cap["cost_class"])
            adapter = self._prefer_adapter(cap["capability"], status)
            self._db.execute(
                """
                UPDATE awap_capabilities
                SET status = ?, adapter_id = ?, probe_detail_json = ?, updated_at = ?
                WHERE capability = ?
                """,
                (
                    status,
                    adapter,
                    _stable_json(detail),
                    now,
                    cap["capability"],
                ),
            )
            results.append(
                {
                    "capability": cap["capability"],
                    "status": status,
                    "cost_class": cap["cost_class"],
                    "adapter_id": adapter,
                    "detail": detail,
                }
            )
        self._db.commit()
        return {"probes": results}

    def route(
        self,
        *,
        capability: str,
        allow_paid: bool = False,
        prefer: str | None = None,
    ) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM awap_capabilities WHERE capability = ?", (capability,)
        )
        if row is None:
            raise ValueError(f"unknown capability: {capability}")
        cap = self._cap_row(row)
        if cap["status"] == "unknown":
            self.probe(capability)
            row = self._db.fetchone(
                "SELECT * FROM awap_capabilities WHERE capability = ?",
                (capability,),
            )
            cap = self._cap_row(row)
        if cap["cost_class"] == "unknown":
            self._budget_event(capability, "unknown", False, "unknown cost blocked")
            raise ValueError("unknown cost class cannot start processes")
        if cap["cost_class"] == "paid_api" and not allow_paid:
            self._budget_event(
                capability, "paid_api", False, "paid API blocked by budget gate"
            )
            raise ValueError("paid API not authorized; set allow_paid=true after approval")
        if cap["status"] in {"unavailable"} and prefer != "mock":
            # Fall back to mock for offline workflow continuity.
            adapter = "mock"
            degraded = True
        else:
            adapter = prefer or cap.get("adapter_name") or "mock"
            degraded = cap["status"] != "ready"
        self._budget_event(capability, cap["cost_class"], True, "routed")
        return {
            "capability": capability,
            "adapter": adapter,
            "cost_class": cap["cost_class"],
            "status": cap["status"],
            "degraded": degraded,
            "allowed": True,
        }

    def catalog(self) -> dict[str, Any]:
        return {
            "capabilities": self.list_capabilities(),
            "adapters": self.list_adapters(),
            "protocol": "AWAP/1.0-draft",
        }

    def _cap_row(self, row: Any) -> dict[str, Any]:
        adapter_name = None
        if row["adapter_id"]:
            a = self._db.fetchone(
                "SELECT name FROM awap_adapters WHERE id = ?", (row["adapter_id"],)
            )
            if a:
                adapter_name = a["name"]
        return {
            "id": row["id"],
            "capability": row["capability"],
            "version": row["version"],
            "cost_class": row["cost_class"],
            "status": row["status"],
            "adapter_id": row["adapter_id"],
            "adapter_name": adapter_name,
            "probe_detail": json.loads(row["probe_detail_json"]),
            "updated_at": row["updated_at"],
        }

    def _prefer_adapter(self, capability: str, status: str) -> str | None:
        if status == "unavailable":
            row = self._db.fetchone(
                "SELECT id FROM awap_adapters WHERE name = 'mock'"
            )
            return row["id"] if row else None
        mapping = {
            "text.structured_generate": "grok" if shutil.which("grok") else "codex",
            "image.generate": "grok",
            "image.edit": "grok",
            "video.image_to_video": "grok",
            "ffmpeg.transcode": "ffmpeg",
            "ffmpeg.probe": "ffmpeg",
            "tts.synthesize": "cosyvoice3",
            "lipsync.apply": "musetalk",
            "music.download": "music-downloader",
        }
        name = mapping.get(capability, "mock")
        if name in {"grok", "codex", "ffmpeg", "music-downloader"} and not shutil.which(
            name if name != "ffmpeg" else "ffmpeg"
        ):
            if name == "ffmpeg" and not shutil.which("ffmpeg"):
                name = "mock"
            elif name != "ffmpeg":
                name = "mock"
        row = self._db.fetchone(
            "SELECT id FROM awap_adapters WHERE name = ?", (name,)
        )
        return row["id"] if row else None

    def _probe_one(self, capability: str, cost_class: str) -> tuple[str, dict[str, Any]]:
        if capability.startswith("ffmpeg"):
            bin_name = "ffprobe" if "probe" in capability else "ffmpeg"
            path = shutil.which(bin_name)
            if path:
                return "ready", {"binary": path}
            return "unavailable", {"binary": None, "fallback": "mock"}
        if capability.startswith("image") or capability.startswith("video") or capability.startswith("text"):
            if shutil.which("grok"):
                return "ready", {"cli": "grok"}
            if capability.startswith("text") and shutil.which("codex"):
                return "ready", {"cli": "codex"}
            if capability.startswith("video"):
                return "unavailable", {
                    "reason": "video may require upload_url; use static_motion degrade",
                    "fallback": "mock",
                }
            return "degraded", {"reason": "CLI missing; mock available"}
        if capability == "tts.synthesize":
            return "degraded", {"reason": "CosyVoice3 optional; mock TTS available"}
        if capability == "lipsync.apply":
            return "degraded", {"reason": "MuseTalk optional; mock lipsync available"}
        if capability == "music.download":
            path = shutil.which("music-downloader") or shutil.which("yt-dlp")
            if path:
                return "ready", {"binary": path}
            return "degraded", {"fallback": "mock"}
        return "unknown", {"cost_class": cost_class}

    def _budget_event(
        self, capability: str, cost_class: str, allowed: bool, reason: str
    ) -> None:
        self._db.execute(
            """
            INSERT INTO budget_events(
                id, capability, cost_class, allowed, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                capability,
                cost_class,
                1 if allowed else 0,
                reason,
                utc_now(),
            ),
        )
        self._db.commit()
