"""讀取老師的 rubric（TOML），並換算等級與分數。

一面作業牆可以有好幾個區段，每個區段是一份作業（例如 ① 微距攝影、② 人物攝影、③ 風景攝影）：
- 最上層的 [[criteria]] 是每份作業都要看的「共同檢查點」
- [[assignments]] 是各份作業，name 對應區段名稱（區段名稱裡有這幾個字就算對上），底下的 [[assignments.criteria]] 只用在這份作業
評某個區段的作品時，用 for_section() 取得「共同檢查點＋該作業檢查點」。
"""
import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

UNKNOWN = "無法判斷"
DEFAULT_WORK = "學生的美術作品本身（畫作、立體作品等），只拍到教室或人物、看不到作品的不算；草稿或創作過程照也算"
DEFAULT_PRAISE = ["塗色是否均勻", "邊緣是否乾淨俐落", "形狀是否完整", "媒材運用", "完成度", "看得出的用心"]


@dataclass
class Criterion:
    key: str            # c1、c2…（共同）或 a1c1…（第 1 份作業），給模型的 JSON 欄位名稱
    name: str
    look_for: str
    ai: bool = True     # False = 交給老師判斷，不送給模型
    level_notes: dict = field(default_factory=dict)


@dataclass
class Assignment:
    name: str
    task: str
    criteria: list


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
    work: str = DEFAULT_WORK            # 什麼算是這份作業的作品（判斷「請重傳」用）
    praise_angles: list = field(default_factory=lambda: list(DEFAULT_PRAISE))
    assignments: list = field(default_factory=list)
    assignment: str = ""                # for_section() 產生的版本才有：對應到哪一份作業

    @property
    def ai_criteria(self):
        return [c for c in self.criteria if c.ai]

    @property
    def top_level(self):
        return self.levels[0]

    def match(self, section: str):
        """區段名稱裡有作業名稱就算對上（「① 微距攝影」對上「微距攝影」）；對上好幾個時取名稱最長的。"""
        s = _norm(section)
        hits = [a for a in self.assignments if _norm(a.name) and _norm(a.name) in s]
        return max(hits, key=lambda a: len(_norm(a.name))) if hits else None

    def for_section(self, section: str) -> "Rubric":
        a = self.match(section or "")
        if not a:
            return dataclasses.replace(self, assignment="")
        return dataclasses.replace(self, title=f"{self.title}｜{a.name}", task=a.task or self.task,
                                   criteria=self.criteria + a.criteria, assignment=a.name)

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


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _criteria(items, prefix: str, where: str) -> list:
    out = []
    for i, c in enumerate(items or [], 1):
        if not isinstance(c, dict) or not c.get("name") or not c.get("look_for"):
            raise ValueError(f"{where}第 {i} 個檢查點要有 name 和 look_for")
        out.append(Criterion(key=f"{prefix}{i}", name=c["name"], look_for=c["look_for"], ai=c.get("ai", True),
                             level_notes=c.get("level_notes", {})))
    return out


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
    crit = _criteria(d.get("criteria"), "c", f"{path.name}：共同檢查點")
    assignments = []
    for j, a in enumerate(d.get("assignments") or [], 1):
        if not isinstance(a, dict) or not a.get("name"):
            raise ValueError(f"{path.name}：第 {j} 份作業（[[assignments]]）要有 name")
        assignments.append(Assignment(name=a["name"], task=a.get("task", ""),
                                      criteria=_criteria(a.get("criteria"), f"a{j}c", f"{path.name}：「{a['name']}」的")))
    if not crit and not any(a.criteria for a in assignments):
        raise ValueError(f"{path.name}：至少要有一個 [[criteria]] 或 [[assignments.criteria]]")
    if len({_norm(a.name) for a in assignments}) < len(assignments):
        raise ValueError(f"{path.name}：作業名稱不能重複")
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
        work=d.get("work") or DEFAULT_WORK,
        praise_angles=fb.get("praise_angles") or list(DEFAULT_PRAISE),
        assignments=assignments,
    )
