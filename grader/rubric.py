"""讀取老師的 rubric（TOML），並換算等級與分數。"""
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

UNKNOWN = "無法判斷"


@dataclass
class Criterion:
    key: str            # c1、c2…，給模型的 JSON 欄位名稱
    name: str
    look_for: str
    ai: bool = True     # False = 交給老師判斷，不送給模型
    level_notes: dict = field(default_factory=dict)


@dataclass
class Rubric:
    path: Path
    title: str
    task: str
    levels: list
    points: list
    criteria: list
    tone: str
    length: str
    avoid: list
    examples: list
    disclose_ai: bool
    reupload_text: str = ""

    @property
    def ai_criteria(self):
        return [c for c in self.criteria if c.ai]

    @property
    def top_level(self):
        return self.levels[0]

    def score(self, levels_by_key: dict):
        """回傳 (得分, 滿分, 尚未評的項目名稱)；沒設定 points 時回傳 None。"""
        if not self.points:
            return None
        got, missing = 0, []
        for c in self.criteria:
            lv = levels_by_key.get(c.key)
            if lv in self.levels:
                got += self.points[self.levels.index(lv)]
            else:
                missing.append(c.name)
        return got, max(self.points) * len(self.criteria), missing


def load(path) -> Rubric:
    path = Path(path)
    try:
        d = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path.name} 格式有誤：{e}。常見原因：文字沒加雙引號、中文的鍵名沒加雙引號、少了逗號") from e
    levels = d.get("levels") or ["符合", "部分符合", "不符合"]
    points = d.get("points") or []
    if points and len(points) != len(levels):
        raise ValueError(f"{path.name}：points 有 {len(points)} 個，levels 有 {len(levels)} 個，數量要一樣")
    if UNKNOWN in levels:
        raise ValueError(f"{path.name}：levels 不要寫「{UNKNOWN}」，系統會自動加上")
    crit = [Criterion(key=f"c{i}", name=c["name"], look_for=c["look_for"], ai=c.get("ai", True),
                      level_notes=c.get("level_notes", {}))
            for i, c in enumerate(d.get("criteria", []), 1)]
    if not crit:
        raise ValueError(f"{path.name}：至少要有一個 [[criteria]]")
    fb = d.get("feedback", {})
    return Rubric(
        path=path, title=d.get("title", path.stem), task=d.get("task", ""),
        levels=levels, points=points, criteria=crit,
        tone=fb.get("tone", "溫暖、具體，用提問引導學生自己發現"),
        length=fb.get("length", "優點 1 句、引導問題 1～2 句、鼓勵 1 句，120 字以內"),
        avoid=fb.get("avoid", []), examples=fb.get("examples", []),
        disclose_ai=fb.get("disclose_ai", True),
        reupload_text=fb.get("reupload", "老師在這篇貼文裡沒有看到你的作品照片。"
                                         "請重新上傳：正對作品拍、光線充足、讓作品佔滿畫面，老師再給你回饋！"),
    )
