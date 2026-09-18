"""手動試跑：拿真實作品照片讓模型寫評語草稿，看品質與速度。

用法：
    .venv\\Scripts\\python.exe -X utf8 tools\\vision_smoke.py 作品1.jpg [作品2.jpg ...] [--max-side 1536]
多張圖會當成「同一位學生」一次送出（批改主程式的建議做法）。
"""
import argparse
import json

import vision


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+", help="作品照片路徑")
    ap.add_argument("--max-side", type=int, default=1536, help="縮圖長邊（美術作品建議 1536）")
    ap.add_argument("--rubric", nargs="*", default=vision.DEMO_RUBRIC, help="作業檢查點，可多個")
    args = ap.parse_args()

    b64s = [vision.file_to_b64(p, args.max_side) for p in args.images]
    r = vision.chat(b64s, vision.build_prompt(args.rubric, len(b64s)))

    print(json.dumps(r["parsed"], ensure_ascii=False, indent=2) if r["parsed"] else r["content"])
    print(f"\n耗時 {r['wall_s']:.1f} 秒｜載入 {r['load_s']:.1f} 秒｜"
          f"圖片與提示 {r['prompt_tokens']} tokens｜生成 {r['eval_tokens']} tokens（{r['eval_tok_s']:.1f} tok/s）")


if __name__ == "__main__":
    main()
