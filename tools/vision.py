"""共用模組：圖片前處理、呼叫 Ollama 視覺模型、評語 JSON 欄位定義。

verify.py（驗收）與 vision_smoke.py（手動試跑）都用這支，
下一階段的批改主程式也可以直接沿用。
"""
import base64
import io
import json
import os
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

try:  # iPhone 拍的 HEIC 照片；沒裝 pillow-heif 就只能讀 JPEG／PNG
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """讀 .env（沒有就算了），只補環境變數裡沒有的值。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
# ⚠ 不要寫成 qwen3-vl:8b：那個 tag 指向 thinking 版
MODEL = os.environ.get("VISION_MODEL", "qwen3-vl:8b-instruct")
NUM_CTX = int(os.environ.get("NUM_CTX", "16384"))

# 評語固定欄位（交給 Ollama 的 format，強制輸出合法 JSON）
FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "result": {"type": "string", "enum": ["符合", "部分符合", "不符合", "無法判斷"]},
                    "note": {"type": "string"},
                },
                "required": ["item", "result", "note"],
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "encouragement": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["description", "checklist", "strengths", "encouragement", "question"],
}

SYSTEM_PROMPT = (
    "你是高中美術老師的評語助教。一律使用台灣繁體中文，不可使用簡體字。"
    "只描述畫面中實際看得到的東西，不確定就寫「無法判斷」，不要猜測。"
    "指出優點時要說明在畫面的哪個位置（例如左上、中央、右下）。"
    "不評論透視、比例、明暗光源、線條筆觸，也不評分。"
    "description 限 80 字內，每個 note 限 30 字內，encouragement 限 50 字內，question 限 40 字內。"
)

# 驗收用的示範規準；正式上線換成美術老師的 rubric
DEMO_RUBRIC = ["畫面至少使用兩種顏色", "有明確的主體", "作品上寫有標題文字"]


def image_to_b64(img: Image.Image, max_side: int = 1024) -> str:
    """縮到長邊 max_side、轉 JPEG、再轉 base64（Ollama 的 images 只收 base64，不收網址）。"""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def file_to_b64(path, max_side: int = 1024) -> str:
    with Image.open(path) as img:
        return image_to_b64(img, max_side)


_caps_cache = {}


def capabilities(model: str = MODEL) -> list:
    """查模型能力（vision / thinking…），用來決定要不要送 think=false。"""
    if model not in _caps_cache:
        r = requests.post(f"{OLLAMA_URL}/api/show", json={"model": model}, timeout=30)
        r.raise_for_status()
        _caps_cache[model] = r.json().get("capabilities", [])
    return _caps_cache[model]


def build_prompt(rubric=DEMO_RUBRIC, n_images: int = 1) -> str:
    items = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(rubric))
    head = f"以下是同一位學生的 {n_images} 張作品照片。" if n_images > 1 else "以下是一位學生的作品照片。"
    return f"{head}\n請依照作業檢查點逐項判斷，並寫出評語草稿：\n{items}"


def chat(images_b64, prompt, fmt=FEEDBACK_SCHEMA, num_predict: int = 800, timeout: int = 300,
         system: str = SYSTEM_PROMPT, temperature: float = 0.3) -> dict:
    """送一次請求，回傳 {'content', 'parsed', 各項耗時(秒), tok/s}。"""
    body = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt, "images": list(images_b64)},
        ],
        "options": {"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": num_predict},
    }
    if fmt is not None:
        body["format"] = fmt
    # 只有支援 thinking 的模型才送 think=false；instruct 版不送，避免被拒
    if "thinking" in capabilities():
        body["think"] = False

    t0 = time.perf_counter()
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=timeout)
    wall = time.perf_counter() - t0
    r.raise_for_status()
    d = r.json()

    content = d.get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None

    ns = 1e9
    eval_s = d.get("eval_duration", 0) / ns
    pe_s = d.get("prompt_eval_duration", 0) / ns
    return {
        "content": content,
        "parsed": parsed,
        "wall_s": wall,
        "total_s": d.get("total_duration", 0) / ns,
        "load_s": d.get("load_duration", 0) / ns,
        "prompt_tokens": d.get("prompt_eval_count", 0),
        "prompt_tok_s": d.get("prompt_eval_count", 0) / pe_s if pe_s else 0.0,
        "eval_tokens": d.get("eval_count", 0),
        "eval_tok_s": d.get("eval_count", 0) / eval_s if eval_s else 0.0,
        "done_reason": d.get("done_reason", ""),
    }


def schema_ok(parsed) -> tuple:
    """檢查 JSON 是否有全部必要欄位且型別正確，回傳 (是否通過, 說明)。"""
    if not isinstance(parsed, dict):
        return False, "不是合法 JSON 物件"
    missing = [k for k in FEEDBACK_SCHEMA["required"] if k not in parsed]
    if missing:
        return False, f"缺欄位：{', '.join(missing)}"
    if not isinstance(parsed["checklist"], list) or not isinstance(parsed["strengths"], list):
        return False, "checklist／strengths 應為陣列"
    return True, "欄位完整"
