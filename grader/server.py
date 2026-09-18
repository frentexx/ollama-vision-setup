"""老師審核頁：預設只給本機（127.0.0.1:8765）。
.env 設 GRADER_HOST=0.0.0.0 可開放校內網路（方案 C），但必須先用 grade.py set-password 設密碼。"""
import functools
import html
import io
import os
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, session

import auth
import feedback
import padlet
import portal
import workflow
from store import Store, list_boards

HERE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.json.sort_keys = False   # 保留 rubric 的項目與等級順序
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")


@app.before_request
def _require_login():
    """設了密碼就每一頁都要登入（本機也一樣，規則單純）。"""
    if not auth.password_hash() or request.path in ("/login", "/logout", "/theme.css") or session.get("ok"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="請先登入（重新整理頁面）"), 401
    return redirect("/login")


LOGIN_PAGE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>AI 評語小幫手：登入</title>
<link rel="stylesheet" href="/theme.css">
<style>body{{min-height:100vh;display:grid;place-items:center;padding:16px}}
form{{width:min(340px,100%)}}form input{{width:100%}}form button{{margin-top:12px;width:100%}}
.brand{{margin-bottom:18px}}</style>
<form method="post" class="panel"><div class="brand"><span class="mark">評</span><span><b>AI 評語小幫手</b><small>Art Feedback Assistant</small></span></div>
{msg}<input type="password" name="pw" placeholder="密碼" aria-label="密碼" autofocus><button class="primary">登入</button></form></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if auth.attempt(ip, request.form.get("pw", "")):
            session.clear()
            session["ok"] = True
            session.permanent = False   # 關掉瀏覽器就登出
            return redirect("/portal")   # 老師登入後預設進作業牆管理
        wait = auth.locked(ip)
        msg = f"錯誤太多次，請 {wait // 60 + 1} 分鐘後再試" if wait else "密碼錯誤"
        msg = f'<p class="err">{html.escape(msg)}</p>'
    return LOGIN_PAGE.format(msg=msg)


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


EDITABLE = ("comment", "teacher_note", "mode")
_write = threading.Lock()


def serialized(f):
    """會改進度或呼叫模型的請求一次只處理一個，避免同時寫檔互相蓋掉、或同時搶顯卡。"""
    @functools.wraps(f)
    def wrap(*a, **kw):
        if kw.get("bid") in portal.busy():
            abort(409, "這面牆正在背景抓作業／寫草稿，請等完成後重新整理")
        with _write:
            return f(*a, **kw)
    return wrap


def _store(bid: str) -> Store:
    st = Store(bid)
    if not st.state:
        abort(404, f"找不到作業版 {bid}")
    return st


def _view(st: Store) -> dict:
    rb = workflow.load_rubric(st)
    students = []
    for s in st.ordered():
        r = workflow.rubric_for(rb, s)   # 每個區段一份作業時，各作品的檢查點不同
        sc = r.score(s["levels"]) if s.get("ai") else None
        # 「請老師判斷」即時計算：老師選了等級就不再提醒
        todo = [c.name for c in r.criteria if not c.ai and not s["levels"].get(c.key)]
        flags = ([f"請老師判斷：{'、'.join(todo)}"] if todo and s["status"] != "posted" else []) + s["flags"]
        students.append({**s, "flags": flags, "score": list(sc) if sc else None,
                         "criteria": [{"key": c.key, "name": c.name, "look_for": c.look_for, "ai": c.ai} for c in r.criteria],
                         "preview_html": feedback.to_html(s["comment"], s.get("teacher_note", ""), rb.disclose_ai)})
    return {
        "board": st.state["board"], "source": st.state.get("source"), "group_by": st.state.get("group_by"),
        "fetched_at": st.state.get("fetched_at"), "summary": st.state.get("summary"),
        "rubric": {"title": rb.title, "task": rb.task, "levels": rb.levels, "has_points": bool(rb.points)},
        "students": students,
    }


@app.errorhandler(Exception)
def _err(e):
    if type(e) is LookupError or isinstance(e, (ValueError, padlet.PadletError)):   # 給老師看的訊息，原樣顯示
        return jsonify(error=str(e)), 404 if type(e) is LookupError else 400
    code = getattr(e, "code", 500)
    return jsonify(error=getattr(e, "description", None) or f"{type(e).__name__}: {e}"), code if isinstance(code, int) else 500


@app.get("/theme.css")
def theme():
    return send_from_directory(HERE / "static", "theme.css")


@app.get("/")
def index():
    return send_from_directory(HERE / "static", "review.html")


@app.get("/api/boards")
def boards():
    return jsonify(list_boards())


@app.get("/api/board/<bid>")
def board(bid):
    return jsonify(_view(_store(bid)))


@app.get("/img/<bid>/<path:p>")
def img(bid, p):
    return send_from_directory(_store(bid).images, p)


@app.post("/api/board/<bid>/update")
@serialized
def update(bid):
    st, d = _store(bid), request.get_json()
    s = st.students.get(d["sid"]) or abort(404, "找不到這位學生")
    if s["status"] == "posted":
        abort(409, "已經發布到 Padlet，不能再改（要修改請到 Padlet 上手動處理）")
    for k in EDITABLE:
        if k in d:
            s[k] = d[k]
    if "levels" in d:
        s["levels"].update({k: v for k, v in d["levels"].items() if v})
    act = d.get("action")
    if act == "approve":
        if not s["comment"].strip():
            abort(400, "評語是空的，不能核准")
        s["status"] = "approved"
    elif act == "skip":
        s["status"] = "skipped"
    elif act == "reopen":
        s["status"] = "drafted" if s.get("ai") else "new"
    if act:
        st.log(d["sid"], {"approve": "老師核准", "skip": "老師略過", "reopen": "退回重審"}[act])
    st.save()
    return jsonify(_view(st))


@app.post("/api/board/<bid>/redraft")
@serialized
def redraft(bid):
    st, d = _store(bid), request.get_json()
    s = st.students.get(d["sid"]) or abort(404, "找不到這位學生")
    if s["status"] in ("posted", "approved"):
        abort(409, "已核准或已發布的評語要先「退回重審」才能重寫")
    if "mode" in d:
        s["mode"] = d["mode"]
    if "levels" in d:
        s["levels"].update({k: v for k, v in d["levels"].items() if v})
    rb = workflow.load_rubric(st)
    if not d.get("rejudge") and "mode" not in d and s["mode"] != workflow.REUPLOAD:
        s["mode"] = feedback.pick_mode(workflow.rubric_for(rb, s), s["levels"])   # 改了等級就重新決定模式；「請重傳」要老師手動切換
    workflow.draft_one(st, d["sid"], rb, rejudge=bool(d.get("rejudge")), teacher_hint=d.get("hint", ""))
    return jsonify(_view(st))


@app.post("/api/board/<bid>/summary")
@serialized
def summary(bid):
    st = _store(bid)
    workflow.summarize(st)
    return jsonify(_view(st))


@app.post("/api/board/<bid>/publish")
@serialized
def publish(bid):
    st, d = _store(bid), request.get_json()
    if d.get("confirm") != "發布":
        abort(400, "缺少確認")
    r = workflow.publish(st, log=lambda *_: None)
    return jsonify({**_view(st), "result": r})


@app.get("/api/board/<bid>/export")
def export(bid):
    p = workflow.export_csv(_store(bid))
    return send_file(p, as_attachment=True, download_name=p.name)


# ---------------------------------------------------------------- 作業牆建立與後台（portal.py）
@app.get("/portal")
def portal_page():
    return send_from_directory(HERE / "static", "portal.html")


@app.get("/api/portal")
def portal_all():
    return jsonify(templates=portal.templates(), rubrics=portal.rubrics(), todos=portal.TODOS,
                   boards=[portal.view(b) for b in portal.boards()], jobs=portal.running())


@app.post("/api/portal/create")
def portal_create():
    p = portal.prepare(request.get_json())
    return jsonify(portal.start("create", lambda job: portal.create_board(p, job)))


@app.get("/api/portal/jobs/<jid>")
def portal_job(jid):
    return jsonify(portal.job(jid))


@app.post("/api/portal/boards/<bid>")
def portal_board(bid):
    d = request.get_json()
    if "todo" in d:
        b = portal.set_todo(bid, d["todo"], d.get("done"))
    elif "rubric" in d:
        b = portal.set_rubric(bid, d["rubric"])
    else:
        abort(400, "沒有要改的內容")
    return jsonify(portal.view(b))


@app.post("/api/portal/boards/<bid>/count")
def portal_count(bid):
    return jsonify(portal.count(bid))


@app.post("/api/portal/boards/<bid>/grade")
def portal_grade(bid):
    portal.get(bid)
    return jsonify(portal.start("grade", lambda job: portal.grade(bid, job), board=bid))


@app.post("/api/portal/boards/<bid>/forget")
def portal_forget(bid):
    if bid in portal.busy():
        abort(409, "這面牆正在背景處理中，請等它完成")
    portal.forget(bid)
    return jsonify(ok=True)


@app.get("/api/portal/boards/<bid>/qr")
def portal_qr(bid):
    png, name = portal.qr_png(bid)
    return send_file(io.BytesIO(png), mimetype="image/png", as_attachment=True, download_name=name)


@app.post("/api/portal/templates")
def portal_template_save():
    return jsonify(portal.save_template(request.get_json()))


@app.post("/api/portal/templates/<tid>/delete")
def portal_template_delete(tid):
    return jsonify(portal.delete_template(tid))


@app.get("/api/portal/rubric")
def portal_rubric_read():
    return jsonify(portal.read_rubric(request.args.get("path", "")))


@app.post("/api/portal/rubric")
def portal_rubric_save():
    d = request.get_json()
    return jsonify(portal.save_rubric(d.get("name", ""), d.get("text", ""), bool(d.get("overwrite"))))


def _lan_ips() -> list:
    import socket
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                       if not a[4][0].startswith(("127.", "169.254."))})
    except OSError:
        return []


def serve(port: int = 0, open_browser: bool = True):
    port = port or int(os.environ.get("GRADER_PORT", "8765"))
    host = os.environ.get("GRADER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost") and not auth.password_hash():
        raise SystemExit("GRADER_HOST 開放到網路時一定要有密碼：請先執行 grade.py set-password")
    app.secret_key = auth.secret_key()
    url = f"http://127.0.0.1:{port}/"
    print(f"審核頁：{url}（按 Ctrl+C 結束）")
    if host not in ("127.0.0.1", "localhost"):
        for ip in _lan_ips():
            print(f"  校內其他電腦：http://{ip}:{port}/（需要密碼；Windows 防火牆也要允許 {port} 連接埠）")
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # 預設只綁 127.0.0.1。要多執行緒：瀏覽器會保留連線，單執行緒會卡住其他請求
    app.run(host=host, port=port, threaded=True, debug=False)
