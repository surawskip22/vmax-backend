import hashlib
import hmac
import secrets

from .config import get_settings


def normalize_email(value: str) -> str:
    return value.strip().lower()


def random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_secret(value: str) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_secret(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected_hash)
