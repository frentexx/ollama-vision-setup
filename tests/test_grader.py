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


EXAMPLE = ROOT / "rubrics" / "範例-攝影三作業.toml"


class RubricTest(unittest.TestCase):
    def test_example_per_section(self):
        """每個區段一份作業：共同檢查點＋該作業的檢查點；區段名稱前面有編號也對得上。"""
        rb = rubric.load(EXAMPLE)
        self.assertEqual([a.name for a in rb.assignments], ["微距攝影", "人物攝影", "風景攝影"])
        self.assertEqual([c.name for c in rb.criteria], ["對焦清晰", "曝光適當"])
        r = rb.for_section("② 人物攝影")
        self.assertEqual(r.assignment, "人物攝影")
        self.assertEqual([c.key for c in r.criteria], ["c1", "c2", "a2c1", "a2c2", "a2c3"])
        self.assertEqual([c.ai for c in r.criteria], [True, True, True, True, False])
        got, full, missing = r.score({"c1": "符合", "c2": "部分符合", "a2c1": "符合", "a2c2": "不符合"})
        self.assertEqual((got, full, missing), (9, 15, ["神情動作"]))
        other = rb.for_section("④ 靜物攝影")
        self.assertEqual((other.assignment, [c.name for c in other.criteria]), ("", ["對焦清晰", "曝光適當"]))
        self.assertIn("截圖", rb.work)

    def test_match_prefers_longest_name(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.toml"
            f.write_text('[[assignments]]\nname = "攝影"\n[[assignments.criteria]]\nname = "a"\nlook_for = "a"\n'
                         '[[assignments]]\nname = "人物攝影"\n[[assignments.criteria]]\nname = "b"\nlook_for = "b"\n',
                         encoding="utf-8")
            rb = rubric.load(f)
        self.assertEqual(rb.for_section("人物攝影").assignment, "人物攝影")
        self.assertEqual(rb.for_section("風景攝影").assignment, "攝影")
        self.assertEqual(rb.for_section("素描").criteria, [], "對不上也沒有共同檢查點：沒有可評的項目")

    def test_modes(self):
        rb = rubric.load(EXAMPLE).for_section("風景攝影")
        ok = {"c1": "符合", "c2": "符合", "a3c1": "符合", "a3c2": "符合", "a3c3": "符合"}
        self.assertEqual(feedback.pick_mode(rb, ok), "鼓勵")
        self.assertEqual(feedback.pick_mode(rb, {**ok, "a3c2": "無法判斷"}), "鼓勵")
        self.assertEqual(feedback.pick_mode(rb, {**ok, "a3c3": "部分符合"}), "修正建議")
        self.assertEqual([c.name for c in feedback.weakest(rb, {**ok, "c1": "部分符合", "a3c2": "不符合"})],
                         ["三分法構圖", "對焦清晰"])

    def test_judge_prompt_uses_work_definition(self):
        """人物攝影拍到人是對的：「是不是作品」要看 rubric 的 work，不能寫死「拍到人物就不是作品」。"""
        rb = rubric.load(EXAMPLE).for_section("人物攝影")
        prompt = feedback.judge_prompt(rb, 1)
        self.assertIn(rb.work, prompt)
        self.assertIn("人物主角", prompt)
        self.assertNotIn("神情動作", prompt, "ai = false 的項目不送給模型")
        self.assertNotIn("教室、人物", prompt)

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
                    "rubric_path": str(EXAMPLE), "students": {}}
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


def new_board_json(sections, title="802 攝影單元", posts=()):
    return {"data": {"id": "newboard1234567890", "type": "board",
                     "attributes": {"title": title, "webUrl": {
                         "live": "https://padlet.com/pad02_98/802-newboard1234567890",
                         "qrCode": "https://assets.padletcdn.com/padlets/newboard1234567890/qr_code.png"}}},
            "included": [*[{"id": f"sec_{i}", "type": "section", "attributes": {"title": t, "sortIndex": 1}}
                           for i, t in enumerate(sections)], *posts]}


class PortalTest(unittest.TestCase):
    """作業牆自動建立與後台（portal.py）：不連 Padlet、不呼叫模型。"""

    def setUp(self):
        import portal
        self.portal = portal
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "rubrics").mkdir()
        shutil.copy(EXAMPLE, self.tmp / "rubrics" / EXAMPLE.name)
        for p in (mock.patch.object(store, "DATA", self.tmp / "data"), mock.patch.object(portal, "RUBRICS", self.tmp / "rubrics"),
                  mock.patch.object(portal.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        portal._jobs.clear()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tpl = portal.templates()[0]
        self.form = {"template": self.tpl["id"], "class_name": " 802 ", "assignment": "攝影單元", "note": "截止日 10/3",
                     "sections": ["① 微距攝影", " ② 人物攝影 ", "", "③ 風景攝影"]}

    def _create(self, raw=None):
        fake = mock.Mock()
        fake.create_ai_board.return_value = "https://api.padlet.dev/v1/ai-recipe-boards/status/x"
        fake.ai_board_status.side_effect = [("running", {}), ("done", {"id": "newboard1234567890"})]
        fake.board.return_value = raw or new_board_json(["① 微距攝影", "② 人物攝影", "③ 風景攝影"])
        job = self.portal.Job("create")
        with mock.patch.object(padlet, "Client", return_value=fake):
            return self.portal.create_board(self.portal.prepare(self.form), job), fake

    def test_prepare_validates_and_builds_recipe(self):
        p = self.portal.prepare(self.form)
        self.assertEqual(p["title"], "802 攝影單元")
        self.assertEqual(p["sections"], ["① 微距攝影", "② 人物攝影", "③ 風景攝影"], "空白的區段略過、前後空白去掉")
        for sec in p["sections"]:
            self.assertIn(f"「{sec}」", p["instructions"])
        self.assertIn("恰好 3 個區段", p["instructions"])
        self.assertIn("截止日 10/3", p["instructions"])
        self.assertEqual(len(self.portal.prepare({**self.form, "sections": ["作品"]})["sections"]), 1, "老師可以自訂區段數量")
        for bad in ({"class_name": ""}, {"assignment": "x" * 41}, {"template": "nope"}, {"note": "字" * 501},
                    {"sections": []}, {"sections": [f"作業{i}" for i in range(9)]}, {"sections": ["作品", "作品 "]}):
            with self.assertRaises(ValueError):
                self.portal.prepare({**self.form, **bad})

    def test_create_saves_record_and_hides_url_until_todos_done(self):
        v, fake = self._create()
        self.assertEqual(fake.ai_board_status.call_count, 2)
        self.assertEqual(v["problems"], [])
        self.assertFalse(v["ready"])
        self.assertEqual((v["url"], v["qr"]), ("", ""), "待辦沒勾完不給學生網址與 QR")
        self.assertTrue(v["admin_url"], "老師還是要能開牆去改設定")
        with self.assertRaises(ValueError):
            self.portal.qr_png(v["id"])
        for k in self.portal.TODOS:
            b = self.portal.set_todo(v["id"], k, True)
        v = self.portal.view(b)
        self.assertTrue(v["ready"])
        self.assertTrue(v["url"].startswith("https://padlet.com/"))
        self.assertEqual(self.portal.get(v["id"])["rubric"], "rubrics/範例-攝影三作業.toml")
        self.assertEqual(self.portal.get(v["id"])["sections"], ["① 微距攝影", "② 人物攝影", "③ 風景攝影"])

    def test_verify_reports_mismatch(self):
        media = {"id": "p1", "type": "post", "attributes": {"author": None, "content": {"attachment": {"url": "https://x/1.jpg"}}}}
        raw = new_board_json(["作品", "過程"], title="美術作業", posts=[media])
        probs = self.portal.verify(raw, self.tpl["sections"], "802", "攝影單元")
        self.assertEqual(len(probs), 3)
        self.assertIn("區段是「作品」「過程」", probs[0])
        self.assertIn("標題變成", probs[1])
        self.assertIn("1 篇有圖片", probs[2])
        # 區段順序從 API 看不出來（sortIndex 會重複），只比對名稱；順序交給手動待辦
        secs = self.tpl["sections"]
        self.assertEqual(self.portal.verify(new_board_json(secs[::-1], title="802 攝影單元"), secs, "802", "攝影單元"), [])

    def test_rubric_save_checks_format_and_stays_in_folder(self):
        ok_text = EXAMPLE.read_text(encoding="utf-8")
        self.portal.save_rubric("..\\..\\802.toml", ok_text)
        self.assertTrue((self.tmp / "rubrics" / "802.toml").exists(), "路徑只取檔名，寫在 rubrics/ 裡")
        with self.assertRaises(ValueError):
            self.portal.save_rubric("802.toml", ok_text)   # 同名要確認覆蓋
        with self.assertRaises(ValueError) as e:
            self.portal.save_rubric("802.toml", "title = 沒加引號", overwrite=True)
        self.assertIn("802.toml", str(e.exception))
        self.assertEqual((self.tmp / "rubrics" / "802.toml").read_text(encoding="utf-8"), ok_text, "格式錯不能蓋掉原檔")
        self.assertEqual(sorted(p.name for p in (self.tmp / "rubrics").iterdir()), ["802.toml", EXAMPLE.name])
        for bad in (".toml", "a.txt", "a|b.toml"):
            with self.assertRaises(ValueError):
                self.portal.rubric_file(bad)

    def test_template_save_and_delete(self):
        ts = self.portal.save_template({"name": "兩份作業", "sections": ["作品", " ", "過程"], "description": "", "rubric": ""})
        self.assertEqual(ts[-1]["sections"], ["作品", "過程"])
        with self.assertRaises(ValueError):
            self.portal.save_template({"name": "重複", "sections": ["作品", "作品"]})
        self.portal.delete_template(ts[-1]["id"])
        with self.assertRaises(ValueError):
            self.portal.delete_template(self.tpl["id"])   # 至少留一個

    def test_one_grade_job_at_a_time(self):
        gate = __import__("threading").Event()
        self.portal.start("grade", lambda job: gate.wait(5), board="b1")
        with self.assertRaises(ValueError):
            self.portal.start("grade", lambda job: None, board="b2")
        self.assertEqual(self.portal.busy(), {"b1"})
        gate.set()

    def test_review_edits_blocked_while_board_busy(self):
        import server
        server.app.secret_key = "x" * 64
        gate = __import__("threading").Event()
        self.portal.start("grade", lambda job: gate.wait(5), board="b1")
        with mock.patch.dict(os.environ, {"GRADER_PASSWORD_HASH": ""}):
            r = server.app.test_client().post("/api/board/b1/update", json={"sid": "s0"})
        gate.set()
        self.assertEqual(r.status_code, 409)

    def test_api_key_only_sent_to_padlet(self):
        cli = padlet.Client("k")
        with mock.patch.object(padlet.requests, "request") as req:
            with self.assertRaises(padlet.PadletError):
                cli.ai_board_status("https://evil.example.com/v1/status/x")
            req.assert_not_called()


class SectionFetchTest(unittest.TestCase):
    """一個區段一份作業：同一位學生在不同區段的貼文分開評，評語貼在各自區段的那一篇。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(store, "DATA", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp)

    def _fetch(self, unit):
        raw = board_json([
            post("p1", "s01", "王小明", "12 王小明", "https://x/1.jpg", section="sec_1"),
            post("p2", "s01", "王小明", "12 王小明", "https://x/2.jpg", section="sec_2"),
            post("p3", "s02", "陳小華", "13 陳小華", "https://x/3.jpg", section="sec_2"),
        ])
        raw["included"][0]["attributes"].update(title="① 微距攝影", sortIndex=1)
        raw["included"].insert(1, {"id": "sec_2", "type": "section", "attributes": {"title": "② 人物攝影", "sortIndex": 2}})
        fake = mock.Mock()
        fake.me.return_value = ME
        fake.board.return_value = raw
        dl = lambda posts, dest, n, client=None: ([f"{p['id']}_0.jpg" for p in posts], [])  # noqa: E731
        with mock.patch.object(padlet, "Client", return_value=fake), mock.patch.object(padlet, "download_images", dl):
            return workflow.fetch("https://padlet.com/x/abcd1234efgh5678", str(EXAMPLE), log=lambda *_: None, unit=unit)

    def test_split_by_section(self):
        st = self._fetch("auto")   # 範例 rubric 有 [[assignments]] → 自動用 section
        self.assertEqual(st.state["unit"], "section")
        got = {(s["name"], s["section"], s["assignment"], s["target_post"]) for s in st.students.values()}
        self.assertEqual(got, {("王小明", "① 微距攝影", "微距攝影", "p1"), ("王小明", "② 人物攝影", "人物攝影", "p2"),
                               ("陳小華", "② 人物攝影", "人物攝影", "p3")})
        s = next(s for s in st.students.values() if s["target_post"] == "p2")
        keys = [c.key for c in workflow.rubric_for(workflow.load_rubric(st), s).criteria]
        self.assertEqual(keys, ["c1", "c2", "a2c1", "a2c2", "a2c3"], "人物攝影用共同＋人物的檢查點")

    def test_student_unit_merges_sections(self):
        st = self._fetch("student")
        self.assertEqual(len(st.students), 2)
        self.assertNotIn("section", next(iter(st.students.values())))

    def test_export_has_assignment_column(self):
        st = self._fetch("section")
        for s in st.students.values():
            s.update(ai={"criteria": {}}, levels={"c1": "符合"}, status="drafted", comment="很好")
        with open(workflow.export_csv(st), encoding="utf-8-sig") as f:
            head, *rows = list(__import__("csv").reader(f))
        self.assertEqual(head[:5], ["學生", "作業", "狀態", "對焦清晰", "曝光適當"])
        self.assertIn("微距攝影｜近距離主角", head)
        self.assertIn("人物攝影｜神情動作", head)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
