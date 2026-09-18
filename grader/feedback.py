"""產生評語：先看圖逐項判斷（第一段），再依判斷結果寫給學生的留言（第二段）。

- 全部 AI 檢查點都達到最高等級 → 「鼓勵」模式：具體肯定＋延伸挑戰的提問
- 有任何一項沒達到 → 「修正建議」模式：先肯定做到的，再用蘇格拉底式提問引導學生自己發現要改哪裡
分兩段的好處：老師在審核頁改了等級或模式之後，可以只重寫留言（第二段），不必重新看圖。
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vision  # noqa: E402

from rubric import UNKNOWN, Rubric  # noqa: E402

MAX_SIDE = 1536   # 美術作品保留筆觸細節；驗收時 3 張約 10 秒
NOT_GRADED = "未評"   # 老師負責、但還沒選等級的項目

JUDGE_SYSTEM = (
    "你是高中美術老師的評分助教。一律使用台灣繁體中文，不可使用簡體字。"
    "只描述照片中實際看得到的東西；看不清楚、被遮住、或拍攝角度讓你無法確定時，等級一律選「無法判斷」，不要猜。"
    "每一項的 evidence 要說出在畫面的哪個位置看到什麼（例如「左下角的蘋果是橘紅色」），限 40 字。"
    "照片的燈光偏黃或偏藍時，判斷顏色要保守。不評論創意好壞，也不評論沒有列出的項目。"
)

COMPOSE_SYSTEM = (
    "你是高中美術老師，正在寫一則給學生看的作品留言。一律使用台灣繁體中文，不可使用簡體字。"
    "直接對學生說話，用「你」稱呼。只根據提供的觀察紀錄寫，不可以加入紀錄裡沒有的畫面內容。"
    "不提分數、等級、檢查點名稱，也不提到 AI。"
)

SOCRATIC_RULES = (
    "【修正建議模式：蘇格拉底式引導】\n"
    "- praise：一句話具體肯定學生已經做到的地方，要說出畫面位置。"
    "只能肯定「已達成」的項目或觀察紀錄裡的優點，不可以肯定「需要引導的項目」。\n"
    "- 每個 q 欄位針對指定的一個項目，寫一個讓學生自己觀察、比較、發現的問題：\n"
    "  · 從學生畫面上的具體位置出發（例如「看看你畫面的右半邊…」「數數看桌上有幾樣東西…」）\n"
    "  · 只能是問句，不可以說出答案或做法（不要寫「你應該」「可以改成」「試試看把…」「下次讓…」）\n"
    "  · 可以用比較、想像、假設的問法（「如果…你覺得會…？」「哪一個地方最先吸引你？為什麼？」）\n"
    "- encouragement：一句鼓勵，只表達肯定與期待，不可以包含任何建議、做法或指令。"
)

PRAISE_RULES = (
    "【鼓勵模式】\n"
    "- praise：具體肯定 1～2 個做得好的地方，要說出畫面位置與為什麼好。\n"
    "- q1：一個延伸挑戰的問題，引導學生思考下一步還能怎麼探索（不是指出錯誤），只能是問句。\n"
    "- encouragement：一句鼓勵。"
)


# ---------------------------------------------------------------- 第一段：看圖判斷
def judge_schema(rb: Rubric) -> dict:
    lv = rb.levels + [UNKNOWN]
    crit = {c.key: {"type": "object",
                    # evidence 放在 level 前面：模型照欄位順序生成，先寫觀察再下判斷，等級才會跟觀察一致
                    "properties": {"evidence": {"type": "string"}, "level": {"type": "string", "enum": lv}},
                    "required": ["evidence", "level"]} for c in rb.ai_criteria}
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            # 放在 description 後面：先看清楚拍到什麼，再判斷是不是作品
            "is_artwork": {"type": "boolean"},
            "criteria": {"type": "object", "properties": crit, "required": list(crit)},
            "strengths": {"type": "array", "items": {
                "type": "object", "properties": {"where": {"type": "string"}, "what": {"type": "string"}},
                "required": ["where", "what"]}},
            "photo_issue": {"type": "string"},
        },
        "required": ["description", "is_artwork", "criteria", "strengths", "photo_issue"],
    }


def judge_prompt(rb: Rubric, n_images: int, labels=None) -> str:
    lines = [f"作業：{rb.title}" + (f"（{rb.task}）" if rb.task else ""),
             f"以下是同一位學生的 {n_images} 張作品照片。" if n_images > 1 else "以下是一位學生的作品照片。"]
    if labels and any(labels):
        # 作業版分欄時（例如 ① 作品全貌、② 局部特寫、③ 創作過程），告訴模型每張的用途
        lines += [f"第 {i} 張：{x or '未分類'}" for i, x in enumerate(labels, 1)]
        lines.append("檢查點以「作品全貌」那張為主；特寫用來看細節，過程照不要當成完成作品評。")
    lines += [f"請依照檢查點逐項判斷：先寫 evidence（看到什麼），再根據 evidence 選等級，等級必須和 evidence 一致。"
              f"等級只能選：{'、'.join(rb.levels + [UNKNOWN])}。", "", "檢查點："]
    for c in rb.ai_criteria:
        lines.append(f"- {c.key}【{c.name}】{c.look_for}")
        for lv, note in c.level_notes.items():
            lines.append(f"    · {lv}：{note}")
    lines += ["",
              "另外請寫：",
              "- description：客觀描述照片拍到什麼、使用的媒材，80 字內",
              "- is_artwork：這些照片中，有沒有至少一張拍的是學生的作品本身（畫作、立體作品等）？"
              "如果全部都是教室、人物、網路圖片或與作業無關的東西，填 false；草稿或創作過程照不算無關",
              "- strengths：1～3 個畫面上具體的優點，where 寫位置，what 寫看到什麼、好在哪",
              "- photo_issue：照片本身的問題（太暗、斜拍、反光、模糊、沒拍到整張），沒有就填空字串"]
    return "\n".join(lines)


def judge(rb: Rubric, image_paths, labels=None) -> dict:
    b64s = [vision.file_to_b64(p, MAX_SIDE) for p in image_paths]
    r = vision.chat(b64s, judge_prompt(rb, len(b64s), labels), fmt=judge_schema(rb), num_predict=900,
                    system=JUDGE_SYSTEM, temperature=0.2)
    p = r["parsed"]
    if not isinstance(p, dict) or not isinstance(p.get("criteria"), dict):
        raise ValueError(f"模型回傳的不是合法 JSON：{r['content'][:200]}")
    crit = {}
    for c in rb.ai_criteria:
        x = p["criteria"].get(c.key) or {}
        lv = x.get("level") if x.get("level") in rb.levels + [UNKNOWN] else UNKNOWN
        crit[c.key] = {"level": lv, "evidence": fix_simplified(x.get("evidence", ""))[0]}
    return {
        "description": fix_simplified(p.get("description", ""))[0],
        "is_artwork": p.get("is_artwork") is not False,
        "criteria": crit,
        "strengths": [{"where": fix_simplified(s.get("where", ""))[0], "what": fix_simplified(s.get("what", ""))[0]}
                      for s in p.get("strengths", []) if isinstance(s, dict)],
        "photo_issue": fix_simplified(p.get("photo_issue", ""))[0],
        "seconds": round(r["wall_s"], 1),
    }


# ---------------------------------------------------------------- 第二段：寫留言
def pick_mode(rb: Rubric, levels: dict) -> str:
    """有判斷出等級的項目全都是最高等級 → 鼓勵；「無法判斷」和老師還沒選的項目不算。"""
    known = [levels[c.key] for c in rb.criteria if levels.get(c.key) in rb.levels]
    return "鼓勵" if all(lv == rb.top_level for lv in known) else "修正建議"


def weakest(rb: Rubric, levels: dict) -> list:
    """需要引導的項目：等級最低的排前面，最多 2 項（一次問太多學生會亂）。「無法判斷」不列入。"""
    ranked = [(rb.levels.index(levels[c.key]), c) for c in rb.criteria
              if levels.get(c.key) in rb.levels and levels[c.key] != rb.top_level]
    return [c for _, c in sorted(ranked, key=lambda t: -t[0])][:2]


def compose_schema(n_questions: int) -> dict:
    qs = [f"q{i}" for i in range(1, n_questions + 1)]
    props = {"praise": {"type": "string"}, **{q: {"type": "string"} for q in qs}, "encouragement": {"type": "string"}}
    return {"type": "object", "properties": props, "required": ["praise", *qs, "encouragement"]}


def compose(rb: Rubric, ai: dict, levels: dict, mode: str, teacher_hint: str = "") -> dict:
    ev = {k: v.get("evidence", "") for k, v in ai.get("criteria", {}).items()}
    obs = [f"畫面描述：{ai.get('description', '')}"]
    for s in ai.get("strengths", []):
        obs.append(f"優點：{s['where']}－{s['what']}")
    for c in rb.criteria:
        lv = levels.get(c.key, UNKNOWN)
        obs.append(f"【{c.name}】{c.look_for} → {lv}" + (f"；觀察：{ev[c.key]}" if ev.get(c.key) else ""))
    lines = [f"作業：{rb.title}" + (f"（{rb.task}）" if rb.task else ""), "", "觀察紀錄：", *obs, ""]
    targets = weakest(rb, levels) if mode == "修正建議" else []
    # 8B 模型常把「沒達成的狀況」當成優點來稱讚（例如主體放正中央卻說「很搶眼」），所以明列禁止稱讚的內容，寫完再檢查一次
    not_ok = [f"{c.name}：{ev.get(c.key) or c.look_for}" for c in rb.criteria
              if levels.get(c.key) in rb.levels and levels[c.key] != rb.top_level]
    if mode == "修正建議":
        done = [c.name for c in rb.criteria if levels.get(c.key) == rb.top_level]
        lines += [SOCRATIC_RULES, "", "已達成的項目：" + ("、".join(done) or "（無，請從觀察紀錄的優點找）"),
                  "praise 不可以稱讚或正面描述下列狀況（這些是還沒做到的地方）：", *[f"- {x}" for x in not_ok],
                  "需要引導的項目："]
        lines += [f"- q{i}：【{c.name}】{c.look_for}（目前：{levels[c.key]}；觀察：{ev.get(c.key) or '無'}）"
                  for i, c in enumerate(targets, 1)] or ["- q1：依觀察紀錄挑一個最值得再想想的地方"]
    else:
        lines += [PRAISE_RULES]
    if teacher_hint:
        lines += ["", f"老師特別交代：{teacher_hint}"]
    lines += ["", f"語氣：{rb.tone}", f"長度：{rb.length}"]
    if rb.avoid:
        lines.append("不要出現：" + "；".join(rb.avoid))
    if rb.examples:
        lines += ["", "老師過去寫的評語（模仿語氣與長度，不要照抄內容）："] + [f"- {e}" for e in rb.examples]
    nq = max(len(targets), 1)
    r = vision.chat([], "\n".join(lines), fmt=compose_schema(nq), num_predict=500,
                    system=COMPOSE_SYSTEM, temperature=0.5)
    secs, p = r["wall_s"], r["parsed"] or {}
    bad = praise_conflicts(p.get("praise", ""), not_ok) if not_ok else []
    for _ in range(2):   # 肯定句有問題：只重寫這一句，提問保留
        if not bad:
            break
        new, t = safe_praise(ai, not_ok, rb.tone)
        secs += t
        if new:
            p["praise"] = new
            bad = praise_conflicts(new, not_ok)
    if not_ok and praise_conflicts(p.get("encouragement", ""), not_ok):
        # 結尾的鼓勵也常順口說「你已經掌握構圖」，改成只表達期待、不稱讚具體項目的一句
        r = vision.chat([], "\n".join([
            f"原本的鼓勵：「{p.get('encouragement', '')}」",
            "這句稱讚了學生還沒做到的地方。請改寫成一句鼓勵（25 字內，用「你」稱呼）：",
            "只表達期待與支持，不要稱讚任何具體的畫面項目（構圖、色彩、位置、對比都不要提）。", f"語氣：{rb.tone}"]),
            fmt={"type": "object", "properties": {"encouragement": {"type": "string"}}, "required": ["encouragement"]},
            num_predict=100, temperature=0.4, system=COMPOSE_SYSTEM)
        secs += r["wall_s"]
        p["encouragement"] = (r["parsed"] or {}).get("encouragement") or p.get("encouragement", "")
    parts =[p.get("praise", ""), *[p.get(f"q{i}", "") for i in range(1, nq + 1)], p.get("encouragement", "")]
    text, fixed = fix_simplified("\n".join(x.strip() for x in parts if x and x.strip()))
    return {"text": text, "mode": mode, "had_simplified": fixed, "directive": directive_words(text),
            "praise_conflict": bad, "seconds": round(secs, 1)}


def safe_praise(ai: dict, not_ok: list, tone: str) -> tuple:
    """重寫肯定句：只准稱讚技巧與用心，避開所有還沒做到的項目。回傳 (句子, 秒數)。"""
    prompt = "\n".join([
        f"畫面描述：{ai.get('description', '')}",
        "這位學生還沒做到的地方（完全不要提，也不要換句話說成優點）：", *[f"- {x}" for x in not_ok], "",
        "請寫一句給學生的肯定句（30 字內，用「你」稱呼），只能從下面這些角度挑一個具體稱讚：",
        "塗色是否均勻、邊緣是否乾淨俐落、形狀是否完整、媒材運用、完成度、看得出的用心。",
        "不要用「醒目」「焦點」「搶眼」「突出」「吸引目光」「構圖」「位置」「對比」這些字。", f"語氣：{tone}"])
    r = vision.chat([], prompt, fmt={"type": "object", "properties": {"praise": {"type": "string"}},
                                     "required": ["praise"]},
                    num_predict=120, temperature=0.4, system=COMPOSE_SYSTEM)
    return (r["parsed"] or {}).get("praise", "").strip(), r["wall_s"]


def praise_conflicts(praise: str, not_ok: list) -> list:
    """檢查肯定句有沒有把「還沒做到的狀況」說成優點；回傳被誤稱讚的項目名稱。判斷比生成容易，小模型做得比較準。"""
    keys = [f"k{i}" for i in range(1, len(not_ok) + 1)]
    schema = {"type": "object", "properties": {"analysis": {"type": "string"},
                                               **{k: {"type": "boolean"} for k in keys}},
              "required": ["analysis", *keys]}
    prompt = "\n".join([f"給學生的肯定句：「{praise}」", "",
                        "下面是這位學生還沒做到的地方。請逐項判斷：這句肯定句有沒有稱讚到該項「沒做到的那個狀況本身」？",
                        *[f"{k}：{x}" for k, x in zip(keys, not_ok)], "",
                        "判斷標準：",
                        "- 算 true：肯定句把那個狀況說成好的。例如該項是「主體在正中央」，肯定句說「放在中央很醒目」。",
                        "- 算 false：只是提到同一個物體，或稱讚的是別的特質（顏色飽和、邊緣乾淨、塗色均勻、筆觸）。",
                        "  例如該項是「主體在正中央」，肯定句說「紅色圓形塗得很飽和」→ false。",
                        "analysis 先用一兩句說明理由，再把每個 k 填 true 或 false。"])
    r = vision.chat([], prompt, fmt=schema, num_predict=300, temperature=0.0,
                    system="你是嚴謹的審稿員，一律使用台灣繁體中文。只回答被問的問題。")
    p = r["parsed"] or {}
    return [x.split("：", 1)[0] for k, x in zip(keys, not_ok) if p.get(k) is True]


DIRECTIVE = re.compile(r"你應該|應該要|要把|可以改|改成|試試看把|下次(要|讓|可以)|建議你|不夠|太(暗|亮|小|大|少)了|錯了|不好")


def directive_words(text: str) -> list:
    """找出像是直接下指令或否定的字眼，提醒老師看一下（蘇格拉底式引導應該用提問）。"""
    return sorted({m.group(0) for m in DIRECTIVE.finditer(text)})


# ---------------------------------------------------------------- 全班摘要
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"highlights": {"type": "array", "items": {"type": "string"}},
                   "common_issues": {"type": "array", "items": {"type": "string"}},
                   "teaching_tips": {"type": "array", "items": {"type": "string"}}},
    "required": ["highlights", "common_issues", "teaching_tips"],
}


def class_summary(rb: Rubric, students: list) -> dict:
    """students：[{levels, ai}]。先統計各檢查點的等級分布，再請模型整理全班亮點與常見問題。"""
    stats = {}
    for c in rb.criteria:
        cnt = {lv: 0 for lv in rb.levels + [UNKNOWN, NOT_GRADED]}
        for s in students:
            cnt[s["levels"].get(c.key) or NOT_GRADED] += 1
        stats[c.name] = cnt
    notes = []
    for i, s in enumerate(students, 1):
        ai = s.get("ai") or {}
        st = "；".join(f"{x['where']}{x['what']}" for x in ai.get("strengths", []))
        ev = "；".join(f"{c.name}:{s['levels'].get(c.key, UNKNOWN)}({(ai.get('criteria', {}).get(c.key) or {}).get('evidence', '')})"
                      for c in rb.criteria)
        notes.append(f"{i}. 優點：{st}｜{ev}")
    prompt = "\n".join([
        f"作業：{rb.title}（{rb.task}）", "",
        "各檢查點等級人數：", json.dumps(stats, ensure_ascii=False), "",
        "每位學生的觀察紀錄（匿名）：", *notes, "",
        "請整理給老師看的全班回饋：highlights 全班共同亮點 2～3 項；common_issues 常見問題 2～3 項（說明大約幾位）；"
        "teaching_tips 下一堂課可以怎麼教 1～2 項。每項 50 字內，不要點名個別學生。"])
    r = vision.chat([], prompt, fmt=SUMMARY_SCHEMA, num_predict=800,
                    system="你是高中美術科的教學顧問。一律使用台灣繁體中文。只根據提供的資料整理，不要編造。",
                    temperature=0.3)
    p = r["parsed"] or {}
    fix = lambda xs: [fix_simplified(x)[0] for x in xs]  # noqa: E731
    return {"stats": stats, "highlights": fix(p.get("highlights", [])),
            "common_issues": fix(p.get("common_issues", [])), "teaching_tips": fix(p.get("teaching_tips", []))}


# ---------------------------------------------------------------- 簡體字與輸出格式
try:
    from opencc import OpenCC
    _s2t = OpenCC("s2tw")
except Exception:
    _s2t = None
S2T_FALSE_ALARM = set("才吃核")   # 台灣正體本來就這樣寫


def fix_simplified(text: str) -> tuple:
    """只轉換真的是簡體的字，避免整段丟進 s2t 把正確的繁體字改壞。回傳 (文字, 是否有改)。"""
    if not text or _s2t is None:
        return text, False
    out = [(_s2t.convert(ch) if "一" <= ch <= "鿿" and ch not in S2T_FALSE_ALARM else ch) for ch in text]
    new = "".join(out)
    return new, new != text


def to_html(text: str, teacher_note: str, disclose_ai: bool) -> str:
    paras = [p for p in text.split("\n") if p.strip()]
    if teacher_note.strip():
        paras.append(teacher_note.strip())
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paras)
    if disclose_ai:
        body += "<p><em>（本評語由 AI 協助草擬，經老師審閱）</em></p>"
    return body
