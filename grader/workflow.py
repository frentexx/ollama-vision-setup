"""批改流程：抓作業 → 產生草稿 → （老師審核）→ 發布到 Padlet → 匯出成績。

命令列（grade.py）與審核頁（server.py）共用這裡的函式。
"""
import csv
import time
from pathlib import Path

import feedback
import padlet
import rubric as rubric_mod
from store import ROOT, Store, now

REUPLOAD = "請重傳"
MAX_IMAGES = 4  # 一位學生最多送幾張給模型（3 張 1536px 約 5,400 tokens，NUM_CTX 16384 放得下）


def load_rubric(st: Store) -> rubric_mod.Rubric:
    p = Path(st.state["rubric_path"])
    return rubric_mod.load(p if p.is_absolute() else ROOT / p)


def _rel(p: Path) -> str:
    p = Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- 1. 抓作業
def fetch(source: str, rubric_path: str, group_by: str = "auto", log=print) -> Store:
    """source：Padlet 網址／board id，或本機資料夾（每位學生一個子資料夾）。已存在的學生保留原本的草稿與審核結果。"""
    rubric_mod.load(rubric_path)   # 先確認 rubric 寫得對，免得跑到一半才出錯
    src = Path(source)
    if src.is_dir():
        board, me, cli = padlet.local_board(src), None, None
    else:
        cli = padlet.Client()
        me = cli.me()
        board = padlet.parse_board(cli.board(padlet.board_id_from(source)), me)
    used, groups = padlet.group_posts(board["posts"], group_by)

    st = Store(board["id"])
    st.state.update({"board": {k: board[k] for k in ("id", "title", "url")}, "source": "local" if me is None else "padlet",
                     "rubric_path": _rel(rubric_path), "group_by": used, "fetched_at": now()})
    log(f"作業版「{board['title']}」：{len(board['posts'])} 篇貼文，依「{used}」分成 {len(groups)} 位學生")

    for i, g in enumerate(groups):
        s = st.students.get(g["key"])
        # 依欄位排序（① 全貌 → ② 特寫 → ③ 過程），留言貼在第一欄那篇
        g["posts"].sort(key=lambda p: (p.get("section_order", 0), p["created_at"], p["sort_index"]))
        post_ids = [p["id"] for p in g["posts"]]
        if s and s["status"] in ("approved", "posted", "skipped"):
            s["order"] = i
            continue   # 老師處理過的不動
        if s and s.get("post_ids") == post_ids and s["status"] == "drafted":
            s["order"] = i
            continue   # 沒有新照片，沿用草稿
        dest = st.images / _safe(g["key"])
        if me is None:
            files, problems = padlet.copy_local_images(g["posts"], dest, MAX_IMAGES)
        else:
            files, problems = padlet.download_images(g["posts"], dest, MAX_IMAGES, client=cli)
        already = [c for p in g["posts"] for c in p["my_comments"]]
        if already:
            problems.append("你在 Padlet 上已經留過言了，確認是否還要再發")
        typed = {p["subject"] for p in g["posts"] if p["subject"] and p.get("author_name")}
        odd = [t for t in typed if padlet._norm(t) not in padlet._norm(g["name"]) and padlet._norm(g["name"]) not in padlet._norm(t)]
        if odd:   # 實測：帳號名稱「109-23-林大民」，標題卻寫「1-10-99李小名」
            problems.append(f"標題寫「{'」「'.join(sorted(odd))}」，但 Padlet 帳號是「{g['name']}」，請確認是誰的作品")
        own = [p for p in g["posts"] if not p.get("via_comment")]   # 學生自己的貼文，評語才貼得回去
        if len(own) < len(g["posts"]):
            problems.append("有照片是留言在老師說明卡底下（不是新貼文）" +
                            ("" if own else "，評語無法自動貼回，請手動回覆或請學生改用＋新增貼文"))
        st.students[g["key"]] = {
            "key": g["key"], "name": g["name"], "order": i, "post_ids": post_ids,
            "target_post": own[0]["id"] if own else None, "web_url": g["posts"][0]["web_url"],
            "subject": g["posts"][0]["subject"], "body": g["posts"][0]["body"],
            "images": [f"{_safe(g['key'])}/{f}" for f in files], "problems": problems,
            # 每張照片來自哪一欄（檔名開頭是貼文 id），給模型知道哪張是全貌、哪張是特寫
            "image_labels": [{p["id"]: p.get("section", "") for p in g["posts"]}.get(f.rsplit("_", 1)[0], "")
                             for f in files],
            "status": "new" if files else "error", "error": "" if files else "沒有可讀的作品照片",
            "ai": None, "levels": {}, "mode": "", "comment": "", "teacher_note": "", "flags": [],
            "history": [f"{now()} 抓取 {len(files)} 張照片"],
        }
        log(f"  {g['name']}：{len(files)} 張" + (f"（{'；'.join(problems)}）" if problems else ""))
    # Padlet 上已經找不到的學生（貼文被刪、分組方式變了）：還沒處理的直接移除；已核准／已發布的保留並提醒
    current = {g["key"] for g in groups}
    for k in [k for k in st.students if k not in current]:
        s = st.students[k]
        if s["status"] in ("approved", "posted"):
            if "Padlet 上已找不到這位學生的貼文" not in s["flags"]:
                s["flags"].append("Padlet 上已找不到這位學生的貼文")
        else:
            log(f"  移除：{s['name']}（Padlet 上已找不到）")
            del st.students[k]
    st.save()
    return st


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)[:60] or "x"


# ---------------------------------------------------------------- 2. 產生草稿
def draft_one(st: Store, sid: str, rb=None, rejudge: bool = True, teacher_hint: str = "") -> dict:
    """rejudge=True：重新看圖＋寫留言；False：沿用老師改過的等級，只重寫留言。"""
    rb = rb or load_rubric(st)
    s = st.students[sid]
    if rejudge or not s.get("ai"):
        s["ai"] = feedback.judge(rb, [st.images / p for p in s["images"]], s.get("image_labels"))
        # 老師負責的項目（ai = false）保留老師已選的，沒選過就留空等老師在審核頁選
        kept = {c.key: s["levels"][c.key] for c in rb.criteria if not c.ai and s["levels"].get(c.key)}
        s["levels"] = {**kept, **{c.key: s["ai"]["criteria"][c.key]["level"] for c in rb.ai_criteria}}
        s["mode"] = feedback.pick_mode(rb, s["levels"])
        if not s["ai"].get("is_artwork", True):
            s["mode"] = REUPLOAD   # 拍的不是作品：不寫評語，請學生重傳
    if s["mode"] == REUPLOAD:
        # 固定文字，不交給模型：避免把教室照、人物照當成作品來稱讚
        c = {"text": rb.reupload_text, "had_simplified": False}
    else:
        c = feedback.compose(rb, s["ai"], s["levels"], s["mode"], teacher_hint)
    s["comment"] = c["text"]
    s["flags"] = _flags(rb, s, c)
    s["status"], s["error"] = "drafted", ""
    st.log(sid, f"{'看圖＋' if rejudge else ''}寫評語（{s['mode']}）")
    st.save()
    return s


def _flags(rb, s, c) -> list:
    """提醒老師特別看一下的地方。"""
    f = []
    if s["mode"] == REUPLOAD:
        f.append("AI 判斷照片拍的不是作品，評語改成請學生重傳。如果其實是作品，請按「鼓勵」或「修正建議」重寫")
    if s["ai"].get("photo_issue"):
        f.append(f"照片問題：{s['ai']['photo_issue']}")
    unk = [x.name for x in rb.ai_criteria if s["levels"].get(x.key) == rubric_mod.UNKNOWN]
    if unk:
        f.append(f"AI 無法判斷：{'、'.join(unk)}")
    if c.get("praise_conflict"):
        f.append(f"第一句可能稱讚了還沒做到的地方：{'、'.join(c['praise_conflict'])}")
    if c.get("directive"):
        f.append(f"評語可能直接給答案或語氣太重：「{'」「'.join(c['directive'])}」")
    if c["had_simplified"]:
        f.append("評語原本有簡體字，已自動轉換，請留意用詞")
    return f + s.get("problems", [])


def draft_all(st: Store, redo: bool = False, log=print):
    rb = load_rubric(st)
    todo = [s for s in st.ordered() if s["status"] == "new" or (redo and s["status"] in ("drafted", "error") and s["images"])]
    t0 = time.perf_counter()
    for i, s in enumerate(todo, 1):
        try:
            draft_one(st, s["key"], rb)
            log(f"  [{i}/{len(todo)}] {s['name']}：{s['mode']}，{s['ai']['seconds'] + 0:.0f} 秒")
        except Exception as e:
            s["status"], s["error"] = "error", f"{type(e).__name__}: {e}"[:300]
            st.save()
            log(f"  [{i}/{len(todo)}] {s['name']}：失敗 {s['error']}")
    log(f"草稿完成 {len(todo)} 位，共 {(time.perf_counter() - t0) / 60:.1f} 分鐘")


# ---------------------------------------------------------------- 3. 發布
def publish(st: Store, sids=None, log=print) -> dict:
    """把「已核准」的評語貼到學生的第一篇貼文下。只有 Padlet 來源能發；已發過的不會重發。"""
    if st.state.get("source") != "padlet":
        raise padlet.PadletError("這是本機資料夾試跑，不能發到 Padlet")
    rb = load_rubric(st)
    cli = padlet.Client()
    ok, fail = [], []
    for s in st.ordered():
        if s["status"] != "approved" or (sids and s["key"] not in sids):
            continue
        if not s.get("target_post"):
            s["error"] = "這位學生只用留言繳交，沒有自己的貼文可以貼評語，請到 Padlet 手動回覆"
            fail.append(f"{s['name']}（沒有可貼評語的貼文）")
            st.save()
            continue
        try:
            html = feedback.to_html(s["comment"], s.get("teacher_note", ""), rb.disclose_ai)
            c = cli.comment(s["target_post"], html)
            s["status"], s["posted"] = "posted", {"comment_id": c["id"], "at": now(), "html": html}
            st.log(s["key"], "已發布到 Padlet")
            ok.append(s["name"])
        except Exception as e:
            s["error"] = f"發布失敗：{e}"[:300]
            fail.append(f"{s['name']}（{e}）")
        st.save()   # 每發一則就存，中途斷掉也不會重複發
        time.sleep(0.3)
    log(f"發布成功 {len(ok)} 則" + (f"，失敗 {len(fail)} 則：{'；'.join(fail)}" if fail else ""))
    return {"ok": ok, "fail": fail}


# ---------------------------------------------------------------- 4. 摘要與成績
def summarize(st: Store) -> dict:
    rb = load_rubric(st)
    done = [s for s in st.ordered() if s.get("ai") and s["status"] != "skipped"]
    if not done:
        raise ValueError("還沒有任何草稿")
    st.state["summary"] = {**feedback.class_summary(rb, done), "at": now(), "n": len(done)}
    st.save()
    return st.state["summary"]


def export_csv(st: Store) -> Path:
    rb = load_rubric(st)
    out = st.dir / f"成績-{rb.title}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig：Excel 直接開不會亂碼
        w = csv.writer(f)
        w.writerow(["學生", "狀態", *[c.name for c in rb.criteria], *(["得分", "滿分", "未評項目"] if rb.points else []), "評語"])
        for s in st.ordered():
            sc = rb.score(s["levels"]) if s.get("ai") else None   # 沒有草稿（沒照片）的不算分
            w.writerow([s["name"], s["status"], *[s["levels"].get(c.key, "") for c in rb.criteria],
                        *([sc[0], sc[1], "、".join(sc[2])] if sc else ["", "", ""] if rb.points else []),
                        (s["comment"] + ("\n" + s["teacher_note"] if s.get("teacher_note") else ""))])
    return out
