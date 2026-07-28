"""程序目录数据路径、兼容迁移和可靠的 JSON 持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT
CONFIG_PATH = ROOT / "config.json"
POS_PATH = ROOT / "window_pos.json"
CRASH_LOG = ROOT / "crash.log"
DEFAULT_CONFIG_PATH = ROOT / "config.example.json"
LOCAL_DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "GlassMonitor"


class StorageError(RuntimeError):
    pass


def _move_preserving(path: Path, target: Path) -> None:
    if not path.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.move(str(path), str(target))
        return

    index = 1
    backup = target.with_name(f"{target.stem}.legacy-{index}{target.suffix}")
    while backup.exists():
        index += 1
        backup = target.with_name(f"{target.stem}.legacy-{index}{target.suffix}")
    shutil.move(str(path), str(backup))


def migrate_legacy_data() -> None:
    """若上一版本迁到了 LocalAppData，则在程序目录缺文件时搬回来。"""
    if not os.environ.get("LOCALAPPDATA") or LOCAL_DATA_ROOT == ROOT:
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("config.json", CONFIG_PATH),
        ("window_pos.json", POS_PATH),
        ("crash.log", CRASH_LOG),
    ):
        source = LOCAL_DATA_ROOT / name
        if source.is_file() and not target.exists():
            shutil.move(str(source), str(target))


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default.copy() if isinstance(default, dict) else default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise StorageError(f"无法读取 {path}: {exc}") from exc


def atomic_save_json(path: Path, data: Any) -> None:
    """在同目录写临时文件后原子替换，旧版本保留为 .bak。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        if path.is_file():
            backup = path.with_suffix(path.suffix + ".bak")
            backup_temp = backup.with_suffix(backup.suffix + ".tmp")
            shutil.copy2(path, backup_temp)
            os.replace(backup_temp, backup)
        os.replace(temp_name, path)
    except Exception as exc:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except Exception:
                pass
        raise StorageError(f"无法保存 {path}: {exc}") from exc


def append_crash_log(message: str) -> None:
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass
