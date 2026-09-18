"""批改進度存在 data/<board>/state.json（含學生姓名與作品照片，data/ 不進 git）。

每位學生的狀態：
  new（剛抓下來）→ drafted（AI 草稿完成）→ approved（老師核准）→ posted（已貼到 Padlet）
  另有 skipped（老師略過，不發）與 error（處理失敗，看 error 欄位）
"""
import datetime as dt
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
_lock = threading.RLock()


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, board_id: str):
        self.dir = DATA / board_id
        self.images = self.dir / "images"
        self.path = self.dir / "state.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self):
        with _lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)   # 寫到一半當機也不會把舊檔弄壞

    @property
    def students(self) -> dict:
        return self.state.setdefault("students", {})

    def ordered(self) -> list:
        return sorted(self.students.values(), key=lambda s: s["order"])

    def log(self, sid: str, event: str):
        self.students[sid].setdefault("history", []).append(f"{now()} {event}")


def list_boards() -> list:
    out = []
    for p in sorted(DATA.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        s = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": p.parent.name, "title": s.get("board", {}).get("title", ""),
                    "rubric": s.get("rubric_path", ""), "n": len(s.get("students", {}))})
    return out
