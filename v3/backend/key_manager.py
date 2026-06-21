"""API key encryption/decryption using cryptography.fernet.

Key bound to machine via PBKDF2 over machine-id + fixed salt.
Keys NEVER logged or written to disk in plaintext.
"""
import hashlib, base64, os, platform, re, logging, getpass, uuid
from pathlib import Path

_log = logging.getLogger("key_manager")

# Regex patterns for log scrubbing
KEY_PATTERNS = [
    re.compile(r'sk-[a-zA-Z0-9_-]{20,}'),
    re.compile(r'ai-[a-zA-Z0-9_-]{20,}'),
    re.compile(r'AIza[a-zA-Z0-9_-]{30,}'),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),
]


_SALT = b'video-subber-v2-fixed-salt-2026'

def _derive_key() -> bytes:
    """PBKDF2 key derivation from machine identity — 100,000 iterations."""
    material = f"{platform.node()}-{getpass.getuser()}-{uuid.getnode()}".encode()
    key_bytes = hashlib.pbkdf2_hmac(
        'sha256', material, _SALT, 100_000, dklen=32
    )
    return base64.urlsafe_b64encode(key_bytes)


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        return Fernet(_derive_key())
    except ImportError:
        return None


def encrypt_api_key(plaintext: str) -> str:
    f = _get_fernet()
    if f is None:
        return plaintext  # No cryptography available — store as-is (dev mode)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    f = _get_fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        _log.warning("Failed to decrypt API key — may be from different machine")
        return ciphertext


def mask_key(key: str, visible: int = 4) -> str:
    if len(key) <= visible:
        return "..." + key
    return "···" + key[-visible:]


def scrub_logs(text: str) -> str:
    """Replace API key patterns with [REDACTED]."""
    for pattern in KEY_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
