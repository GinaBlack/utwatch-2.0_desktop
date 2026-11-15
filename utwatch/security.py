"""Password hashing and login lockout (standard library only)."""
from __future__ import annotations
import hashlib
import hmac
import os
import time

# Check the current OWASP guidance for PBKDF2-HMAC-SHA256 before release; tests lower this.
ITERATIONS = int(os.environ.get("UTWATCH_PBKDF2_ITERATIONS", "600000"))
MAX_FAILED = 5
LOCK_SECONDS = 300
MIN_PASSWORD = 8


def new_salt() -> bytes:
    return os.urandom(16)


def hash_password(password: str, salt: bytes, iterations: int | None = None) -> str:
    n = iterations or ITERATIONS
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, n)
    return f"{n}${dk.hex()}"


def verify_password(password: str, salt: bytes, stored: str) -> bool:
    try:
        n_s, _hex = stored.split("$", 1)
        candidate = hash_password(password, salt, int(n_s))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(candidate, stored)


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ValueError("Mix upper and lower case letters and include a digit")


def lock_until(now: float | None = None) -> float:
    return (now or time.time()) + LOCK_SECONDS
