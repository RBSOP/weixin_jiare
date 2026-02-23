"""断点续传 - 保存/加载采集进度"""
import json
from datetime import datetime
from pathlib import Path

from config import DB_PATH


def _checkpoint_path() -> Path:
    return Path(DB_PATH).parent / "collect_checkpoint.json"


def save_checkpoint(page_num: int, total_pages: int, saved_count: int) -> None:
    """保存采集进度"""
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_page": page_num,
        "total_pages": total_pages,
        "saved_count": saved_count,
        "timestamp": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint() -> dict | None:
    """加载采集进度，不存在或无效则返回 None"""
    path = _checkpoint_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def clear_checkpoint() -> None:
    """清除 checkpoint（全量重采时使用）"""
    path = _checkpoint_path()
    if path.exists():
        path.unlink()
