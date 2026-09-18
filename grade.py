"""美術課 AI 評語小幫手：讀 Padlet 作品 → AI 依 rubric 寫草稿 → 老師審核 → 發回 Padlet。

常用（在 repo 資料夾執行）：
  一次做完抓作業＋寫草稿＋開審核頁：
    .venv\\Scripts\\python.exe -X utf8 grade.py run <Padlet網址> --rubric rubrics\\範例-攝影三作業.toml
  用本機資料夾試跑（每位學生一個子資料夾，不會發到 Padlet）：
    .venv\\Scripts\\python.exe -X utf8 grade.py run D:\\試跑作品 --rubric rubrics\\範例-攝影三作業.toml
  只開審核頁（接著上次的進度）：
    .venv\\Scripts\\python.exe -X utf8 grade.py review
詳細說明見 GRADING.md。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "grader"))
import padlet  # noqa: E402
import workflow  # noqa: E402
from store import Store, list_boards  # noqa: E402


def _board_store(ref: str) -> Store:
    """ref 可以是 board id、Padlet 網址、本機資料夾；空的就用最近一次的作業版。"""
    if not ref:
        b = list_boards()
        if not b:
            sys.exit("還沒有任何作業版，請先執行 grade.py run 或 fetch")
        return Store(b[0]["id"])
    if Path(ref).is_dir():
        return Store(padlet.local_board(Path(ref))["id"])
    st = Store(ref)
    if not st.state:
        st = Store(padlet.board_id_from(ref))
    if not st.state:
        sys.exit(f"找不到 {ref} 的進度，請先執行 grade.py fetch")
    return st


def main():
    ap = argparse.ArgumentParser(description="美術課 AI 評語小幫手")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def src_args(p):
        p.add_argument("source", help="Padlet 網址／board id，或本機資料夾")
        p.add_argument("--rubric", required=True, help="評分規準 .toml")
        p.add_argument("--group-by", default="auto", choices=["auto", "author", "name", "subject", "section", "post"],
                       help="怎麼把貼文分給學生（預設 auto：有登入依帳號，訪客依名字，匿名依標題）")
        p.add_argument("--unit", default="auto", choices=["auto", "section", "student"],
                       help="section：每個區段是一份作業，分開評；student：整面牆一份作業，各區段照片合起來評；"
                            "auto（預設）：rubric 有 [[assignments]] 就用 section")

    p = sub.add_parser("run", help="抓作業＋寫草稿＋開審核頁")
    src_args(p)
    p.add_argument("--port", type=int, default=0)
    p = sub.add_parser("fetch", help="只抓作業與照片")
    src_args(p)
    p = sub.add_parser("draft", help="替還沒有草稿的學生寫草稿")
    p.add_argument("board", nargs="?", default="")
    p.add_argument("--redo", action="store_true", help="已有草稿但還沒核准的也重寫")
    p = sub.add_parser("review", help="開審核頁")
    p.add_argument("board", nargs="?", default="")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true", help="不要自動打開瀏覽器")
    p = sub.add_parser("export", help="匯出成績與評語 CSV")
    p.add_argument("board", nargs="?", default="")
    sub.add_parser("list", help="列出處理過的作業版")
    p = sub.add_parser("check", help="確認 Padlet API key 可用，並列出看得到的貼文")
    p.add_argument("source")
    sub.add_parser("set-password", help="設定審核頁的登入密碼（開放校內網路時必須設定）")

    a = ap.parse_args()
    try:
        if a.cmd in ("run", "fetch"):
            st = workflow.fetch(a.source, a.rubric, a.group_by, unit=a.unit)
            if a.cmd == "run":
                workflow.draft_all(st)
                import server
                server.serve(a.port)
        elif a.cmd == "draft":
            workflow.draft_all(_board_store(a.board), redo=a.redo)
        elif a.cmd == "review":
            _board_store(a.board)   # 確認有進度
            import server
            server.serve(a.port, open_browser=not a.no_browser)
        elif a.cmd == "export":
            print(workflow.export_csv(_board_store(a.board)))
        elif a.cmd == "set-password":
            import getpass
            import auth
            pw = getpass.getpass("審核頁新密碼（輸入時畫面不會顯示）：")
            if pw != getpass.getpass("再輸入一次：") or not pw:
                sys.exit("兩次不一樣或是空的，沒有更改")
            if len(pw) < 8 or pw.isdigit():
                print("⚠️ 這個密碼很容易被猜到。開放校內網路時，學生也連得到登入頁；"
                      "拿到密碼的人可以用你的 Padlet 帳號發評語。建議 8 碼以上、英數混合。")
            auth.set_env({"GRADER_PASSWORD_HASH": auth.hash_password(pw)})
            print("已設定（.env 只存雜湊，不存密碼本身）。重開審核頁後生效")
        elif a.cmd == "list":
            for b in list_boards():
                print(f"{b['id']}\t{b['n']} 位\t{b['title']}\t{b['rubric']}")
        elif a.cmd == "check":
            cli = padlet.Client()
            me = cli.me()
            print(f"API key 正常：{me.get('name') or me.get('username')}")
            b = padlet.parse_board(cli.board(padlet.board_id_from(a.source)), me)
            used, groups = padlet.group_posts(b["posts"])
            print(f"作業版「{b['title']}」：{len(b['posts'])} 篇學生貼文，依「{used}」分成 {len(groups)} 位")
            for g in groups:
                n = sum(len(p["image_urls"]) for p in g["posts"])
                print(f"  {g['name']}：{len(g['posts'])} 篇、{n} 個附件")
    except (padlet.PadletError, ValueError) as e:
        sys.exit(f"錯誤：{e}")


if __name__ == "__main__":
    main()
