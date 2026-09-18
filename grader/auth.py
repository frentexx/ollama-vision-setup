"""審核頁的登入密碼（方案 C：開放校內網路時必須設定）。

- 密碼只存雜湊（PBKDF2-SHA256 加鹽），寫在 .env 的 GRADER_PASSWORD_HASH；明碼不落地
- 設定方式：.venv\\Scripts\\python.exe -X utf8 grade.py set-password（在自己的終端機輸入，畫面不顯示）
- 同一個 IP 連續錯 5 次，鎖 10 分鐘，避免被猜密碼
"""
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from store import ROOT

ENV = ROOT / ".env"
ITERATIONS = 200_000
MAX_FAILS, LOCK_SECONDS = 5, 600
_fails = {}   # ip → [失敗次數, 鎖到何時]


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${dk.hex()}"


def verify(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), h)
    except (ValueError, TypeError):
        return False


def set_env(values: dict, path: Path = ENV):
    """更新 .env 裡的幾個鍵，其他行（包含 PADLET_API_KEY）原樣保留。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    todo = dict(values)
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in todo:
            lines[i] = f"{k}={todo.pop(k)}"
    lines += [f"{k}={v}" for k, v in todo.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def password_hash() -> str:
    return os.environ.get("GRADER_PASSWORD_HASH", "")


def secret_key() -> str:
    """簽 session cookie 用；第一次需要時自動產生並存進 .env，重開伺服器後登入狀態仍有效。"""
    k = os.environ.get("GRADER_SECRET", "")
    if not k:
        k = secrets.token_hex(32)
        set_env({"GRADER_SECRET": k})
    return k


def locked(ip: str) -> int:
    """回傳還要鎖幾秒（0 表示沒鎖）。"""
    n, until = _fails.get(ip, (0, 0))
    return max(0, int(until - time.time()))


def attempt(ip: str, pw: str) -> bool:
    if locked(ip):
        return False
    if verify(pw, password_hash()):
        _fails.pop(ip, None)
        return True
    n = _fails.get(ip, (0, 0))[0] + 1
    _fails[ip] = (0, time.time() + LOCK_SECONDS) if n >= MAX_FAILS else (n, 0)
    return False
