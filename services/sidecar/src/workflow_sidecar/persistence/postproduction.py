"""TTS, captions, music, timeline, mix, render, export (M4)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .assets import AssetService
from .awap import AwapService
from .database import Database
from .production import ProductionService
from .storyboard import StoryboardService
from .timeutil import utc_now


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class PostProductionService:
    def __init__(self, db: Database, project_root: Path) -> None:
        self._db = db
        self._root = Path(project_root)
        self._assets = AssetService(db, self._root)
        self._awap = AwapService(db)
        self._storyboards = StoryboardService(db)
        self._production = ProductionService(db, self._root)

    # --- voice auth + TTS ---

    def authorize_voice(
        self,
        *,
        character_id: str | None = None,
        voice_profile_id: str | None = None,
        evidence_note: str | None = None,
    ) -> dict[str, Any]:
        auth_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO voice_authorizations(
                id, character_id, voice_profile_id, status, evidence_note,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'confirmed', ?, ?, ?)
            """,
            (auth_id, character_id, voice_profile_id, evidence_note, now, now),
        )
        self._db.commit()
        return {
            "id": auth_id,
            "character_id": character_id,
            "voice_profile_id": voice_profile_id,
            "status": "confirmed",
        }

    def synthesize_tts(
        self,
        *,
        text: str,
        character_id: str | None = None,
        voice_profile_id: str | None = None,
        dialogue_line_revision_id: str | None = None,
        authorization_id: str | None = None,
    ) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("text is required")
        if not authorization_id:
            # require auth for character-bound TTS
            if character_id or voice_profile_id:
                raise ValueError("TTS requires voice authorization_id")
        else:
            row = self._db.fetchone(
                "SELECT * FROM voice_authorizations WHERE id = ?",
                (authorization_id,),
            )
            if row is None or row["status"] != "confirmed":
                raise ValueError("voice authorization missing or not confirmed")
        self._awap.route(capability="tts.synthesize")
        # mock wav bytes
        asset = self._assets.create_asset(
            title=f"tts:{text[:24]}",
            asset_type="audio",
            role="dialogue_tts",
            bytes_data=b"RIFF....WAVEmock" + text.encode("utf-8")[:200],
            mime_type="audio/wav",
            license_status="confirmed_by_user",
        )
        utt_id = str(uuid.uuid4())
        duration = max(800, min(8000, len(text) * 80))
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO tts_utterances(
                id, dialogue_line_revision_id, character_id, voice_profile_id,
                text, status, asset_id, duration_ms, authorization_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)
            """,
            (
                utt_id,
                dialogue_line_revision_id,
                character_id,
                voice_profile_id,
                text,
                asset["id"],
                duration,
                authorization_id,
                now,
            ),
        )
        self._db.commit()
        return {
            "id": utt_id,
            "text": text,
            "status": "succeeded",
            "asset_id": asset["id"],
            "duration_ms": duration,
            "authorization_id": authorization_id,
        }

    def plan_lipsync(
        self,
        *,
        shot_revision_id: str,
        tts_utterance_id: str,
        level: str = "simplified",
    ) -> dict[str, Any]:
        if level not in {"precise", "simplified", "none"}:
            raise ValueError("invalid lip_sync level")
        srev = self._storyboards.get_shot_revision(shot_revision_id)
        # only precise for CU/ECU/MCU
        if level == "precise" and srev["framing"] not in {"ECU", "CU", "MCU"}:
            level = "simplified"
        if level == "none":
            return {"status": "skipped", "level": "none"}
        self._awap.route(capability="lipsync.apply")
        job_id = str(uuid.uuid4())
        asset = self._assets.create_asset(
            title=f"lipsync-{shot_revision_id[:8]}",
            asset_type="video",
            role="lipsync",
            bytes_data=b"mock-lipsync",
            mime_type="video/mp4",
            license_status="confirmed_by_user",
        )
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO lip_sync_jobs(
                id, shot_revision_id, tts_utterance_id, level, status,
                output_asset_id, created_at
            ) VALUES (?, ?, ?, ?, 'succeeded', ?, ?)
            """,
            (job_id, shot_revision_id, tts_utterance_id, level, asset["id"], now),
        )
        self._db.commit()
        return {
            "id": job_id,
            "level": level,
            "status": "succeeded",
            "output_asset_id": asset["id"],
        }

    # --- captions ---

    def create_caption_track(
        self, *, episode_id: str, style: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        track_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO caption_tracks(
                id, episode_id, status, style_json, created_at, updated_at
            ) VALUES (?, ?, 'draft', ?, ?, ?)
            """,
            (track_id, episode_id, _stable_json(style or {"safe_area": "vertical"}), now, now),
        )
        self._db.commit()
        return self.get_caption_track(track_id)

    def get_caption_track(self, track_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM caption_tracks WHERE id = ?", (track_id,)
        )
        if row is None:
            raise ValueError(f"caption track not found: {track_id}")
        segs = self._db.fetchall(
            """
            SELECT * FROM caption_segments WHERE track_id = ?
            ORDER BY sort_order ASC
            """,
            (track_id,),
        )
        return {
            "id": row["id"],
            "episode_id": row["episode_id"],
            "status": row["status"],
            "style": json.loads(row["style_json"]),
            "segments": [
                {
                    "id": s["id"],
                    "start_ms": int(s["start_ms"]),
                    "end_ms": int(s["end_ms"]),
                    "text": s["text"],
                    "sort_order": int(s["sort_order"]),
                    "tts_utterance_id": s["tts_utterance_id"],
                }
                for s in segs
            ],
            "created_at": row["created_at"],
        }

    def add_caption_from_tts(
        self, track_id: str, tts_utterance_id: str, *, start_ms: int
    ) -> dict[str, Any]:
        track = self.get_caption_track(track_id)
        utt = self._db.fetchone(
            "SELECT * FROM tts_utterances WHERE id = ?", (tts_utterance_id,)
        )
        if utt is None:
            raise ValueError("tts utterance not found")
        end_ms = start_ms + int(utt["duration_ms"])
        seg_id = str(uuid.uuid4())
        order = len(track["segments"]) + 1
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO caption_segments(
                id, track_id, dialogue_line_revision_id, tts_utterance_id,
                start_ms, end_ms, text, sort_order, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                seg_id,
                track_id,
                tts_utterance_id,
                start_ms,
                end_ms,
                utt["text"],
                order,
                now,
            ),
        )
        self._db.commit()
        return self.get_caption_track(track_id)

    def compile_ass(self, track_id: str) -> dict[str, Any]:
        track = self.get_caption_track(track_id)
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, Alignment, MarginV",
            "Style: Default,Arial,48,&H00FFFFFF,2,120",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Text",
        ]
        for seg in track["segments"]:
            lines.append(
                f"Dialogue: 0,{self._ms_to_ass(seg['start_ms'])},"
                f"{self._ms_to_ass(seg['end_ms'])},Default,{seg['text']}"
            )
        content = "\n".join(lines) + "\n"
        rel = f"assets/subtitles/{track_id[:8]}.ass"
        dest = self._root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        asset = self._assets.create_asset(
            title=f"captions-{track_id[:8]}",
            asset_type="subtitle",
            role="caption_ass",
            relative_path=rel,
            bytes_data=content.encode("utf-8"),
            mime_type="text/x-ass",
            license_status="confirmed_by_user",
        )
        self._db.execute(
            "UPDATE caption_tracks SET status = 'compiled', updated_at = ? WHERE id = ?",
            (utc_now(), track_id),
        )
        self._db.commit()
        return {
            "track_id": track_id,
            "ass_relative_path": rel,
            "asset_id": asset["id"],
            "safe_area": track["style"].get("safe_area", "vertical"),
            "segment_count": len(track["segments"]),
        }

    # --- music ---

    def import_music(
        self,
        *,
        title: str,
        kind: str = "bgm",
        url: str | None = None,
    ) -> dict[str, Any]:
        self._awap.route(capability="music.download")
        asset = self._assets.create_asset(
            title=title,
            asset_type="audio",
            role=kind,
            bytes_data=f"mock-music:{title}".encode("utf-8"),
            mime_type="audio/mpeg",
            license_status="pending",
        )
        source = self._assets.add_source_record(
            asset_id=asset["id"],
            url=url or f"mock://music/{title}",
            platform="mock",
            title=title,
            tool="music-downloader",
        )
        item_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO music_library_items(
                id, asset_id, title, kind, confirmation_status,
                source_record_id, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (item_id, asset["id"], title, kind, source["id"], now),
        )
        self._db.commit()
        return self.get_music_item(item_id)

    def confirm_music(self, item_id: str) -> dict[str, Any]:
        item = self.get_music_item(item_id)
        self._assets.confirm_license(
            item["asset_id"], license_type="user_confirmed", note="music library"
        )
        self._db.execute(
            """
            UPDATE music_library_items SET confirmation_status = 'confirmed'
            WHERE id = ?
            """,
            (item_id,),
        )
        self._db.commit()
        return self.get_music_item(item_id)

    def get_music_item(self, item_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM music_library_items WHERE id = ?", (item_id,)
        )
        if row is None:
            raise ValueError("music item not found")
        return {
            "id": row["id"],
            "asset_id": row["asset_id"],
            "title": row["title"],
            "kind": row["kind"],
            "confirmation_status": row["confirmation_status"],
            "source_record_id": row["source_record_id"],
            "created_at": row["created_at"],
        }

    def list_music(self, *, confirmed_only: bool = False) -> list[dict[str, Any]]:
        if confirmed_only:
            rows = self._db.fetchall(
                """
                SELECT id FROM music_library_items
                WHERE confirmation_status = 'confirmed'
                ORDER BY created_at DESC
                """
            )
        else:
            rows = self._db.fetchall(
                "SELECT id FROM music_library_items ORDER BY created_at DESC"
            )
        return [self.get_music_item(row["id"]) for row in rows]

    # --- timeline ---

    def create_timeline(self, *, episode_id: str) -> dict[str, Any]:
        tl_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO timelines(
                    id, episode_id, status, current_revision_id,
                    confirmed_revision_id, created_at, updated_at
                ) VALUES (?, ?, 'active', NULL, NULL, ?, ?)
                """,
                (tl_id, episode_id, now, now),
            )
            self._db.execute(
                """
                INSERT INTO timeline_revisions(
                    id, timeline_id, revision_no, status, canvas_json, fps,
                    duration_ms, content_hash, created_at, confirmed_at
                ) VALUES (?, ?, 1, 'draft', ?, 30, 0, NULL, ?, NULL)
                """,
                (
                    rev_id,
                    tl_id,
                    _stable_json({"width": 1080, "height": 1920}),
                    now,
                ),
            )
            for idx, (ttype, name) in enumerate(
                [
                    ("video", "V1"),
                    ("voice", "A1 Dialogue"),
                    ("music", "A2 Music"),
                    ("sfx", "A3 SFX"),
                    ("caption", "C1"),
                ]
            ):
                self._db.execute(
                    """
                    INSERT INTO timeline_tracks(
                        id, timeline_revision_id, track_type, name, sort_order, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), rev_id, ttype, name, idx, now),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_timeline(tl_id)

    def get_timeline(self, timeline_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM timelines WHERE id = ?", (timeline_id,)
        )
        if row is None:
            raise ValueError("timeline not found")
        rev = None
        if row["current_revision_id"]:
            rev = self.get_timeline_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM timeline_revisions
                WHERE timeline_id = ? ORDER BY revision_no DESC LIMIT 1
                """,
                (timeline_id,),
            )
            if latest:
                rev = self.get_timeline_revision(latest["id"])
        return {
            "id": row["id"],
            "episode_id": row["episode_id"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "confirmed_revision_id": row["confirmed_revision_id"],
            "current_revision": rev,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_timeline_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM timeline_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError("timeline revision not found")
        tracks = self._db.fetchall(
            """
            SELECT * FROM timeline_tracks
            WHERE timeline_revision_id = ?
            ORDER BY sort_order ASC
            """,
            (revision_id,),
        )
        track_payload = []
        for t in tracks:
            clips = self._db.fetchall(
                """
                SELECT * FROM timeline_clips WHERE track_id = ?
                ORDER BY start_ms ASC
                """,
                (t["id"],),
            )
            track_payload.append(
                {
                    "id": t["id"],
                    "track_type": t["track_type"],
                    "name": t["name"],
                    "sort_order": int(t["sort_order"]),
                    "clips": [
                        {
                            "id": c["id"],
                            "asset_file_id": c["asset_file_id"],
                            "shot_revision_id": c["shot_revision_id"],
                            "start_ms": int(c["start_ms"]),
                            "end_ms": int(c["end_ms"]),
                            "source_in_ms": int(c["source_in_ms"]),
                            "source_out_ms": c["source_out_ms"],
                            "params": json.loads(c["params_json"]),
                        }
                        for c in clips
                    ],
                }
            )
        return {
            "id": row["id"],
            "timeline_id": row["timeline_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "canvas": json.loads(row["canvas_json"]),
            "fps": int(row["fps"]),
            "duration_ms": int(row["duration_ms"]),
            "content_hash": row["content_hash"],
            "tracks": track_payload,
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
        }

    def assemble_from_storyboard(
        self,
        timeline_revision_id: str,
        storyboard_revision_id: str,
        *,
        music_item_id: str | None = None,
        caption_track_id: str | None = None,
    ) -> dict[str, Any]:
        rev = self.get_timeline_revision(timeline_revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError("timeline revision not editable")
        shots = self._storyboards.list_shots(storyboard_revision_id)
        items = self._production.list_items(
            storyboard_revision_id=storyboard_revision_id
        )
        item_by_shot = {i["shot_revision_id"]: i for i in items if i.get("output_asset_id")}
        video_track = next(t for t in rev["tracks"] if t["track_type"] == "video")
        music_track = next(t for t in rev["tracks"] if t["track_type"] == "music")
        caption_track = next(t for t in rev["tracks"] if t["track_type"] == "caption")
        cursor = 0
        now = utc_now()
        for shot in shots:
            srev = shot["current_revision"]
            if not srev:
                continue
            duration = int(srev["duration_ms"])
            item = item_by_shot.get(srev["id"])
            asset_file_id = None
            if item and item.get("output_asset_id"):
                asset = self._assets.get_asset(item["output_asset_id"])
                if asset["files"]:
                    asset_file_id = asset["files"][0]["id"]
            self._db.execute(
                """
                INSERT INTO timeline_clips(
                    id, track_id, asset_file_id, shot_revision_id, start_ms, end_ms,
                    source_in_ms, source_out_ms, params_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, '{}', ?)
                """,
                (
                    str(uuid.uuid4()),
                    video_track["id"],
                    asset_file_id,
                    srev["id"],
                    cursor,
                    cursor + duration,
                    duration,
                    now,
                ),
            )
            cursor += duration
        if music_item_id:
            music = self.get_music_item(music_item_id)
            if music["confirmation_status"] != "confirmed":
                raise ValueError("music must be confirmed before timeline use")
            asset = self._assets.get_asset(music["asset_id"])
            file_id = asset["files"][0]["id"] if asset["files"] else None
            self._db.execute(
                """
                INSERT INTO timeline_clips(
                    id, track_id, asset_file_id, shot_revision_id, start_ms, end_ms,
                    source_in_ms, source_out_ms, params_json, created_at
                ) VALUES (?, ?, ?, NULL, 0, ?, 0, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    music_track["id"],
                    file_id,
                    cursor,
                    cursor,
                    _stable_json({"gain_db": -12}),
                    now,
                ),
            )
        if caption_track_id:
            cap = self.get_caption_track(caption_track_id)
            for seg in cap["segments"]:
                self._db.execute(
                    """
                    INSERT INTO timeline_clips(
                        id, track_id, asset_file_id, shot_revision_id,
                        start_ms, end_ms, source_in_ms, source_out_ms,
                        params_json, created_at
                    ) VALUES (?, ?, NULL, NULL, ?, ?, 0, NULL, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        caption_track["id"],
                        seg["start_ms"],
                        seg["end_ms"],
                        _stable_json({"text": seg["text"]}),
                        now,
                    ),
                )
        content_hash = _hash({"duration_ms": cursor, "shot_count": len(shots)})
        self._db.execute(
            """
            UPDATE timeline_revisions
            SET duration_ms = ?, content_hash = ?, status = 'draft'
            WHERE id = ?
            """,
            (cursor, content_hash, timeline_revision_id),
        )
        self._db.execute(
            """
            UPDATE timelines SET current_revision_id = ?, updated_at = ?
            WHERE id = (
                SELECT timeline_id FROM timeline_revisions WHERE id = ?
            )
            """,
            (timeline_revision_id, now, timeline_revision_id),
        )
        self._db.commit()
        return self.get_timeline_revision(timeline_revision_id)

    def create_mix_plan(self, timeline_revision_id: str) -> dict[str, Any]:
        rev = self.get_timeline_revision(timeline_revision_id)
        plan = {
            "dialogue_lufs": -16,
            "music_lufs": -24,
            "sfx_lufs": -20,
            "sidechain_db": -6,
            "loudness_target": -14,
            "duration_ms": rev["duration_ms"],
        }
        mix_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO mix_plans(
                id, timeline_revision_id, status, plan_json, created_at
            ) VALUES (?, ?, 'ready', ?, ?)
            """,
            (mix_id, timeline_revision_id, _stable_json(plan), now),
        )
        self._db.commit()
        return {"id": mix_id, "status": "ready", "plan": plan}

    def render_timeline(
        self,
        timeline_revision_id: str,
        *,
        kind: str = "proxy",
    ) -> dict[str, Any]:
        if kind not in {"proxy", "rough", "master"}:
            raise ValueError("kind must be proxy, rough, or master")
        rev = self.get_timeline_revision(timeline_revision_id)
        self._awap.route(capability="ffmpeg.transcode")
        rel = f"renders/{kind}/{timeline_revision_id[:8]}.mp4"
        dest = self._root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Prefer real ffmpeg color source if available; else mock file.
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            duration_s = max(1, int(rev["duration_ms"] / 1000) or 3)
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s=1080x1920:d={duration_s}",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=48000:cl=stereo:d={duration_s}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(dest),
            ]
            subprocess.run(cmd, capture_output=True, check=False)
        if not dest.is_file():
            dest.write_bytes(b"mock-render-" + kind.encode("utf-8"))
        job_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO render_jobs(
                id, timeline_revision_id, kind, status, output_relative_path,
                params_json, created_at, finished_at
            ) VALUES (?, ?, ?, 'succeeded', ?, ?, ?, ?)
            """,
            (
                job_id,
                timeline_revision_id,
                kind,
                rel,
                _stable_json({"fps": rev["fps"], "duration_ms": rev["duration_ms"]}),
                now,
                now,
            ),
        )
        self._db.commit()
        return {
            "id": job_id,
            "kind": kind,
            "status": "succeeded",
            "output_relative_path": rel,
        }

    def confirm_rough_cut(self, timeline_revision_id: str) -> dict[str, Any]:
        rev = self.get_timeline_revision(timeline_revision_id)
        now = utc_now()
        self._db.execute(
            """
            UPDATE timeline_revisions
            SET status = 'confirmed', confirmed_at = ?
            WHERE id = ?
            """,
            (now, timeline_revision_id),
        )
        self._db.execute(
            """
            UPDATE timelines
            SET current_revision_id = ?, confirmed_revision_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (timeline_revision_id, timeline_revision_id, now, rev["timeline_id"]),
        )
        self._db.commit()
        return self.get_timeline(rev["timeline_id"])

    def create_cover(
        self, *, episode_id: str, title: str, template: str = "vertical_title"
    ) -> dict[str, Any]:
        asset = self._assets.create_asset(
            title=f"cover:{title}",
            asset_type="image",
            role="cover",
            bytes_data=b"mock-cover",
            mime_type="image/jpeg",
            license_status="confirmed_by_user",
        )
        cov_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO cover_revisions(
                id, episode_id, title, template, asset_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'ready', ?)
            """,
            (cov_id, episode_id, title, template, asset["id"], now),
        )
        self._db.commit()
        return {
            "id": cov_id,
            "episode_id": episode_id,
            "title": title,
            "template": template,
            "asset_id": asset["id"],
            "status": "ready",
        }

    def export_episode(
        self,
        *,
        episode_id: str,
        profile: str,
        timeline_revision_id: str | None = None,
        music_confirmed: bool = True,
    ) -> dict[str, Any]:
        if profile not in {"master", "douyin", "hongguo"}:
            raise ValueError("profile must be master, douyin, or hongguo")
        if not music_confirmed:
            raise ValueError("unconfirmed music cannot enter master export")
        # Ensure no pending music linked is a simplified check: all music must be confirmed
        pending = self.list_music(confirmed_only=False)
        if any(m["confirmation_status"] != "confirmed" for m in pending if m["kind"] == "bgm"):
            # allow if empty; if any pending bgm exists, block master
            if profile == "master" and any(
                m["confirmation_status"] == "pending" for m in pending
            ):
                raise ValueError("pending music cannot enter master")
        rel = f"exports/{profile}/{episode_id[:8]}.mp4"
        dest = self._root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if timeline_revision_id:
            rough = self.render_timeline(timeline_revision_id, kind="master")
            src = self._root / rough["output_relative_path"]
            if src.is_file():
                shutil.copy2(src, dest)
        if not dest.is_file():
            dest.write_bytes(f"export-{profile}".encode("utf-8"))
        checklist = [
            "no_platform_watermark" if profile == "master" else f"profile:{profile}",
            "captions_optional",
            "audio_mixed",
        ]
        job_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO export_jobs(
                id, episode_id, profile, status, output_relative_path,
                checklist_json, created_at, finished_at
            ) VALUES (?, ?, ?, 'succeeded', ?, ?, ?, ?)
            """,
            (
                job_id,
                episode_id,
                profile,
                rel,
                _stable_json(checklist),
                now,
                now,
            ),
        )
        self._db.commit()
        return {
            "id": job_id,
            "episode_id": episode_id,
            "profile": profile,
            "status": "succeeded",
            "output_relative_path": rel,
            "checklist": checklist,
        }

    def _ms_to_ass(self, ms: int) -> str:
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        cs = (ms % 1000) // 10
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
