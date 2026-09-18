"""作業牆自動建立（老師填表 → Padlet AI Recipe 建牆）與後台。

- 建牆紀錄 data/boards.json、範本 data/templates.json（第一次用時放入預設範本），都在 data/，不進 git
- 一個區段（section）就是一份作業：老師建牆時決定區段數量與名稱，學生在各區段各貼一篇，批改時每篇分開評
- Padlet API 的限制（2026-09 實測）：只能用 AI Recipe 建牆、新牆預設關留言、API 不能改設定也不能事後加區段，
  所以建好後讀回比對區段，並列出要老師到 Padlet 手動做的待辦；待辦全勾之前不給學生網址與 QR
- 背景工作（建牆約 30～90 秒、抓作業＋寫草稿要幾分鐘）用執行緒跑，前端輪詢工作進度
"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import requests

import padlet
import rubric as rubric_mod
import store
import workflow
from store import ROOT, now

RUBRICS = ROOT / "rubrics"
TODOS = {"comments": "開啟留言", "moderation": "關閉貼文審核（學生貼了馬上看得到）", "layout": "確認版型是分欄、區段順序正確"}
LIMITS = {"class_name": 20, "assignment": 40, "note": 500}
LABELS = {"class_name": "班級", "assignment": "作業牆名稱", "note": "補充說明"}
MAX_SECTIONS, SECTION_LEN = 8, 20
CREATE_TIMEOUT = 180
QR_HOST = "https://assets.padletcdn.com/"
DEFAULT_TEMPLATE = {
    "id": "photo-3", "name": "攝影課三份作業（微距／人物／風景）",
    "sections": ["① 微距攝影", "② 人物攝影", "③ 風景攝影"],
    "description": "每個區段是一份作業。請在對應的區段按「＋」上傳你自己拍的照片，每篇只放 1 張，標題寫「座號 姓名」。"
                   "拍人物前請先徵得對方同意。老師審閱後會在你的貼文下留言回饋。",
    "rubric": "rubrics/範例-攝影三作業.toml",
}
_lock = threading.RLock()


# ---------------------------------------------------------------- 存檔
def _read(name: str, default):
    p = store.DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(name: str, obj):
    p = store.DATA / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------- 範本
def templates() -> list:
    with _lock:
        t = _read("templates.json", None)
        if t is None:
            t = [DEFAULT_TEMPLATE]
            _write("templates.json", t)
        return t


def check_sections(items) -> list:
    secs = [re.sub(r"\s+", " ", c).strip() for c in items or [] if c and c.strip()]
    if not 1 <= len(secs) <= MAX_SECTIONS:
        raise ValueError(f"區段要 1～{MAX_SECTIONS} 個（一個區段是一份作業）")
    if any(len(c) > SECTION_LEN for c in secs):
        raise ValueError(f"區段名稱請在 {SECTION_LEN} 字以內")
    if len({_norm(c) for c in secs}) < len(secs):
        raise ValueError("區段名稱不能重複")
    return secs


def save_template(d: dict) -> list:
    name = (d.get("name") or "").strip()
    secs = check_sections(d.get("sections"))
    desc = (d.get("description") or "").strip()
    rb = d.get("rubric") or ""
    if not name:
        raise ValueError("範本要有名稱")
    if len(desc) > 600:
        raise ValueError("牆的說明文字請在 600 字以內")
    if rb:
        rb = _rel(rubric_file(rb))
    with _lock:
        ts = templates()
        tid = d.get("id") or "t" + uuid.uuid4().hex[:8]
        new = {"id": tid, "name": name, "sections": secs, "description": desc, "rubric": rb}
        if any(t["id"] == tid for t in ts):
            ts = [new if t["id"] == tid else t for t in ts]
        else:
            ts.append(new)
        _write("templates.json", ts)
        return ts


def delete_template(tid: str) -> list:
    with _lock:
        ts = templates()
        if len(ts) <= 1:
            raise ValueError("至少要留一個範本")
        ts = [t for t in ts if t["id"] != tid]
        _write("templates.json", ts)
        return ts


# ---------------------------------------------------------------- 評分規準（rubrics/*.toml）
def rubric_file(name: str) -> Path:
    """只允許 rubrics/ 底下的 .toml；路徑一律只取檔名，避免寫到別的資料夾。"""
    fn = Path(str(name).replace("\\", "/")).name
    if not re.fullmatch(r"[^\\/:*?\"<>|.][^\\/:*?\"<>|]{0,58}\.toml", fn):
        raise ValueError("檔名要以 .toml 結尾，不能有 \\ / : * ? \" < > | 這些符號")
    return RUBRICS / fn


def _rel(p: Path) -> str:
    return f"rubrics/{p.name}"


def _check_rubric(p: Path, shown: str):
    try:
        return rubric_mod.load(p)
    except ValueError as e:
        raise ValueError(str(e).replace(p.name, shown)) from e
    except (KeyError, TypeError) as e:
        raise ValueError(f"{shown}：每個 [[criteria]] 都要有 name 和 look_for") from e


def rubrics() -> list:
    out = []
    for p in sorted(RUBRICS.glob("*.toml")):
        try:
            rb = _check_rubric(p, p.name)
            out.append({"path": _rel(p), "title": rb.title, "error": "",
                        "assignments": [a.name for a in rb.assignments], "common": len(rb.criteria)})
        except ValueError as e:
            out.append({"path": _rel(p), "title": p.stem, "error": str(e), "assignments": [], "common": 0})
    return out


def read_rubric(name: str) -> dict:
    p = rubric_file(name)
    if not p.exists():
        raise ValueError(f"找不到 {p.name}")
    return {"path": _rel(p), "text": p.read_text(encoding="utf-8-sig")}


def save_rubric(name: str, text: str, overwrite: bool = False) -> list:
    """先用暫存檔檢查格式，通過才寫入，寫壞的 rubric 不會蓋掉原本能用的。"""
    p = rubric_file(name)
    if p.exists() and not overwrite:
        raise ValueError(f"已經有 {p.name}，要覆蓋請確認")
    if len(text) > 50_000:
        raise ValueError("檔案太大（上限 5 萬字）")
    tmp = p.with_name(p.name + ".check")
    RUBRICS.mkdir(exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        _check_rubric(tmp, p.name)
    finally:
        tmp.unlink(missing_ok=True)
    p.write_text(text, encoding="utf-8")
    return rubrics()


# ---------------------------------------------------------------- 作業牆紀錄
def boards() -> list:
    with _lock:
        return _read("boards.json", [])


def get(bid: str) -> dict:
    b = next((b for b in boards() if b["id"] == bid), None)
    if not b:
        raise LookupError(f"清單裡沒有作業牆 {bid}")
    return b


def update(bid: str, **fields) -> dict:
    with _lock:
        bs = boards()
        b = next((b for b in bs if b["id"] == bid), None)
        if not b:
            raise LookupError(f"清單裡沒有作業牆 {bid}")
        b.update(fields)
        _write("boards.json", bs)
        return b


def forget(bid: str):
    """只移除本機紀錄，Padlet 上的牆不會被刪。"""
    with _lock:
        _write("boards.json", [b for b in boards() if b["id"] != bid])


def set_todo(bid: str, key: str, done: bool) -> dict:
    if key not in TODOS:
        raise ValueError(f"沒有這個待辦：{key}")
    return update(bid, todos={**get(bid)["todos"], key: bool(done)})


def set_rubric(bid: str, name: str) -> dict:
    p = rubric_file(name)
    if not p.exists():
        raise ValueError(f"找不到 {p.name}")
    _check_rubric(p, p.name)
    return update(bid, rubric=_rel(p))


def ready(b: dict) -> bool:
    return all(b.get("todos", {}).get(k) for k in TODOS)


def view(b: dict) -> dict:
    """給前端的資料：待辦沒勾完不給學生網址與 QR。老師要到 Padlet 改設定，另給 admin_url。"""
    ok = ready(b)
    return {**b, "admin_url": b["url"], "ready": ok, "url": b["url"] if ok else "", "qr": b["qr"] if ok else "",
            "has_state": bool(store.Store(b["id"]).state), "busy": b["id"] in busy()}


def qr_png(bid: str) -> tuple:
    b = get(bid)
    if not ready(b):
        raise ValueError("待辦三項都打勾後才能下載 QR code")
    if not b.get("qr", "").startswith(QR_HOST):
        raise ValueError("Padlet 沒有提供這面牆的 QR code，請改用網址")
    r = requests.get(b["qr"], timeout=30)
    r.raise_for_status()
    return r.content, f"QR-{b['class_name']}{b['assignment']}.png"


# ---------------------------------------------------------------- 建牆
def prepare(form: dict) -> dict:
    """同步檢查表單（錯了馬上回報，不用等背景工作），回傳建牆需要的資料。"""
    tpl = next((t for t in templates() if t["id"] == form.get("template")), None)
    if not tpl:
        raise ValueError("請選一個範本")
    f = {k: re.sub(r"\s+", " ", (form.get(k) or "")).strip() for k in LIMITS}
    if not f["class_name"] or not f["assignment"]:
        raise ValueError("班級和作業牆名稱都要填")
    for k, n in LIMITS.items():
        if len(f[k]) > n:
            raise ValueError(f"{LABELS[k]}請在 {n} 字以內")
    secs = check_sections(form.get("sections") if form.get("sections") is not None else tpl["sections"])
    title, instr = recipe(secs, tpl.get("description", ""), f["class_name"], f["assignment"], f["note"])
    if len(instr) > 2000:
        raise ValueError("說明文字加上補充說明太長（Padlet 上限 2000 字），請縮短補充說明")
    return {"tpl": tpl, **f, "sections": secs, "title": title, "instructions": instr}


def recipe(sections: list, description: str, class_name: str, assignment: str, note: str = "") -> tuple:
    title = f"{class_name} {assignment}"
    names = "、".join(f"「{c}」" for c in sections)
    s = (f"建立一面美術課的作業繳交牆，標題「{title}」。版面用分欄（columns），"
         f"恰好 {len(sections)} 個區段（section），由左到右依序命名為{names}，名稱一字不改，不要增加或減少區段。"
         "每個區段是一份作業，保持空白讓學生上傳作品，不要放任何範例貼文、圖片或引導貼文。")
    desc = f"這面牆有 {len(sections)} 份作業：{'、'.join(sections)}。" + (description or "")
    s += f"牆的說明文字寫：「{desc}」"
    if note:
        s += f"老師補充：{note}"
    return title, s


def verify(raw: dict, sections: list, class_name: str, assignment: str) -> list:
    """讀回新牆，回傳和表單不一致、要老師處理的地方（空清單表示一致）。區段順序交給手動待辦確認：
    實測 AI 建的區段 sortIndex 會重複，從 API 看不出左右順序。"""
    inc = raw.get("included", [])
    got = [x["attributes"].get("title", "") for x in inc if x["type"] == "section"]
    probs = []
    if sorted(map(_norm, got)) != sorted(map(_norm, sections)):
        probs.append(f"區段是「{'」「'.join(got) or '（沒有區段）'}」，應該是「{'」「'.join(sections)}」，"
                     "請到 Padlet 手動改名、增加或刪除區段")
    title = raw.get("data", {}).get("attributes", {}).get("title", "")
    if _norm(class_name) not in _norm(title) or _norm(assignment) not in _norm(title):
        probs.append(f"牆的標題變成「{title}」，請到 Padlet 改成「{class_name} {assignment}」")
    media = [x for x in inc if x["type"] == "post" and (
        ((x["attributes"].get("content") or {}).get("attachment") or {}).get("url")
        or "<img" in ((x["attributes"].get("content") or {}).get("bodyHtml") or ""))]
    if media:
        probs.append(f"AI 放了 {len(media)} 篇有圖片或附件的貼文，學生繳交前請到 Padlet 刪掉，否則會被當成學生作品")
    return probs


def create_board(p: dict, job: "Job") -> dict:
    cli = padlet.Client()
    job.log("送出建牆要求給 Padlet…")
    status_url = cli.create_ai_board(p["instructions"])
    t0 = time.time()
    while True:
        time.sleep(3)
        state, b = cli.ai_board_status(status_url)
        if state == "done":
            break
        if state == "failed":
            raise padlet.PadletError("Padlet 回報建牆失敗，請按重試")
        if time.time() - t0 > CREATE_TIMEOUT:
            raise padlet.PadletError("超過 3 分鐘還沒建好。請先到 Padlet 儀表板看是不是已經建好了，"
                                     "沒有再按重試（避免建出兩面一樣的牆）")
        job.progress = f"Padlet 建立中…（已等 {int(time.time() - t0)} 秒，通常 30～90 秒）"
    job.progress = ""
    bid = b.get("id") or padlet.board_id_from(((b.get("attributes") or {}).get("webUrl") or {}).get("live", ""))
    job.board = bid
    job.log(f"建好了（{int(time.time() - t0)} 秒），讀回檢查區段…")
    raw = cli.board(bid)
    at = raw.get("data", {}).get("attributes", {})
    web = at.get("webUrl") or ((b.get("attributes") or {}).get("webUrl")) or {}
    tpl = p["tpl"]
    rec = {"id": bid, "title": at.get("title") or p["title"], "url": web.get("live", ""), "qr": web.get("qrCode", ""),
           "class_name": p["class_name"], "assignment": p["assignment"], "note": p["note"],
           "template": tpl["id"], "template_name": tpl["name"], "sections": p["sections"],
           "rubric": tpl.get("rubric", ""), "created_at": now(),
           "problems": verify(raw, p["sections"], p["class_name"], p["assignment"]),
           "todos": {k: False for k in TODOS}, "count": None}
    with _lock:
        _write("boards.json", [rec, *[x for x in boards() if x["id"] != bid]])
    job.log("區段與標題都正確" if not rec["problems"] else f"有 {len(rec['problems'])} 個地方和設定不一樣，請看提醒")
    return view(rec)


# ---------------------------------------------------------------- 繳交人數、抓作業＋寫草稿
_me = {}


def _client() -> tuple:
    cli = padlet.Client()
    if cli.key not in _me:
        _me.clear()
        _me[cli.key] = cli.me()
    return cli, _me[cli.key]


def count(bid: str) -> dict:
    cli, me = _client()
    b = padlet.parse_board(cli.board(bid), me)
    _, groups = padlet.group_posts(b["posts"])
    return view(update(bid, count={"students": len(groups), "posts": len(b["posts"]), "at": now()}))


def grade(bid: str, job: "Job") -> dict:
    b = get(bid)
    if not b.get("rubric"):
        raise ValueError("這面牆還沒選評分規準")
    st = workflow.fetch(b["url"], str(rubric_file(b["rubric"])), log=job.log, unit="section")   # 一個區段一份作業
    workflow.draft_all(st, log=job.log)
    try:
        count(bid)
    except Exception:
        pass   # 人數只是順便更新，失敗不影響批改結果
    return {"review": f"/?board={bid}"}


# ---------------------------------------------------------------- 背景工作
class Job:
    def __init__(self, kind: str, board: str = ""):
        self.id, self.kind, self.board = uuid.uuid4().hex[:12], kind, board
        self.status, self.lines, self.progress, self.result, self.error = "running", [], "", None, ""
        self.started = time.time()

    def log(self, msg: str):
        self.lines.append(str(msg).strip())
        del self.lines[:-200]

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "board": self.board, "status": self.status, "lines": self.lines,
                "progress": self.progress, "result": self.result, "error": self.error,
                "seconds": int(time.time() - self.started)}


_jobs = {}


def busy() -> set:
    return {j.board for j in _jobs.values() if j.status == "running" and j.board}


def running() -> list:
    return [j.view() for j in _jobs.values() if j.status == "running"]


def job(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise LookupError("找不到這個工作（伺服器可能重開過），請重新整理頁面")
    return j.view()


def start(kind: str, fn, board: str = "") -> dict:
    with _lock:
        if kind == "grade" and any(j.kind == "grade" and j.status == "running" for j in _jobs.values()):
            raise ValueError("已經有一面牆在抓作業／寫草稿，顯示卡同一時間只跑一個，請等它完成")
        if board and board in busy():
            raise ValueError("這面牆正在背景處理中，請等它完成")
        for k in [k for k, v in _jobs.items() if v.status != "running"][:-20]:
            del _jobs[k]   # 只留最近 20 個做完的工作
        j = Job(kind, board)
        _jobs[j.id] = j

    def run():
        try:
            j.result = fn(j)
            j.status = "done"
        except Exception as e:
            known = isinstance(e, (ValueError, LookupError, padlet.PadletError))
            j.error = str(e) if known else f"{type(e).__name__}: {e}"
            j.status = "error"
        finally:
            j.progress = ""
    threading.Thread(target=run, daemon=True).start()
    return j.view()
