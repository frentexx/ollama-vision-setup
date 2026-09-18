"""地端視覺模型驗收：服務層 → GPU 層 → 功能層 → 效能層 → 壓力測試，最後寫出 Markdown 報告。

用法（由 scripts/50-verify.ps1 呼叫）：
    .venv\\Scripts\\python.exe -X utf8 tests\\verify.py [--skip-stress]
結束碼：有任何 FAIL 就回 1。
"""
import argparse
import datetime as dt
import json
import os
import platform
import random
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vision  # noqa: E402

ROOT = vision.ROOT
IMG_DIR = ROOT / "tests" / "images" / "generated"
REPORT_DIR = ROOT / "reports"

# 門檻（對應規格卡的驗收條件）
SINGLE_LIMIT_S = 15      # 效能層：單張 ≤15 秒
STRESS_LIMIT_S = 600     # 壓力測試：50 張 ≤10 分鐘
TEMP_LIMIT_C = 85        # 壓力測試：GPU ≤85°C
STRESS_MAX_SIDE = 1536   # 美術作品保留筆觸細節

results = []   # 每一項檢查：{layer, item, result, detail}
extra = {}     # 報告附錄資料


def record(layer, item, result, detail=""):
    results.append({"layer": layer, "item": item, "result": result, "detail": detail})
    mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}[result]
    print(f"  {mark} [{layer}] {item}：{detail}", flush=True)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(執行失敗：{e})"


# ---------------------------------------------------------------- 測試圖
def find_cjk_font(size):
    for name in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "kaiu.ttf"):
        p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return None


def make_images():
    """產生三張自製測試圖：OCR 圖、色塊構圖圖、模擬斜拍偏色圖。"""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    big, small = find_cjk_font(110), find_cjk_font(64)

    # 1. OCR：白紙上的中文標題
    ocr = Image.new("RGB", (1600, 1200), "white")
    d = ImageDraw.Draw(ocr)
    if big:
        d.text((180, 380), "屏北高中 美術作業", font=big, fill="black")
        d.text((180, 560), "第三週 色彩練習", font=small, fill=(60, 60, 60))
    d.rectangle((150, 330, 1450, 700), outline="black", width=6)

    # 2. 色塊構圖：左上紅圓、右下藍方、中央黃色三角
    color = Image.new("RGB", (1600, 1200), (245, 238, 220))
    d = ImageDraw.Draw(color)
    d.ellipse((120, 100, 620, 600), fill=(210, 30, 30))
    d.rectangle((1000, 650, 1480, 1100), fill=(30, 70, 200))
    d.polygon([(800, 380), (620, 800), (980, 800)], fill=(240, 200, 20))
    if small:
        d.text((560, 1080), "我的構圖", font=small, fill="black")

    # 3. 斜拍偏色：把色塊圖做透視變形、偏黃、壓暗
    # QUAD 的四個角順序：左上、左下、右下、右上（來源圖座標）
    photo = color.transform((1600, 1200), Image.QUAD,
                            (-80, -40, 0, 1200, 1600, 1260, 1700, 80), Image.BICUBIC,
                            fillcolor=(90, 80, 70))
    photo = Image.blend(photo, Image.new("RGB", photo.size, (255, 220, 120)), 0.18)
    photo = ImageEnhance.Brightness(photo).enhance(0.85)

    imgs = {"ocr": ocr, "color": color, "photo": photo}
    for k, im in imgs.items():
        im.save(IMG_DIR / f"{k}.jpg", quality=92)
    return imgs, big is not None


def jitter(img, rng):
    """壓力測試用：隨機裁切／旋轉／亮度，避免每張一模一樣被快取而測不準。"""
    w, h = img.size
    s = rng.uniform(0.85, 1.0)
    x, y = rng.randint(0, int(w * (1 - s))), rng.randint(0, int(h * (1 - s)))
    im = img.crop((x, y, x + int(w * s), y + int(h * s)))
    im = im.rotate(rng.uniform(-3, 3), expand=False, fillcolor=(128, 128, 128))
    return ImageEnhance.Brightness(im).enhance(rng.uniform(0.85, 1.15))


# ---------------------------------------------------------------- 繁體檢查
try:
    from opencc import OpenCC
    _s2t = OpenCC("s2t")
except Exception:  # 套件沒裝就退回只檢查 CJK 比例
    _s2t = None
S2T_FALSE_ALARM = set("才吃核")  # 台灣正體本來就這樣寫，s2t 會誤報


def simplified_chars(text):
    if _s2t is None:
        return None
    return sorted({ch for ch in text if "\u4e00" <= ch <= "\u9fff"
                   and ch not in S2T_FALSE_ALARM and _s2t.convert(ch) != ch})


def all_text(parsed):
    return json.dumps(parsed, ensure_ascii=False) if parsed is not None else ""


# ---------------------------------------------------------------- 各層
def layer_service():
    L = "服務層"
    try:
        v = requests.get(f"{vision.OLLAMA_URL}/api/version", timeout=10).json()["version"]
        record(L, "API 回應", "PASS", f"Ollama {v}")
        extra["ollama_version"] = v
    except Exception as e:
        record(L, "API 回應", "FAIL", f"連不到 {vision.OLLAMA_URL}：{e}")
        return False

    # 只能綁 127.0.0.1，不可以是 0.0.0.0 或 [::]
    # 中文版 Windows 的狀態欄可能不是 LISTENING，改用「遠端位址為 *:0」判斷監聽中
    listen = []
    for ln in run(["netstat", "-ano", "-p", "TCP"]).splitlines() + run(["netstat", "-ano", "-p", "TCPv6"]).splitlines():
        p = ln.split()
        if len(p) >= 4 and p[1].endswith(":11434") and p[2] in ("0.0.0.0:0", "[::]:0"):
            listen.append(p[1])
    if not listen:
        record(L, "只給本機使用", "WARN", "netstat 查不到 11434 的監聽紀錄")
    elif all(a.startswith("127.0.0.1:") or a.startswith("[::1]:") for a in listen):
        record(L, "只給本機使用", "PASS", f"監聽 {', '.join(sorted(set(listen)))}")
    else:
        record(L, "只給本機使用", "FAIL",
               f"監聽 {', '.join(sorted(set(listen)))}，其他電腦連得到。"
               "請關閉 Ollama 設定裡的 Expose Ollama to the network，並把 OLLAMA_HOST 設為 127.0.0.1:11434")

    tags = [m["name"] for m in requests.get(f"{vision.OLLAMA_URL}/api/tags", timeout=10).json()["models"]]
    if vision.MODEL in tags:
        record(L, "模型已下載", "PASS", vision.MODEL)
    else:
        record(L, "模型已下載", "FAIL", f"找不到 {vision.MODEL}，目前有：{', '.join(tags) or '無'}")
        return False
    caps = vision.capabilities()
    record(L, "模型能力", "PASS" if "vision" in caps else "FAIL", ", ".join(caps))

    if platform.system() == "Windows":
        startup = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        lnk = list(startup.glob("Ollama*.lnk"))
        record(L, "開機自動啟動", "PASS" if lnk else "WARN",
               lnk[0].name if lnk else "啟動資料夾沒有 Ollama 捷徑，請重開 Ollama 並確認設定")
    return True


def layer_gpu():
    L = "GPU 層"
    # 先送一個純文字請求把模型載入，順便量冷啟動時間
    r = vision.chat([], "請回答：好", fmt=None, num_predict=5)
    record(L, "模型載入", "INFO", f"載入 {r['load_s']:.1f} 秒（冷啟動才會有，常駐後接近 0）")

    ps = requests.get(f"{vision.OLLAMA_URL}/api/ps", timeout=10).json().get("models", [])
    m = next((x for x in ps if x["name"] == vision.MODEL), None)
    if not m:
        record(L, "100% 使用 GPU", "FAIL", "/api/ps 查不到已載入的模型")
    else:
        pct = 100 * m.get("size_vram", 0) / max(m.get("size", 1), 1)
        record(L, "100% 使用 GPU", "PASS" if pct >= 99.9 else "FAIL",
               f"{pct:.0f}% 在顯存（{m.get('size_vram', 0) / 2**30:.1f} / {m.get('size', 0) / 2**30:.1f} GiB）")
        # KEEP_ALIVE=-1 時，到期時間會落在很遠的未來
        exp = m.get("expires_at", "")
        far = exp[:4].isdigit() and int(exp[:4]) > dt.date.today().year + 1
        record(L, "模型常駐（KEEP_ALIVE）", "PASS" if far else "WARN",
               f"expires_at={exp}" + ("" if far else "；未常駐，請確認 OLLAMA_KEEP_ALIVE=-1 並重開 Ollama"))

    extra["ollama_ps"] = run(["ollama", "ps"]).strip()
    q = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"]).strip()
    extra["nvidia_smi"] = q
    parts = [p.strip() for p in q.split(",")]
    if len(parts) >= 5:
        record(L, "顯示卡", "INFO", f"{parts[0]}，驅動 {parts[1]}，顯存 {parts[3]}/{parts[2]} MiB，{parts[4]}°C")
    else:
        record(L, "顯示卡", "WARN", f"nvidia-smi 讀取失敗：{q[:120]}")


def layer_function(imgs, has_font):
    L = "功能層"
    checks = {
        "ocr": ("OCR 讀出中文標題", ["屏北", "美術"]),
        "color": ("辨識色塊顏色", ["紅", "藍"]),
        "photo": ("斜拍偏色仍能描述", ["紅", "藍"]),
    }
    extra["samples"] = {}
    for key, (label, words) in checks.items():
        if key == "ocr" and not has_font:
            record(L, label, "WARN", "找不到中文字型，略過 OCR 測試")
            continue
        r = vision.chat([vision.image_to_b64(imgs[key], 1024)], vision.build_prompt())
        extra["samples"][key] = r
        ok, why = vision.schema_ok(r["parsed"])
        record(L, f"{key} 合法 JSON", "PASS" if ok else "FAIL", why)
        text = all_text(r["parsed"]) or r["content"]

        simp = simplified_chars(text)
        if simp is None:
            record(L, f"{key} 繁體中文", "WARN", "未安裝 opencc，無法檢查簡體字")
        else:
            record(L, f"{key} 繁體中文", "PASS" if not simp else "FAIL",
                   "無簡體字" if not simp else f"出現簡體字：{''.join(simp)}")

        hit = [w for w in words if w in text]
        res = "PASS" if len(hit) == len(words) else ("WARN" if hit else "FAIL")
        record(L, label, res, f"命中 {hit or '無'}／應有 {words}")


def layer_perf(imgs):
    L = "效能層"
    b64 = vision.image_to_b64(imgs["color"], 1024)
    runs = [vision.chat([b64], vision.build_prompt()) for _ in range(3)]
    med = statistics.median(r["wall_s"] for r in runs)
    tok = statistics.median(r["eval_tok_s"] for r in runs)
    ptok = statistics.median(r["prompt_tok_s"] for r in runs)
    record(L, f"單張 ≤{SINGLE_LIMIT_S} 秒（3 次取中位數）", "PASS" if med <= SINGLE_LIMIT_S else "FAIL",
           f"{med:.1f} 秒；生成 {tok:.1f} tok/s；讀圖 {ptok:.0f} tok/s；輸出 {runs[0]['eval_tokens']} tokens")
    extra["perf_runs"] = runs

    three = [vision.image_to_b64(imgs[k], STRESS_MAX_SIDE) for k in ("ocr", "color", "photo")]
    r = vision.chat(three, vision.build_prompt(n_images=3))
    record(L, f"同一位學生 3 張（長邊 {STRESS_MAX_SIDE}px）", "INFO",
           f"{r['wall_s']:.1f} 秒；圖片與提示共 {r['prompt_tokens']} tokens")


def layer_stress(imgs):
    L = "壓力測試"
    rng = random.Random(20260918)
    base = [imgs["ocr"], imgs["color"], imgs["photo"]]
    # 20 位學生：前 10 位各 3 張、後 10 位各 2 張，共 50 張
    students = [3] * 10 + [2] * 10
    print(f"  … 開始模擬一節課：{len(students)} 位學生、{sum(students)} 張圖，依序處理", flush=True)

    temps, stop = [], threading.Event()

    def sample():
        while not stop.is_set():
            out = run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"])
            if out.strip().isdigit():
                temps.append(int(out.strip()))
            stop.wait(2)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    times, bad, t0 = [], 0, time.perf_counter()
    try:
        for i, n in enumerate(students, 1):
            b64s = [vision.image_to_b64(jitter(rng.choice(base), rng), STRESS_MAX_SIDE) for _ in range(n)]
            r = vision.chat(b64s, vision.build_prompt(n_images=n))
            times.append(r["wall_s"])
            ok, _ = vision.schema_ok(r["parsed"])
            bad += 0 if ok else 1
            print(f"    學生 {i:2d}/{len(students)}：{n} 張，{r['wall_s']:.1f} 秒{'' if ok else '（JSON 不合格）'}", flush=True)
    finally:
        stop.set()
        th.join(timeout=5)
    total = time.perf_counter() - t0

    record(L, f"{sum(students)} 張 ≤{STRESS_LIMIT_S // 60} 分鐘", "PASS" if total <= STRESS_LIMIT_S else "FAIL",
           f"共 {total / 60:.1f} 分鐘；每位學生平均 {statistics.mean(times):.1f} 秒，最慢 {max(times):.1f} 秒")
    record(L, "JSON 全部合格", "PASS" if bad == 0 else "FAIL", f"{len(students) - bad}/{len(students)}")
    if temps:
        record(L, f"GPU ≤{TEMP_LIMIT_C}°C", "PASS" if max(temps) <= TEMP_LIMIT_C else "FAIL",
               f"最高 {max(temps)}°C，平均 {statistics.mean(temps):.0f}°C（每 2 秒取樣 {len(temps)} 次）")
    else:
        record(L, f"GPU ≤{TEMP_LIMIT_C}°C", "WARN", "讀不到溫度")
    extra["stress"] = {"total_s": total, "per_student_s": times, "temps": temps}


# ---------------------------------------------------------------- 報告
def write_report(started):
    host = socket.gethostname()
    stamp = started.strftime("%Y%m%d-%H%M")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "raw").mkdir(exist_ok=True)
    fails = sum(r["result"] == "FAIL" for r in results)
    warns = sum(r["result"] == "WARN" for r in results)
    verdict = "❌ 未通過" if fails else ("⚠️ 通過（有警告）" if warns else "✅ 通過")

    lines = [
        f"# 驗收報告：{host}",
        "",
        f"- 時間：{started:%Y-%m-%d %H:%M} ～ {dt.datetime.now():%H:%M}",
        f"- 結論：**{verdict}**（FAIL {fails}、WARN {warns}）",
        f"- 模型：`{vision.MODEL}`｜Ollama {extra.get('ollama_version', '?')}｜num_ctx {vision.NUM_CTX}",
        f"- 系統：{platform.platform()}｜Python {platform.python_version()}",
        f"- 顯示卡（名稱, 驅動, 總顯存, 已用, 溫度）：{extra.get('nvidia_smi', '?')}",
        "",
        "| 層 | 項目 | 結果 | 說明 |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['layer']} | {r['item']} | {r['result']} | {r['detail'].replace('|', '／')} |")
    if extra.get("ollama_ps"):
        lines += ["", "## ollama ps", "", "```", extra["ollama_ps"], "```"]
    for k, r in extra.get("samples", {}).items():
        lines += ["", f"## 評語樣本：{k}", "", "```json",
                  json.dumps(r["parsed"], ensure_ascii=False, indent=2) if r["parsed"] else r["content"], "```"]
    lines += ["", "## 人工確認（驗收人填寫）", "",
              "- [ ] 從另一台電腦開 `http://<本機IP>:11434` 連不上",
              "- [ ] 評語樣本沒有描述畫面裡不存在的東西",
              "- [ ] 重開機後不必手動操作，Ollama 就會自己啟動",
              "", "驗收人：＿＿＿＿＿＿　日期：＿＿＿＿＿＿"]

    md = REPORT_DIR / f"acceptance-{host}-{stamp}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (REPORT_DIR / "raw" / f"acceptance-{host}-{stamp}.json").write_text(
        json.dumps({"results": results, "extra": extra}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return md, fails


def main():
    ap = argparse.ArgumentParser(description="地端視覺模型驗收")
    ap.add_argument("--skip-stress", action="store_true", help="略過 50 張壓力測試（約 10 分鐘）")
    args = ap.parse_args()
    started = dt.datetime.now()
    print(f"驗收開始：{vision.MODEL} @ {vision.OLLAMA_URL}\n")

    imgs, has_font = make_images()
    print(f"測試圖已產生：{IMG_DIR}\n")
    try:
        if layer_service():
            layer_gpu()
            layer_function(imgs, has_font)
            layer_perf(imgs)
            if args.skip_stress:
                record("壓力測試", "略過", "WARN", "使用了 --skip-stress")
            else:
                layer_stress(imgs)
    except requests.RequestException as e:
        record("執行", "呼叫 Ollama 失敗", "FAIL", str(e)[:200])

    md, fails = write_report(started)
    print(f"\n報告：{md}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
