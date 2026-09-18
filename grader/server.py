"""老師審核頁：預設只給本機（127.0.0.1:8765）。
.env 設 GRADER_HOST=0.0.0.0 可開放校內網路（方案 C），但必須先用 grade.py set-password 設密碼。"""
import functools
import html
import os
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, session

import auth
import feedback
import workflow
from store import Store, list_boards

HERE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.json.sort_keys = False   # 保留 rubric 的項目與等級順序
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")


@app.before_request
def _require_login():
    """設了密碼就每一頁都要登入（本機也一樣，規則單純）。"""
    if not auth.password_hash() or request.path in ("/login", "/logout") or session.get("ok"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="請先登入（重新整理頁面）"), 401
    return redirect("/login")


LOGIN_PAGE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>評語審核：登入</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f5f2;
font:15px/1.6 "Microsoft JhengHei",system-ui,sans-serif;color:#1f2328}}
form{{background:#fff;border:1px solid #e3e1dc;border-radius:12px;padding:24px 28px;width:min(320px,90vw)}}
h1{{font-size:18px;margin:0 0 12px}}input{{width:100%;box-sizing:border-box;font:inherit;padding:8px 10px;
border:1px solid #e3e1dc;border-radius:6px}}button{{margin-top:12px;width:100%;font:inherit;padding:8px;border:0;
border-radius:8px;background:#2f6f5e;color:#fff;cursor:pointer}}.err{{color:#a3322b;font-size:14px}}</style>
<form method="post"><h1>評語審核</h1>{msg}<input type="password" name="pw" placeholder="密碼" autofocus>
<button>登入</button></form></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if auth.attempt(ip, request.form.get("pw", "")):
            session.clear()
            session["ok"] = True
            session.permanent = False   # 關掉瀏覽器就登出
            return redirect("/")
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
        sc = rb.score(s["levels"]) if s.get("ai") else None
        # 「請老師判斷」即時計算：老師選了等級就不再提醒
        todo = [c.name for c in rb.criteria if not c.ai and not s["levels"].get(c.key)]
        flags = ([f"請老師判斷：{'、'.join(todo)}"] if todo and s["status"] != "posted" else []) + s["flags"]
        students.append({**s, "flags": flags, "score": list(sc) if sc else None,
                         "preview_html": feedback.to_html(s["comment"], s.get("teacher_note", ""), rb.disclose_ai)})
    return {
        "board": st.state["board"], "source": st.state.get("source"), "group_by": st.state.get("group_by"),
        "fetched_at": st.state.get("fetched_at"), "summary": st.state.get("summary"),
        "rubric": {"title": rb.title, "task": rb.task, "levels": rb.levels, "has_points": bool(rb.points),
                   "criteria": [{"key": c.key, "name": c.name, "look_for": c.look_for, "ai": c.ai} for c in rb.criteria]},
        "students": students,
    }


@app.errorhandler(Exception)
def _err(e):
    code = getattr(e, "code", 500)
    return jsonify(error=getattr(e, "description", None) or f"{type(e).__name__}: {e}"), code if isinstance(code, int) else 500


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
        s["mode"] = feedback.pick_mode(rb, s["levels"])   # 改了等級就重新決定模式；「請重傳」要老師手動切換
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
