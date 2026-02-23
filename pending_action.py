"""待执行动作 - 用于 Web UI 与 main 进程通信"""
import json
from pathlib import Path

from config import PENDING_ACTION_FILE


def read_and_clear() -> dict | None:
    """读取并清除待执行动作，无则返回 None"""
    p = Path(PENDING_ACTION_FILE)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    p.unlink()
    return data if isinstance(data, dict) else None


def write(action: str, **kwargs) -> None:
    """写入待执行动作"""
    p = Path(PENDING_ACTION_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"action": action, **kwargs}, ensure_ascii=False), encoding="utf-8")
