"""批改系統的離線測試：Padlet 回應解析、分組、發布流程（不連 Padlet、不呼叫模型）。

用法：.venv\\Scripts\\python.exe -X utf8 -m unittest tests\\test_grader.py -v
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "grader"))
import feedback  # noqa: E402
import padlet  # noqa: E402
import rubric  # noqa: E402
import store  # noqa: E402
import workflow  # noqa: E402

ME = {"id": "user_teacher", "username": "teacher_wang", "name": "王老師"}


def post(pid, username, full, subject, url, section="sec_1", created="2026-09-18T01:00:00Z"):
    return {"id": pid, "type": "post", "attributes": {
        # 訪客貼文：沒有 username 但有 fullName（實測 Padlet 回傳就是這樣）；完全匿名則沒有 author
        "author": {"username": username or "", "fullName": full} if (username or full) else None,
        "sortIndex": 1, "content": {"subject": subject, "bodyHtml": "<p>我的作品</p>",
                                    "attachment": {"url": url, "caption": ""} if url else None},
        "status": "approved", "webUrl": {"live": f"https://padlet.com/x/wish/{pid}"}, "createdAt": created},
        "relationships": {"section": {"data": {"id": section, "type": "section"}}}}


def board_json(posts, comments=()):
    return {"data": {"id": "abcd1234efgh5678", "type": "board",
                     "attributes": {"title": "802 色彩練習", "webUrl": {"live": "https://padlet.com/x/abcd1234efgh5678"}}},
            "included": [{"id": "sec_1", "type": "section", "attributes": {"title": "第一組"}}, *posts, *comments]}


class ParseTest(unittest.TestCase):
    def test_board_id_from_url(self):
        self.assertEqual(padlet.board_id_from("https://padlet.com/teacher/802-abcd1234efgh5678"), "abcd1234efgh5678")
        self.assertEqual(padlet.board_id_from("https://padlet.com/t/x-abcd1234efgh5678ijkl?foo=1"), "abcd1234efgh5678ijkl")
        with self.assertRaises(padlet.PadletError):
            padlet.board_id_from("https://padlet.com/teacher/")

    def test_group_by_author_and_skip_teacher(self):
        raw = board_json([
            post("p1", "s01", "王小明", "作品1", "https://x/1.jpg", created="2026-09-18T01:00:00Z"),
            post("p2", "s01", "王小明", "作品2", "https://x/2.jpg", created="2026-09-18T01:05:00Z"),
            post("p3", "s02", "陳小華", "作品", "https://x/3.jpg"),
            post("p0", "teacher_wang", "王老師", "作業說明", None),
        ], [{"id": "c1", "type": "comment", "attributes": {"content": "很棒"},
             "relationships": {"post": {"data": {"id": "p3"}}, "author": {"data": {"id": "user_teacher"}}}}])
        b = padlet.parse_board(raw, ME)
        self.assertEqual([p["id"] for p in b["posts"]], ["p1", "p3", "p2"], "依上傳時間排序")
        self.assertNotIn("p0", [p["id"] for p in b["posts"]], "老師自己的作業說明不能算成學生")
        used, groups = padlet.group_posts(b["posts"])
        self.assertEqual(used, "author")
        g = {x["name"]: x for x in groups}
        self.assertEqual([p["id"] for p in g["王小明"]["posts"]], ["p1", "p2"])
        self.assertEqual(g["陳小華"]["posts"][0]["my_comments"], ["很棒"])

    def test_group_anonymous_by_subject(self):
        raw = board_json([post("p1", None, None, "12 王小明", "https://x/1.jpg"),
                          post("p2", None, None, "12王小明 ", "https://x/2.jpg"),
                          post("p3", None, None, "13 陳小華", "https://x/3.jpg")])
        used, groups = padlet.group_posts(padlet.parse_board(raw, ME)["posts"])
        self.assertEqual(used, "subject")
        self.assertEqual(sorted(len(g["posts"]) for g in groups), [1, 2], "標題空白不同也要算同一人")

    def test_group_mixed_login_and_guests_by_name(self):
        raw = board_json([post("p1", "s01", "王小明", "", "https://x/1.jpg"),
                          post("p2", "s01", "王小明", "", "https://x/2.jpg"),
                          post("p3", "", "Naima Dutta", "", "https://x/3.jpg"),
                          post("p4", "", "Naima  Dutta", "", "https://x/4.jpg"),
                          post("p5", "", "Kashvi Gupta", "", "https://x/5.jpg")])
        used, groups = padlet.group_posts(padlet.parse_board(raw, ME)["posts"])
        self.assertEqual(used, "name")
        self.assertEqual(sorted(len(g["posts"]) for g in groups), [1, 2, 2])

    def test_anonymous_name_is_never_merged(self):
        raw = board_json([post("p1", "s01", "王小明", "", "https://x/1.jpg"),
                          post("p2", "", "Anonymous", "", "https://x/2.jpg"),
                          post("p3", "", "Anonymous", "", "https://x/3.jpg")])
        used, groups = padlet.group_posts(padlet.parse_board(raw, ME)["posts"])
        self.assertEqual(used, "post", "有匿名貼文時不能用名字合併")
        self.assertEqual(len(groups), 3)

    def test_skip_ai_instruction_cards_and_order_by_section(self):
        """AI 建板產生的說明卡 author 是 null、沒附件 → 略過；分欄順序依 section 的 sortIndex。"""
        raw = board_json([
            post("card", None, None, "① 作品全貌：怎麼拍", None, section="sec_1"),
            post("p3", "s01", "王小明", "12 王小明", "https://x/3.jpg", section="sec_3", created="2026-09-18T01:00:00Z"),
            post("p1", "s01", "王小明", "12 王小明", "https://x/1.jpg", section="sec_1", created="2026-09-18T01:05:00Z"),
        ])
        raw["included"] = [{"id": "sec_3", "type": "section", "attributes": {"title": "③ 過程", "sortIndex": 9}},
                           *raw["included"]]
        raw["included"][1]["attributes"]["sortIndex"] = 1   # sec_1「第一組」
        b = padlet.parse_board(raw, ME)
        self.assertEqual({p["id"] for p in b["posts"]}, {"p1", "p3"}, "說明卡不能算成學生")
        by = {p["id"]: p for p in b["posts"]}
        self.assertLess(by["p1"]["section_order"], by["p3"]["section_order"])

    def test_comment_submissions_under_cards(self):
        """實測：學生把照片留言在說明卡底下，第三則只寫「33」。要用帳號 id 合併，名字取最常見的。"""
        card = post("card1", None, None, "① 怎麼拍", None, section="sec_1")

        def cm(cid, text, uid="user_stu"):
            return {"id": cid, "type": "comment",
                    "attributes": {"content": text, "attachment": {"url": f"https://x/{cid}.jpg"},
                                   "createdAt": "2026-09-18T02:00:00Z"},
                    "relationships": {"post": {"data": {"id": "card1"}}, "author": {"data": {"id": uid}}}}
        raw = board_json([card], [cm("c1", "109-23-林大民"), cm("c2", "109-23-林大民"), cm("c3", "33"),
                                  cm("c4", "我是老師", uid="user_teacher")])
        b = padlet.parse_board(raw, ME)
        used, groups = padlet.group_posts(b["posts"])
        self.assertEqual(len(groups), 1, "同一帳號的三則留言是同一位學生；老師自己的留言不算")
        self.assertEqual(groups[0]["name"], "109-23-林大民")
        self.assertTrue(all(p.get("via_comment") for p in groups[0]["posts"]))

    def test_inline_images_in_body(self):
        raw = board_json([post("p1", "s01", "王小明", "作品", None)])
        raw["included"][1]["attributes"]["content"]["bodyHtml"] = '<p>看圖</p><img src="https://x/a.png"><img src="https://x/b.png">'
        self.assertEqual(padlet.parse_board(raw, ME)["posts"][0]["image_urls"], ["https://x/a.png", "https://x/b.png"])


class RubricTest(unittest.TestCase):
    def test_example_loads_and_scores(self):
        rb = rubric.load(ROOT / "rubrics" / "範例-色彩練習.toml")
        self.assertEqual([c.ai for c in rb.criteria], [True, True, True, False])
        got, full, missing = rb.score({"c1": "符合", "c2": "部分符合", "c3": "不符合"})
        self.assertEqual((got, full, missing), (6, 12, ["明暗表現"]))

    def test_modes(self):
        rb = rubric.load(ROOT / "rubrics" / "範例-色彩練習.toml")
        self.assertEqual(feedback.pick_mode(rb, {"c1": "符合", "c2": "符合", "c3": "符合"}), "鼓勵")
        self.assertEqual(feedback.pick_mode(rb, {"c1": "符合", "c2": "無法判斷", "c3": "符合"}), "鼓勵")
        self.assertEqual(feedback.pick_mode(rb, {"c1": "符合", "c2": "符合", "c3": "符合", "c4": "不符合"}), "修正建議")
        self.assertEqual([c.name for c in feedback.weakest(rb, {"c1": "部分符合", "c2": "不符合", "c3": "符合"})],
                         ["冷暖對比", "指定內容"])

    def test_bad_toml_message(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.toml"
            p.write_text('title = "x"\n[[criteria]]\nname = 沒加引號\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "雙引號"):
                rubric.load(p)

    def test_html_escapes(self):
        h = feedback.to_html("第一句<b>\n第二句", "老師：加油", disclose_ai=True)
        self.assertIn("&lt;b&gt;", h)
        self.assertEqual(h.count("<p>"), 4)


class PublishTest(unittest.TestCase):
    """發布流程：只發已核准、每則發完就存、重跑不會重複發。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(store, "DATA", self.tmp)
        self.patch.start()
        st = store.Store("abcd1234efgh5678")
        st.state = {"board": {"id": "abcd1234efgh5678", "title": "t", "url": ""}, "source": "padlet",
                    "rubric_path": str(ROOT / "rubrics" / "範例-色彩練習.toml"), "students": {}}
        for i, status in enumerate(["approved", "drafted", "approved", "skipped"]):
            st.students[f"s{i}"] = {"key": f"s{i}", "name": f"學生{i}", "order": i, "status": status,
                                    "target_post": f"post_{i}", "comment": f"評語{i}", "teacher_note": "", "levels": {}}
        st.save()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp)

    def test_publish_only_approved_once(self):
        sent = []
        fake = mock.Mock()
        fake.comment.side_effect = lambda pid, html: sent.append((pid, html)) or {"id": f"cmt_{pid}"}
        with mock.patch.object(padlet, "Client", return_value=fake), mock.patch.object(workflow.time, "sleep"):
            r = workflow.publish(store.Store("abcd1234efgh5678"), log=lambda *_: None)
            self.assertEqual(r["ok"], ["學生0", "學生2"])
            self.assertEqual([p for p, _ in sent], ["post_0", "post_2"])
            self.assertIn("AI 協助草擬", sent[0][1])
            again = workflow.publish(store.Store("abcd1234efgh5678"), log=lambda *_: None)
            self.assertEqual(again["ok"], [], "已發布的不能重發")
        st = store.Store("abcd1234efgh5678")
        self.assertEqual(st.students["s0"]["status"], "posted")
        self.assertEqual(st.students["s0"]["posted"]["comment_id"], "cmt_post_0")
        self.assertEqual(st.students["s1"]["status"], "drafted")

    def test_publish_failure_keeps_approved(self):
        fake = mock.Mock()
        fake.comment.side_effect = [padlet.PadletError("Padlet API 401 NOT_ADMIN"), {"id": "cmt_2"}]
        with mock.patch.object(padlet, "Client", return_value=fake), mock.patch.object(workflow.time, "sleep"):
            r = workflow.publish(store.Store("abcd1234efgh5678"), log=lambda *_: None)
        self.assertEqual(r["ok"], ["學生2"])
        st = store.Store("abcd1234efgh5678")
        self.assertEqual(st.students["s0"]["status"], "approved", "失敗的保留已核准，修好可以重發")
        self.assertIn("NOT_ADMIN", st.students["s0"]["error"])

    def test_local_board_cannot_publish(self):
        st = store.Store("abcd1234efgh5678")
        st.state["source"] = "local"
        with self.assertRaises(padlet.PadletError):
            workflow.publish(st, log=lambda *_: None)


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "PNG")
    return buf.getvalue()


def _resp(status, body=b"", ctype="image/png"):
    m = mock.Mock(status_code=status, content=body, headers={"content-type": ctype})

    def rfs():
        if status >= 400:
            raise padlet.requests.HTTPError(response=m)
    m.raise_for_status = rfs
    return m


class DownloadTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(padlet.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, posts, answers, client=None):
        """answers：{url: [依序回傳的 response]}"""
        calls = []

        def fake_get(url, headers, timeout):
            calls.append(url)
            return answers[url].pop(0)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(padlet.requests, "get", fake_get):
            files, problems = padlet.download_images(posts, Path(d), client=client)
        return files, problems, calls

    def test_non_image_attachment_is_reported(self):
        posts = [{"id": "p1", "subject": "作品", "image_urls": ["https://x/ok.png", "https://x/a.mp3"]}]
        files, problems, _ = self._run(posts, {"https://x/ok.png": [_resp(200, _png())],
                                               "https://x/a.mp3": [_resp(200, b"ID3", "audio/mpeg")]})
        self.assertEqual(files, ["p1_0.jpg"])
        self.assertEqual(problems, ["貼文「作品」的附件不是照片（audio/mpeg），略過"])

    def test_retry_after_403(self):
        """實測：Padlet 附件網址剛拿到時偶爾回 403，重試就好。"""
        posts = [{"id": "p1", "subject": "作品", "image_urls": ["https://x/1.jpg"]}]
        files, problems, calls = self._run(posts, {"https://x/1.jpg": [_resp(403, b"", "text/html"), _resp(200, _png())]})
        self.assertEqual(files, ["p1_0.jpg"])
        self.assertEqual(problems, [])
        self.assertEqual(len(calls), 2)

    def test_fallback_to_preview_image(self):
        posts = [{"id": "p1", "subject": "作品", "image_urls": ["https://x/1.jpg"]}]
        client = mock.Mock()
        client.attachment_preview.return_value = "https://preview/1.png"
        files, problems, _ = self._run(posts, {"https://x/1.jpg": [_resp(403, ctype="text/html")] * 3,
                                               "https://preview/1.png": [_resp(200, _png())]}, client)
        self.assertEqual(files, ["p1_0.jpg"])
        client.attachment_preview.assert_called_once_with("p1")

    def test_download_failure_message(self):
        posts = [{"id": "p1", "subject": "作品", "image_urls": ["https://x/1.jpg"]}]
        files, problems, _ = self._run(posts, {"https://x/1.jpg": [_resp(403, ctype="text/html")] * 3})
        self.assertEqual(files, [])
        self.assertIn("下載失敗（403）", problems[0])


class AuthTest(unittest.TestCase):
    """方案 C：開放校內網路時的登入保護。"""

    def setUp(self):
        import auth
        import server
        self.auth, self.server = auth, server
        self.tmp = Path(tempfile.mkdtemp())
        env = {"GRADER_PASSWORD_HASH": auth.hash_password("abc12345"), "GRADER_SECRET": "x" * 64}
        self.p_env = mock.patch.dict(os.environ, env)
        self.p_env.start()
        self.p_file = mock.patch.object(auth, "ENV", self.tmp / ".env")
        self.p_file.start()
        auth._fails.clear()
        server.app.secret_key = "x" * 64
        self.c = server.app.test_client()

    def tearDown(self):
        self.p_env.stop()
        self.p_file.stop()
        shutil.rmtree(self.tmp)

    def test_hash_roundtrip_and_no_plaintext(self):
        h = self.auth.hash_password("123456")
        self.assertNotIn("123456", h)
        self.assertTrue(self.auth.verify("123456", h))
        self.assertFalse(self.auth.verify("1234567", h))

    def test_pages_and_api_need_login(self):
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.get("/api/boards").status_code, 401)
        self.assertEqual(self.c.post("/api/board/x/publish", json={"confirm": "發布"}).status_code, 401)
        self.assertEqual(self.c.post("/login", data={"pw": "wrong"}).status_code, 200)
        self.assertEqual(self.c.get("/api/boards").status_code, 401)
        self.assertEqual(self.c.post("/login", data={"pw": "abc12345"}).status_code, 302)
        self.assertEqual(self.c.get("/api/boards").status_code, 200)
        self.c.get("/logout")
        self.assertEqual(self.c.get("/api/boards").status_code, 401)

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.c.post("/login", data={"pw": "wrong"})
        r = self.c.post("/login", data={"pw": "abc12345"})
        self.assertIn("分鐘後再試", r.get_data(as_text=True), "鎖住期間連正確密碼也不能登入")
        self.assertEqual(self.c.get("/api/boards").status_code, 401)

    def test_set_env_keeps_other_keys(self):
        f = self.tmp / ".env"
        f.write_text("PADLET_API_KEY=keep-me\nGRADER_PASSWORD_HASH=old\n", encoding="utf-8")
        self.auth.set_env({"GRADER_PASSWORD_HASH": "new", "GRADER_HOST": "0.0.0.0"}, f)
        self.assertEqual(f.read_text(encoding="utf-8"),
                         "PADLET_API_KEY=keep-me\nGRADER_PASSWORD_HASH=new\nGRADER_HOST=0.0.0.0\n")

    def test_refuse_lan_without_password(self):
        with mock.patch.dict(os.environ, {"GRADER_HOST": "0.0.0.0", "GRADER_PASSWORD_HASH": ""}), \
                mock.patch.object(self.server.app, "run") as run:
            with self.assertRaises(SystemExit):
                self.server.serve(open_browser=False)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
