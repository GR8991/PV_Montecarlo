"""Password hashing for Streamlit secrets (standard library only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

PREFIX = "pbkdf2_sha256"
ITER = 260_000


def hash_password(password: str, iterations: int = ITER) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{PREFIX}${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    stored = str(stored)
    if stored.startswith(PREFIX + "$"):
        try:
            _, it, salt_b64, dk_b64 = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(it))
            return hmac.compare_digest(dk, base64.b64decode(dk_b64))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(password.encode(), stored.encode())


