#!/usr/bin/env python3
"""Encrypt credentials for upload to video-subber-config public repo.

Usage:
  python scripts/encrypt_credentials.py

This script reads plaintext credentials from a local file (never committed)
and outputs encrypted JSON to paste into credentials.json in the public repo.

The decryption key is embedded in backend/credential_manager.py (never committed
to the public config repo — only compiled into the EXE).
"""
import base64, json, os, sys

# Same key as in backend/credential_manager.py
_KEY = b"V_wo1jL853wjdIxBba_j2o4KhAC8A_82JYr1rZUFzM4="

PLAINTEXT_FILE = os.path.join(os.path.dirname(__file__), "..", "credentials_plaintext.json")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "credentials_encrypted.json")


def encrypt():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        import subprocess
        print("cryptography 未安装，正在自动安装...")
        subprocess.run([sys.executable, "-m", "pip", "install", "cryptography"], check=True)
        from cryptography.fernet import Fernet

    if not os.path.exists(PLAINTEXT_FILE):
        print(f"Plaintext file not found: {PLAINTEXT_FILE}")
        print("Create it with:")
        print(json.dumps({
            "baidu": {"app_id": "你的百度APP_ID", "secret": "你的百度SECRET"},
            "platform_keys": [
                {"platform": "glm", "name": "GLM-4 Flash", "key": "你的GLM_API_Key"}
            ]
        }, indent=2, ensure_ascii=False))
        sys.exit(1)

    with open(PLAINTEXT_FILE, "r", encoding="utf-8") as f:
        plain = json.load(f)

    f = Fernet(_KEY)
    encrypted = {}

    baidu = plain.get("baidu", {})
    if baidu.get("app_id") and baidu.get("secret"):
        encrypted["baidu"] = {
            "app_id": f.encrypt(baidu["app_id"].encode()).decode(),
            "secret": f.encrypt(baidu["secret"].encode()).decode(),
        }

    pks = plain.get("platform_keys", [])
    if pks:
        encrypted["platform_keys"] = []
        for entry in pks:
            encrypted["platform_keys"].append({
                "platform": entry["platform"],
                "name": entry.get("name", entry["platform"]),
                "key": f.encrypt(entry["key"].encode()).decode(),
            })

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(encrypted, f, indent=2, ensure_ascii=False)

    print(f"Encrypted credentials written to: {OUTPUT_FILE}")
    print("Copy this file to the video-subber-config repo as credentials.json")


if __name__ == "__main__":
    encrypt()
