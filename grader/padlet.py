"""Padlet API：讀作業版、把貼文依學生分組、下載作品照片、發留言。

API 文件：https://docs.padlet.dev/reference/introduction
- 需要付費帳號的 API key（放在 .env 的 PADLET_API_KEY）
- 只能存取「你是管理員」的 Padlet
- 每篇貼文只有一個附件，所以學生傳 2～3 張照片時會是 2～3 篇貼文，這裡把同一位學生的貼文合併
"""
import html
import io
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from PIL import Image

API = "https://api.padlet.dev/v1"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp"}


class PadletError(RuntimeError):
    pass


def board_id_from(url_or_id: str) -> str:
    """接受整個 Padlet 網址或 board id。網址最後一段「-」後面的 15～22 碼就是 id（實測最短 15 碼）。"""
    s = url_or_id.strip().split("?")[0].split("#")[0].rstrip("/")
    tail = s.rsplit("/", 1)[-1].rsplit("-", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9]{15,22}", tail):
        raise PadletError(f"看不出 board id：{url_or_id}（請貼 Padlet 網址，或到 … → Developer 複製 id）")
    return tail


class Client:
    def __init__(self, api_key: str = ""):
        self.key = api_key or os.environ.get("PADLET_API_KEY", "")
        if not self.key:
            raise PadletError("沒有 PADLET_API_KEY：請到 https://padlet.com/dashboard/settings/developers 產生，填進 .env")

    def _req(self, method, path, **kw):
        for attempt in range(3):
            r = requests.request(method, f"{API}{path}", headers={"X-API-KEY": self.key}, timeout=30, **kw)
            if r.status_code == 429 and attempt < 2:   # 每分鐘 250 次上限
                time.sleep(20)
                continue
            if r.status_code >= 400:
                try:
                    e = r.json()["errors"][0]
                    msg = f"{e.get('code')}：{e.get('detail')}"
                except Exception:
                    msg = r.text[:200]
                raise PadletError(f"Padlet API {r.status_code} {msg}")
            return r.json()
        raise PadletError("Padlet API 一直回 429（太頻繁），請一分鐘後再試")

    def me(self) -> dict:
        d = self._req("GET", "/me")["data"]
        return {"id": d["id"], **d.get("attributes", {})}

    def board(self, board_id: str) -> dict:
        return self._req("GET", f"/boards/{board_id}", params={"include": "posts,sections,comments"})

    def attachment_preview(self, post_id: str) -> str:
        return self._req("GET", f"/posts/{post_id}/attachmentData")["data"]["attributes"].get("previewImageUrl") or ""

    def comment(self, post_id: str, html_content: str) -> dict:
        body = {"data": {"type": "comment", "attributes": {"htmlContent": html_content}}}
        return self._req("POST", f"/posts/{post_id}/comments", json=body)["data"]


# ---------------------------------------------------------------- 解析與分組
def _text(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", h or "")).strip()


def parse_board(raw: dict, me: dict | None = None) -> dict:
    """把 JSON:API 回應整理成 {title, url, posts:[…]}；老師自己發的貼文（作業說明）會排除。"""
    inc = raw.get("included", [])
    secs = sorted((x for x in inc if x["type"] == "section"),
                  key=lambda x: (x["attributes"].get("sortIndex") or 0, x["attributes"].get("title", "")))
    sections = {x["id"]: x["attributes"].get("title", "") for x in secs}
    sec_order = {x["id"]: i for i, x in enumerate(secs)}
    comments = defaultdict(list)
    for x in inc:
        if x["type"] == "comment":
            pid = x.get("relationships", {}).get("post", {}).get("data", {}).get("id")
            comments[pid].append({"author": x.get("relationships", {}).get("author", {}).get("data", {}).get("id"),
                                  "text": x["attributes"].get("content", "")})
    my_name = (me or {}).get("username")
    posts = []
    for x in inc:
        if x["type"] != "post":
            continue
        a = x["attributes"]
        author = a.get("author") or {}
        if my_name and author.get("username") == my_name:
            continue
        c = a.get("content") or {}
        urls = []
        if (c.get("attachment") or {}).get("url"):
            urls.append(c["attachment"]["url"])
        urls += re.findall(r'<img[^>]+src="([^"]+)"', c.get("bodyHtml") or "")
        if not a.get("author") and not urls:
            continue   # 沒作者也沒附件：AI 建板產生的說明卡（實測 author 是 null），不是學生作業
        sec = (x.get("relationships", {}).get("section", {}).get("data") or {}).get("id")
        posts.append({
            "id": x["id"],
            "author_username": author.get("username") or "",
            "author_name": author.get("fullName") or author.get("shortName") or author.get("username") or "",
            "subject": _text(c.get("subject")),
            "body": _text(c.get("bodyHtml"))[:500],
            "section": sections.get(sec, ""),
            "section_order": sec_order.get(sec, len(sec_order)),
            "image_urls": urls,
            "web_url": (a.get("webUrl") or {}).get("live", ""),
            "created_at": a.get("createdAt", ""),
            "sort_index": a.get("sortIndex") or 0,
            "my_comments": [cm["text"] for cm in comments[x["id"]] if me and cm["author"] == me.get("id")],
        })
    posts += _comment_submissions(inc, sections, sec_order, my_name, me)
    posts.sort(key=lambda p: (p["created_at"], p["sort_index"]))
    at = raw["data"]["attributes"]
    return {"id": raw["data"]["id"], "title": at.get("title", ""), "url": (at.get("webUrl") or {}).get("live", ""),
            "posts": posts}


def _comment_submissions(inc, sections, sec_order, my_name, me) -> list:
    """學生常把照片「留言」在老師的說明卡底下，而不是按＋新增貼文（2026-09-18 實測）。
    把「說明卡底下、帶照片的留言」也當成繳交：用留言者的帳號 id 分組（不靠學生打的名字），
    欄位取說明卡所在的欄。這種繳交沒有自己的貼文，評語無法自動貼回（target 會是 None）。"""
    cards = {}
    for x in inc:
        if x["type"] != "post":
            continue
        au = x["attributes"].get("author") or {}
        if not au or (my_name and au.get("username") == my_name):   # AI 說明卡或老師自己的貼文
            cards[x["id"]] = x
    out = []
    for x in inc:
        if x["type"] != "comment":
            continue
        rel = x.get("relationships", {})
        pid = (rel.get("post", {}).get("data") or {}).get("id")
        uid = (rel.get("author", {}).get("data") or {}).get("id")
        url = ((x["attributes"].get("attachment") or {}).get("url")) or ""
        if pid not in cards or not url or not uid or (me and uid == me.get("id")):
            continue
        card = cards[pid]
        sec = (card.get("relationships", {}).get("section", {}).get("data") or {}).get("id")
        text = _text(x["attributes"].get("content") or x["attributes"].get("htmlContent"))
        out.append({
            "id": x["id"], "author_username": "id:" + uid, "author_name": "", "subject": text, "body": "",
            "section": sections.get(sec, ""), "section_order": sec_order.get(sec, len(sec_order)),
            "image_urls": [url], "web_url": (card["attributes"].get("webUrl") or {}).get("live", ""),
            "created_at": x["attributes"].get("createdAt", ""), "sort_index": 0, "my_comments": [],
            "via_comment": True,
        })
    return out


def _best_name(posts) -> str:
    """同一位學生各篇寫的名字可能不一樣（例如一篇寫「109-23-林大民」、另一篇只寫「33」），取最常出現、最長的。"""
    names = [p["author_name"] or p["subject"] for p in posts if p["author_name"] or p["subject"]]
    if not names:
        return "（未署名）"
    return max(set(names), key=lambda n: (names.count(n), len(n)))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


GENERIC_NAMES = {"anonymous", "匿名", "guest", "訪客", "someone", ""}


def _name_key(p) -> str:
    """有帳號用帳號；訪客（沒帳號但有填名字）用名字；「匿名」這類通用名稱不能用來合併。"""
    if p["author_username"]:
        return "u:" + p["author_username"]
    n = _norm(p["author_name"]).lower()
    return "" if n in GENERIC_NAMES else "n:" + n


def choose_group_by(posts) -> str:
    """auto：全部有登入 → 依帳號；登入和訪客混在一起但都有名字 → 依帳號或名字；
    都匿名 → 依標題（請學生在標題寫座號姓名）；都沒有 → 一篇一份。"""
    if posts and all(p["author_username"] for p in posts):
        return "author"
    if posts and all(_name_key(p) for p in posts):
        return "name"
    if posts and sum(bool(p["subject"]) for p in posts) >= 0.8 * len(posts):
        return "subject"
    return "post"


def group_posts(posts, group_by: str = "auto") -> tuple:
    """回傳 (實際使用的分組方式, [{key, name, posts}])；留言會貼在每位學生的第一篇貼文下。"""
    if group_by == "auto":
        group_by = choose_group_by(posts)
    groups = {}
    for p in posts:
        key = {"author": p["author_username"], "name": _name_key(p), "subject": _norm(p["subject"]),
               "section": p["section"], "post": p["id"]}[group_by] or p["id"]
        groups.setdefault(key, {"key": key, "posts": []})["posts"].append(p)
    for g in groups.values():
        g["name"] = g["posts"][0]["section"] if group_by == "section" else _best_name(g["posts"])
    return group_by, list(groups.values())


# ---------------------------------------------------------------- 下載照片
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def _get(url: str) -> requests.Response:
    """下載附件。Padlet 的附件網址有簽章，實測偶爾剛拿到時回 403、過幾秒就好，所以 403／5xx 會重試。
    送瀏覽器的 User-Agent：維基百科等網站會擋 Python 預設的。"""
    for wait in (0, 2, 5):
        time.sleep(wait)
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        if r.status_code != 403 and r.status_code < 500:
            break
    r.raise_for_status()
    return r


def download_images(posts, dest: Path, max_images: int = 4, client: "Client | None" = None) -> tuple:
    """下載一位學生所有貼文的照片，回傳 (檔名清單, 問題清單)。非圖片的附件（影片、PDF、連結）會略過並說明。
    原檔下載失敗時，改用 Padlet 產生的預覽圖（attachmentData 的 previewImageUrl）。"""
    dest.mkdir(parents=True, exist_ok=True)
    files, problems = [], []
    for p in posts:
        for i, url in enumerate(p["image_urls"]):
            if len(files) >= max_images:
                problems.append(f"照片超過 {max_images} 張，只取前 {max_images} 張")
                return files, problems
            name = f"{p['id']}_{i}.jpg"
            if (dest / name).exists():
                files.append(name)
                continue
            label = f"貼文「{p['subject'] or p['id']}」的附件"
            try:
                r = _get(url)
            except requests.RequestException as e:
                r = None
                if i == 0 and client:   # 第 0 個是貼文附件，才有 attachmentData
                    try:
                        prev = client.attachment_preview(p["id"])
                        r = _get(prev) if prev else None
                    except (requests.RequestException, PadletError):
                        r = None
                if r is None:
                    code = getattr(e.response, "status_code", "") if getattr(e, "response", None) is not None else ""
                    problems.append(f"{label}下載失敗（{code or type(e).__name__}），可以稍後重跑")
                    continue
            try:
                with Image.open(io.BytesIO(r.content)) as im:
                    im = im.convert("RGB")
                    im.thumbnail((2048, 2048))
                    im.save(dest / name, "JPEG", quality=90)
                files.append(name)
            except Exception:
                kind = (r.headers.get("content-type") or "").split(";")[0] or "未知格式"
                problems.append(f"{label}不是照片（{kind}），略過")
    if not files:
        problems.append("沒有找到任何作品照片")
    return files, problems


# ---------------------------------------------------------------- 本機資料夾（試跑用）
def local_board(folder: Path) -> dict:
    """用本機資料夾模擬作業版：每個子資料夾是一位學生，裡面放作品照片。不能發留言。"""
    folder = Path(folder)
    posts = []
    for sub in sorted(x for x in folder.iterdir() if x.is_dir()):
        imgs = sorted(f for f in sub.iterdir() if f.suffix.lower() in IMAGE_EXT)
        for i, f in enumerate(imgs):
            posts.append({"id": f"{sub.name}#{i}", "author_username": sub.name, "author_name": sub.name,
                          "subject": sub.name, "body": "", "section": "", "image_urls": [],
                          "local_path": str(f), "web_url": "", "created_at": "", "sort_index": i,
                          "my_comments": []})
    if not posts:
        raise PadletError(f"{folder} 底下沒有「每位學生一個子資料夾」的照片")
    return {"id": "local-" + re.sub(r"[^\w-]", "_", folder.name), "title": folder.name, "url": "", "posts": posts}


def copy_local_images(posts, dest: Path, max_images: int = 4) -> tuple:
    dest.mkdir(parents=True, exist_ok=True)
    files, problems = [], []
    for p in posts[:max_images]:
        name = re.sub(r"[^\w-]", "_", p["id"]) + ".jpg"
        try:
            with Image.open(p["local_path"]) as im:
                im = im.convert("RGB")
                im.thumbnail((2048, 2048))
                im.save(dest / name, "JPEG", quality=90)
            files.append(name)
        except Exception as e:
            problems.append(f"{Path(p['local_path']).name} 讀不到（{type(e).__name__}）")
    if len(posts) > max_images:
        problems.append(f"照片超過 {max_images} 張，只取前 {max_images} 張")
    return files, problems
