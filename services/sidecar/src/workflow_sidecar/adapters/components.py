"""Local model component registry (ADR-007): CosyVoice / MuseTalk status."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .timeutil_shim import utc_now_iso


COMPONENT_IDS = ("cosyvoice3", "musetalk")


def components_root() -> Path:
    override = os.environ.get("WORKFLOW_COMPONENTS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ai-video-workflow" / "components"


def registry_path() -> Path:
    return components_root() / "registry.json"


def _default_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": utc_now_iso(),
        "components": {
            "cosyvoice3": {
                "id": "cosyvoice3",
                "status": "not_installed",
                "binary": None,
                "version": None,
                "install_hint": (
                    "Install CosyVoice3 runtime, then: "
                    "components.register path=<bin> component=cosyvoice3"
                ),
            },
            "musetalk": {
                "id": "musetalk",
                "status": "not_installed",
                "binary": None,
                "version": None,
                "install_hint": "Install MuseTalk MLX runtime, then register binary path.",
            },
        },
    }


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        reg = _default_registry()
        save_registry(reg)
        return reg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "components" not in data:
            return _default_registry()
        return data
    except (OSError, json.JSONDecodeError):
        return _default_registry()


def save_registry(data: dict[str, Any]) -> Path:
    root = components_root()
    root.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["updated_at"] = utc_now_iso()
    path = registry_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def probe_components() -> dict[str, Any]:
    reg = load_registry()
    comps = reg.setdefault("components", {})
    # CosyVoice discovery
    cosy = comps.setdefault("cosyvoice3", {"id": "cosyvoice3"})
    env_bin = os.environ.get("WORKFLOW_COSYVOICE_BIN")
    discovered = (
        env_bin
        or cosy.get("binary")
        or shutil.which("cosyvoice")
        or shutil.which("cosyvoice3")
    )
    if discovered and Path(discovered).expanduser().exists():
        path = str(Path(discovered).expanduser())
        cosy["binary"] = path
        cosy["status"] = "ready"
        cosy["discovered_via"] = (
            "env"
            if env_bin
            else ("registry" if cosy.get("binary") else "path")
        )
    else:
        cosy["status"] = cosy.get("status") or "not_installed"
        if cosy["status"] == "ready":
            cosy["status"] = "not_installed"
        cosy.setdefault(
            "install_hint",
            "Set WORKFLOW_COSYVOICE_BIN or register via components.register",
        )

    muse = comps.setdefault("musetalk", {"id": "musetalk"})
    muse_bin = (
        os.environ.get("WORKFLOW_MUSETALK_BIN")
        or muse.get("binary")
        or shutil.which("musetalk")
    )
    if muse_bin and Path(muse_bin).expanduser().exists():
        muse["binary"] = str(Path(muse_bin).expanduser())
        muse["status"] = "ready"
    else:
        if muse.get("status") == "ready":
            muse["status"] = "not_installed"
        muse.setdefault("status", "not_installed")

    save_registry(reg)
    return {
        "components_dir": str(components_root()),
        "registry": str(registry_path()),
        "components": comps,
        "fallbacks": {
            "tts": "macos_say" if shutil.which("say") else "mock",
            "lipsync": "simplified_mux" if shutil.which("ffmpeg") else "mock",
        },
    }


def register_component(
    component: str,
    *,
    binary: str,
    version: str | None = None,
) -> dict[str, Any]:
    if component not in COMPONENT_IDS:
        raise ValueError(f"component must be one of {COMPONENT_IDS}")
    path = Path(binary).expanduser()
    if not path.is_file():
        raise ValueError(f"binary not found: {path}")
    reg = load_registry()
    entry = reg.setdefault("components", {}).setdefault(component, {"id": component})
    entry["binary"] = str(path.resolve())
    entry["version"] = version
    entry["status"] = "ready"
    entry["registered_at"] = utc_now_iso()
    save_registry(reg)
    return probe_components()


def cosyvoice_binary() -> str | None:
    info = probe_components()
    cosy = info["components"].get("cosyvoice3") or {}
    if cosy.get("status") == "ready" and cosy.get("binary"):
        return str(cosy["binary"])
    return None


def install_guide() -> dict[str, Any]:
    return {
        "component": "cosyvoice3",
        "status": probe_components()["components"]["cosyvoice3"]["status"],
        "steps": [
            "Clone/install CosyVoice3 runtime on this machine (Apple Silicon).",
            "Ensure a CLI entrypoint exists (e.g. cosyvoice or custom script).",
            "export WORKFLOW_COSYVOICE_BIN=/absolute/path/to/binary",
            "Or RPC: components.register {component:'cosyvoice3', binary:'/path'}",
            "components.probe should report status=ready",
            "TTS will prefer CosyVoice when ready; else macOS say; else mock if allowed",
        ],
        "components_dir": str(components_root()),
        "registry": str(registry_path()),
    }
