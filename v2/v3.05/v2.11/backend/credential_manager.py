"""Credential manager — decrypt remote config fetched from public GitHub repo.

All sensitive credentials (Baidu, platform LLM keys) are stored encrypted
in the public repo video-subber-config.  The decryption key is embedded here
and compiled into the EXE.  Users never see any of this.
"""
import base64, json, logging
from pathlib import Path

_log = logging.getLogger("credential_manager")

# Fernet-compatible key — embedded at build time, never in the repo.
# Generate a new one with: python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
_CREDENTIAL_KEY = b"V_wo1jL853wjdIxBba_j2o4KhAC8A_82JYr1rZUFzM4="

# In-memory decrypted store (populated at startup)
_baidu_config: dict | None = None
_platform_keys: list[dict] = []


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        return Fernet(_CREDENTIAL_KEY)
    except ImportError:
        return None


def decrypt_credentials(encrypted_payload: dict) -> dict:
    """Decrypt a payload fetched from the remote config repo.
    Returns {baidu: {app_id, secret}, platform_keys: [{platform, key, name}]}
    """
    f = _get_fernet()
    if f is None:
        _log.warning("cryptography unavailable — cannot decrypt remote credentials")
        return {}

    result = {}
    try:
        baidu_enc = encrypted_payload.get("baidu", {})
        if baidu_enc:
            result["baidu"] = {
                "app_id": f.decrypt(baidu_enc["app_id"].encode()).decode(),
                "secret": f.decrypt(baidu_enc["secret"].encode()).decode(),
            }
    except Exception as e:
        _log.warning("Failed to decrypt Baidu credentials: %s", e)

    try:
        pk_enc = encrypted_payload.get("platform_keys", [])
        keys = []
        for entry in pk_enc:
            try:
                keys.append({
                    "platform": entry["platform"],
                    "name": entry.get("name", entry["platform"]),
                    "key": f.decrypt(entry["key"].encode()).decode(),
                })
            except Exception as e:
                _log.warning("Failed to decrypt platform key for %s: %s",
                           entry.get("platform", "?"), e)
        result["platform_keys"] = keys
    except Exception as e:
        _log.warning("Failed to decrypt platform keys: %s", e)

    return result


def load_credentials_from_remote(encrypted_payload: dict):
    """Called once at startup.  Populates the module-level stores."""
    global _baidu_config, _platform_keys
    decrypted = decrypt_credentials(encrypted_payload)
    _baidu_config = decrypted.get("baidu")
    _platform_keys = decrypted.get("platform_keys", [])
    if _baidu_config:
        _log.info("Baidu credentials loaded from remote")
    if _platform_keys:
        _log.info("%d platform key(s) loaded from remote", len(_platform_keys))


def get_baidu_config() -> dict | None:
    """Return {app_id, secret} or None if not loaded."""
    return _baidu_config


def get_platform_keys() -> list[dict]:
    """Return [{platform, name, key}] for SVIP users."""
    return list(_platform_keys)
