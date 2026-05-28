# Security Audit Report — Video Subber V2.11

**Date**: 2026-05-28
**Auditor**: Chief Security Officer (automated)
**Scope**: All backend Python files + `frontend/index.html` + `desktop.py`
**Methodology**: Static code analysis. No dynamic testing (penetration test) was performed.

---

## Risk Classification

| Level | Meaning |
|-------|---------|
| 🔴 Critical | Exploitable, causes data leak, financial loss, or system compromise. Must fix before release. |
| 🟡 Medium | Defense-in-depth gap, configuration weakness, or best-practice violation. Fix in next iteration. |
| 🟢 Low / Informational | Acceptable for a local desktop app. Documented for awareness. |

---

## V2.10 Audit Follow-up

The V2.10 audit identified two critical and five important findings. Status of remediation in V2.11:

| V2.10 ID | Description | Status in V2.11 |
|----------|------------|-----------------|
| S1 | `os.getuid()` crashes on Windows | ✅ Fixed — now uses `getpass.getuser() + uuid.getnode()` |
| S2 | Hardcoded activation codes | ❌ **Not fixed** — `ANDY`/`MENG` still in source |
| S3 | localStorage plaintext API keys | ✅ Fixed — frontend `saveConfig()` now strips keys from localStorage |
| S4 | Weak machine fingerprint | ❌ Not fixed — still only MAC + `os.name` |
| S5 | No certificate pinning for remote config | 🟡 Accepted risk — `urlopen` verifies TLS by default |
| S6 | Worker input validation | 🟡 Accepted risk — list-form args, low threat |
| S7 | No integrity check on activation.json | 🟡 Accepted risk — low ROI for local app |

---

## Findings Summary (New in V2.11)

| # | Severity | Area | Title |
|---|----------|------|-------|
| F1 | 🔴 | API Key Storage | Plaintext key write: `PUT /api/config` bypasses Fernet encryption entirely |
| F2 | 🔴 | API Key Storage | Hardcoded Baidu Translate credentials in source code |
| F3 | 🔴 | API Key Storage | Gemini API key in URL query string |
| F4 | 🟡 | API Key Storage | Fixed PBKDF2 salt reduces encryption strength |
| F5 | 🟡 | API Key Storage | `encrypt_api_key` fallback to plaintext when `cryptography` unavailable |
| F6 | 🟡 | Activation | Hardcoded developer codes in source + no brute-force protection |
| F7 | 🟡 | Activation | `_effective_tier()` in dev mode trusts client-supplied tier without server-side verification |
| F8 | 🟡 | Activation | Machine fingerprint uses only two easily-spoofed values |
| F9 | 🟡 | Log Sanitization | Baidu backend logs exception messages that may expose API parameters |
| F10 | 🟡 | Log Sanitization | `worker_manager` reads stderr from worker without log scrubbing |
| F11 | 🟡 | Path Traversal | `local_path` mode reads arbitrary filesystem paths supplied by client |
| F12 | 🟢 | Desktop App | pywebview developer tools potentially accessible |
| F13 | 🟢 | Web General | WebSocket no authentication |
| F14 | 🟢 | Web General | Job ID entropy (40 bits) is borderline for multi-tenant, acceptable for local |
| F15 | 🟢 | Frontend | `innerHTML` used with user-supplied filenames (local-only mitigates) |
| F16 | 🟢 | Dependency | `pyproject.toml` uses `>=` version pins without hash verification |
| F17 | 🟢 | API Design | `GET /api/config` decrypts then masks; broken when keys stored plaintext |

---

## Detailed Findings

---

### 🔴 F1 — Plaintext API key write: `PUT /api/config` bypasses Fernet encryption entirely

**Files**: `backend/main.py:526-531`, `backend/config_manager.py:35-38`, `frontend/index.html:736-741`

**Description**: The encryption infrastructure in `key_manager.py` (`encrypt_api_key`, `decrypt_api_key`, Fernet with PBKDF2) exists and is correctly implemented, but it is **never wired into the save path**.

The flow is:
1. User types an API key in the frontend (`frontend/index.html:676-700`). The plaintext key is stored in `State.settings.apiKeys`.
2. `saveConfig()` (`frontend/index.html:736-741`) sends the **plaintext key** to `PUT /api/config`:
   ```javascript
   const cfg = { ..., apiKeys: State.settings.apiKeys, ... };
   fetch('/api/config', {method:'PUT', body: JSON.stringify(cfg)});
   ```
3. The backend endpoint (`main.py:526-531`) writes the payload **directly** to disk without encrypting keys:
   ```python
   @app.put("/api/config")
   async def save_config(payload: dict):
       from .config_manager import save_config
       save_config(payload)  # ← no encryption step
   ```
4. `config_manager.save_config()` (`config_manager.py:35-38`) performs a raw `json.dump()`.

**Impact**: All user-provided API keys (Claude, GPT, Gemini, DeepSeek, Doubao, GLM, Custom) are stored in **plaintext** at `~/.video-subber/config.json`. Anyone with filesystem access to the user's home directory can read them.

**Root Cause**: The encryption layer was designed (`key_manager.py`) but the integration step into the save path was never implemented. The `encrypt_api_key()` function has zero callers in the production code path. Conversely, `worker.py` reads `config.get("apiKeys", [])` directly without calling `decrypt_api_key()` — so even if keys were encrypted at rest, the worker would pass ciphertext to the LLM APIs.

**Recommendation**: In `main.py` `save_config()`, iterate over `payload.get("apiKeys", [])` and call `encrypt_api_key()` on each key value before calling `config_manager.save_config()`. Distinguish between already-encrypted values (Fernet tokens start with `gAAAAA`) and new plaintext values to avoid double-encryption. In `worker.py:main()`, call `decrypt_api_key()` on each key when loading the config for translation.

---

### 🔴 F2 — Hardcoded Baidu Translate credentials in source code

**File**: `backend/translation/baidu.py:5-6`

```python
BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID", "20260520002616499")
BAIDU_SECRET = os.environ.get("BAIDU_SECRET", "NGw5pcrCkfDqe6gpygvT")
```

**Description**: Baidu Translate API credentials (`APP_ID` and `SECRET`) are hardcoded as Python default values. These are live production credentials for the Baidu Translate API (a paid service with quota limits). Anyone who can read the source code (open-source repo, decompiled EXE, or even a `strings` command on the .pyc) can extract these credentials and use them to make API calls on the developer's Baidu account.

**Impact**:
- Financial: attacker can exhaust the developer's Baidu Translate quota, incurring charges
- Denial of Service: quota exhaustion breaks Baidu translation for all legitimate users
- The `SECRET` value is a credential that should never appear in source code

**Recommendation**:
1. **Immediately**: Rotate the Baidu credentials on the Baidu console (this report will be stored on disk; treat the current `APP_ID`/`SECRET` as compromised).
2. Remove the hardcoded default values. Change to:
   ```python
   BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID", "")
   BAIDU_SECRET = os.environ.get("BAIDU_SECRET", "")
   ```
3. If these are meant to be "platform-provided" free credentials for the `free` tier, serve them through the same remote config mechanism as `platform_keys.json` rather than baking them into source.

---

### 🔴 F3 — Gemini API key passed in URL query string

**File**: `backend/translation/gemini.py:29-36`

```python
def _call_api(self, prompt: str, max_tokens: int = 8192) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{self._model}:generateContent?key={self._key}"
    )
```

**Description**: The Gemini backend passes the API key as a URL query parameter (`?key=...`). This is a well-known anti-pattern because:
1. URLs appear in server logs, proxy logs, and error messages
2. If any exception occurs during the request, the full URL (including the key) may end up in a Python traceback that gets logged to `debug.log`
3. The `_call_api` method in `base.py` uses `urllib.request.urlopen()`, and if an `HTTPError` is raised, its string representation includes the full URL
4. Even though there's no proxy between the app and Google's API, the key in the URL means it's transmitted in the HTTP request line (not just headers), making it visible in any intermediate network logging

**Impact**: API key leakage through log files (`~/.video-subber/logs/<job_id>/debug.log`) if an `HTTPError` traceback is logged.

**Recommendation**: Use the HTTP header method instead. Google's Gemini API supports `x-goog-api-key` header. Override `_build_headers()` and `_call_api()` to pass the key via header:
```python
def _build_headers(self) -> dict:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": self._key,
    }

def _call_api(self, prompt: str, max_tokens: int = 8192) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{self._model}:generateContent"
    )
    body = self._build_body(prompt, max_tokens)
    req = urllib.request.Request(url, data=body, headers=self._build_headers())
    ...
```

---

### 🟡 F4 — Fixed PBKDF2 salt reduces encryption strength

**File**: `backend/key_manager.py:20`

```python
_SALT = b'video-subber-v2-fixed-salt-2026'
```

**Description**: The PBKDF2 salt is hardcoded in source code. A salt should be random and unique per installation to prevent pre-computation attacks (rainbow tables). With a fixed salt, an attacker who knows the salt (anyone with access to source code) can pre-compute key derivation tables targeting this specific application. The encryption key derivation becomes dependent solely on the machine identity material, which has limited entropy (hostname + username + MAC).

**Impact**: Reduced entropy of the derived encryption key. Since the source is open/decompilable, the salt is effectively public. An attacker targeting multiple installations could pre-compute one rainbow table for all users.

**Recommendation**: Generate a random salt on first run and store it in `~/.video-subber/.salt`. If the salt file doesn't exist, generate one with `os.urandom(16)` and persist it. This ensures each installation has a unique encryption key derivation even if machine identity components are similar across installations.

---

### 🟡 F5 — `encrypt_api_key` silently falls back to plaintext

**File**: `backend/key_manager.py:39-42`

```python
def encrypt_api_key(plaintext: str) -> str:
    f = _get_fernet()
    if f is None:
        return plaintext  # No cryptography available — store as-is (dev mode)
```

**Description**: If the `cryptography` package fails to import (e.g., missing dependency, broken virtual environment), `encrypt_api_key()` returns the plaintext key unchanged. The caller receives no indication that encryption was skipped. There is no WARNING log message, no exception, and no return value that distinguishes "encrypted" from "plaintext (failed)."

**Impact**: Keys stored in plaintext without the developer or user being aware. While `cryptography` is listed in `pyproject.toml` dependencies, installation failures or environment issues could silently degrade security.

**Recommendation**: Either (a) raise a `RuntimeError` if encryption is unavailable at startup, refusing to run without it, or (b) at minimum, log a prominent warning at WARNING level so the user/developer is aware that encryption is degraded.

---

### 🟡 F6 — Hardcoded developer codes + no brute-force protection

**File**: `backend/main.py:151`

```python
_BUILTIN_CODES: set[str] = {"ANDY", "MENG"}
```

**Description**: Developer activation codes are hardcoded as short, predictable strings in source code. Anyone who reads the source can activate developer mode. Additionally, the `/api/dev/activate` endpoint has no rate limiting or brute-force protection — an attacker can attempt unlimited activation codes without delay.

**Impact**: Developer mode unlocks all tiers including `paid_3` (platform-provided API keys). An attacker with source code access can activate dev mode and use platform-provided LLM keys. While the app is local-only, if the remote config system is enabled and platform keys are configured, this could lead to unauthorized consumption of paid LLM quotas.

**Recommendation**:
1. Remove hardcoded codes from source; use environment variable only (`VSUB_DEV_CODES`).
2. Add rate limiting to `/api/dev/activate` (e.g., 5 attempts per minute per source).
3. Consider using longer, randomly-generated activation codes (e.g., `python -c "import secrets; print(secrets.token_urlsafe(16))"`).

---

### 🟡 F7 — `_effective_tier()` in dev mode trusts client-supplied tier without verification

**File**: `backend/main.py:451-464`

```python
def _effective_tier(requested: str) -> str:
    if _dev_mode_active:
        tier = requested if requested in TIER_FEATURES else "free"
        return tier  # ← trusts client directly, no audit log
    return "free"
```

**Description**: When dev mode is active, the client-supplied `tier` parameter is accepted without any server-side verification against the activation code's allowed tier. The activation mechanism grants access to dev mode, but dev mode grants access to ALL tiers unconditionally. There is no audit trail or logging when `paid_3` tier (platform keys) is used.

**Impact**: Any activated developer can use `paid_3` tier (platform-provided LLM keys). While this is partially by design (devs need to test all tiers), there is no audit trail, tier differentiation between activation codes, or usage tracking.

**Recommendation**: If tier differentiation is desired, extend the activation code system to include tier metadata. For example, store `{"code": "ANDY", "tier": "paid_3", "fingerprint": "..."}` in `activation.json` and gate `_effective_tier()` based on the stored tier. At minimum, add an audit log entry when dev mode is used with `paid_3` tier to track platform key consumption.

---

### 🟡 F8 — Machine fingerprint uses only two easily-spoofed values

**File**: `backend/main.py:277-283`

```python
def _machine_fingerprint() -> str:
    parts = [str(_uuid.getnode()), _os.name]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
```

**Description**: The activation binding fingerprint consists of only two components: MAC address (`uuid.getnode()`) and OS name (`os.name`). On some systems, `uuid.getnode()` returns a fallback random value rather than a real MAC. The fingerprint hash is truncated to 16 hex characters (64 bits), which discards entropy from the SHA256 output.

**Impact**: An attacker who knows the target's MAC address and OS could forge the activation file on another machine. The MAC address is visible on local networks (ARP tables). On virtual machines and containers, MAC addresses are often predictable.

**Recommendation**: Add more entropy sources: `platform.node()` (hostname), `getpass.getuser()` (username), `platform.platform()` (OS details). Use the full SHA256 hash (64 characters) instead of truncating to 16 — the storage cost is negligible.

---

### 🟡 F9 — Baidu backend log messages may expose API parameters

**File**: `backend/translation/baidu.py:63-64`

```python
except Exception as e:
    _log.warning("百度翻译失败: %s", e)
```

**Description**: If the Baidu API request raises an exception (e.g., `HTTPError`), the exception object is logged. `HTTPError` objects include the full URL in their string representation. While the Baidu API uses MD5 signing (not a direct key in the URL), the `appid`, `salt`, and query text are present. More broadly, the log scrubbing layer in `key_manager.py` (`scrub_logs`) uses regex patterns for common key formats (`sk-...`, `AIza...`), but would not catch a Baidu secret if it appeared in a log message.

**Impact**: Low, but the log scrubbing layer has demonstrated gaps. If a translation backend ever logs the raw API key in an error message, the scrub patterns may not catch it (e.g., Doubao or GLM keys don't have a recognizable prefix like `sk-`).

**Recommendation**: Expand `KEY_PATTERNS` in `key_manager.py` to include a generic "Bearer token in log" pattern. Additionally, override exception handling in each translation backend to sanitize the error message before logging — strip query parameters from URLs, and never include the raw API response body in log messages at INFO level or above.

---

### 🟡 F10 — `worker_manager` reads stderr from worker without log scrubbing

**File**: `backend/worker_manager.py:122-129`

```python
def _read_stderr(self, job_id: str, proc: subprocess.Popen):
    for line in proc.stderr:
        line_str = line.rstrip()
        _log.debug("[worker:%s stderr] %s", job_id, line_str)
```

**Description**: The worker subprocess's stderr output is logged at DEBUG level without passing through `scrub_logs()`. While the worker process uses `_ScrubFormatter` for its own logging (user.log and debug.log), third-party libraries (faster-whisper, ctranslate2, librosa, torch) writing directly to stderr/stderr would bypass the worker's formatter. If a library's error or warning message happened to include sensitive data, it would appear in the main server's log unsanitized.

**Impact**: Low probability but a defense-in-depth gap. The worker's stderr is mostly C++ library output (faster-whisper progress, ffmpeg warnings) that is unlikely to contain API keys. However, if a future translation backend outputs debug info to stderr, there is no safety net.

**Recommendation**: Import `scrub_logs` and apply it to the stderr line before logging:
```python
from .key_manager import scrub_logs
# ...
_log.debug("[worker:%s stderr] %s", job_id, scrub_logs(line_str))
```

---

### 🟡 F11 — `local_path` mode enables arbitrary filesystem reads

**File**: `backend/main.py:574-608`

```python
local_path = cfg.pop("local_path", "")
if not local_path or not _os_module.path.isfile(local_path):
    return JSONResponse({"error": f"找不到文件：{local_path or '(空路径)'}"}, status_code=400)
# ...
with open(local_path, "rb") as src:
```

**Description**: The `/api/jobs` endpoint accepts a `local_path` parameter and reads any file the backend process has access to. There is no restriction to video file extensions, no validation that the path is within an expected directory, and no guard against reading system files. The file content is not returned to the client (only hashed), but the error message confirms or denies file existence (information disclosure).

**Impact**: An attacker with the ability to make HTTP requests to `localhost:8766` could read any file the user can access. While the app is local-only, a malicious website on the same machine could exploit DNS rebinding or simply make fetch requests to localhost. The file content isn't returned, but existence is confirmed, and the file is read into process memory.

**Recommendation**: Restrict `local_path` to only video file extensions (`.mp4`, `.ts`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.webm`). Additionally, validate that the resolved absolute path is within the user's home directory or a whitelist of directories.

---

### 🟢 F12 — pywebview developer tools potentially accessible

**File**: `desktop.py:156-163`

```python
webview.create_window(
    "Video Subber — 视频字幕生成",
    URL,
    width=1100, height=750,
    resizable=True,
    min_size=(800, 500),
)
```

**Description**: pywebview's `create_window()` does not have an explicit parameter to disable developer tools or right-click context menus. Depending on the platform and pywebview version, right-clicking may open a context menu with "Inspect Element" or similar. On some backends, keyboard shortcuts (F12, Ctrl+Shift+I) may open developer tools.

**Impact**: A user with physical access could open dev tools and inspect JavaScript state, potentially viewing API keys from `State.settings.apiKeys` in memory (keys remain in JS memory after being sent to the backend). For a local desktop app, physical access typically implies trust, so this is low risk.

**Recommendation**: If supported by the pywebview version, add `text_select=False` or explicitly disable the context menu. Alternatively, clear `State.settings.apiKeys` key values from JavaScript memory after the key is successfully sent to the backend.

---

### 🟢 F13 — WebSocket has no authentication

**File**: `backend/main.py:844-853`, `backend/ws_manager.py`

```python
@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str):
    await ws_manager.register(job_id, websocket)
```

**Description**: The WebSocket endpoint accepts connections without any authentication. Anyone who knows a job ID (or guesses one) can connect and receive real-time progress updates, including segment counts, current stage, progress percentage, and final success/failure counts.

**Impact**: An attacker on the same machine could monitor video processing jobs. No sensitive data (API keys, video content, translation text) is transmitted over WebSocket — only progress metadata, segment counts, and final status. Acceptable risk for a local desktop app.

**Recommendation**: Consider adding a simple session token (random value generated at server start, embedded in the frontend HTML) if multi-user support or network-accessible deployment is planned.

---

### 🟢 F14 — Job ID entropy: 40 bits

**File**: `backend/main.py:589,619`

```python
jid = uuid.uuid4().hex[:10]
```

**Description**: Job IDs are generated from UUID4 truncated to 10 hex characters = 40 bits of entropy (approximately 1.1 trillion possibilities). Collision probability with single-digit active jobs is negligible (birthday paradox not relevant at these scales). However, guessability at 40 bits means an attacker making 1000 HTTP requests/second would need approximately 34 years to enumerate the full space.

**Impact**: Minimal. An attacker on the same machine could try to guess job IDs to access WebSocket streams or download results, but the search space is too large for practical brute-force attacks over HTTP. For context, the V2.10 audit's S5 noted that this was "borderline" — the practical risk has not changed.

**Recommendation**: Use full UUID4 hex (32 characters, 122 bits) for future-proofing. The extra bytes in filenames, URLs, and JSON responses are negligible.

---

### 🟢 F15 — `innerHTML` with user-supplied filenames in frontend

**File**: `frontend/index.html:907-918` (`addJobCard`), `envBar.innerHTML` assignments

```javascript
card.innerHTML =
    `<div class="job-card-header"><span class="job-card-name">${filename}</span>...`;
```

**Description**: The frontend uses `innerHTML` extensively with dynamic values including `filename` (from user's video file or `local_path` input) and `jid` (server-generated). A maliciously crafted filename (e.g., `<img src=x onerror=alert(1)>.mp4`) could execute JavaScript when inserted via `innerHTML`. The `local_path` value is typed by the user and inserted into the DOM directly.

**Impact**: Since the app is local-only (127.0.0.1), the attacker would need to trick the user into selecting a file with a malicious name, or typing an XSS payload into the local path input. This is a self-XSS scenario with minimal practical risk.

**Recommendation**: Use `textContent` for text values. When HTML structure is needed, HTML-encode the variable parts:
```javascript
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
```

---

### 🟢 F16 — `pyproject.toml` uses `>=` version pins without hash verification

**File**: `pyproject.toml:11-20`

```python
dependencies = [
    "cryptography>=44.0",
    "faster-whisper>=1.0",
    ...
]
```

**Description**: All dependencies use minimum version constraints (`>=`) without upper bounds or hash verification. A compromised upstream package could publish a malicious update that gets installed on the next `pip install` or `uv sync`.

**Impact**: Supply-chain attack vector. An attacker compromising the `deep-translator` PyPI package could publish a malicious version that exfiltrates API keys. The existing `uv.lock` file provides reproducible builds but does not verify package signatures or authenticity.

**Recommendation**: For production builds (PyInstaller EXE), use `pip install --require-hashes` with a fully-pinned `requirements.txt` that includes SHA256 hashes. Commit `uv.lock` to version control and verify it hasn't been tampered with in CI.

---

### 🟢 F17 — `GET /api/config` decryption path conflicts with plaintext storage

**File**: `backend/main.py:511-523`

```python
@app.get("/api/config")
def get_config():
    cfg = load_config()
    for k in cfg.get("apiKeys", []):
        if "key" in k:
            try:
                k["key"] = mask_key(decrypt_api_key(k["key"]))
            except Exception:
                k["key"] = "···（解密失败）"
    return cfg
```

**Description**: The `GET /api/config` endpoint attempts to `decrypt_api_key()` on stored keys. Since keys are currently stored in plaintext (see F1), the Fernet `decrypt()` call raises an exception (Fernet tokens have a specific format that plaintext doesn't match), and the exception handler sets the key to "decryption failed" in Chinese. This means the frontend receives corrupted key masks.

The frontend then merges this corrupted value into `State.settings.apiKeys`, and on the next `saveConfig()`, the corrupted mask string is written back to disk, permanently destroying the actual key value.

**Impact**: This is a data-loss bug triggered by F1. Any existing plaintext keys in `config.json` will be corrupted on the first `GET /api/config` call after server restart. If F1 is fixed and keys are encrypted before saving, this endpoint works correctly.

**Recommendation**: Fix F1 first (encrypt keys on save, decrypt on load). This endpoint will then work as designed. For robustness, add a guard: if the stored value doesn't look like a Fernet token (doesn't start with `gAAAAA`), treat it as legacy plaintext and either encrypt-on-read or mask it directly without attempting decryption.

---

## Architecture Positives

These security design choices are well-implemented and worth noting:

1. **Binding to 127.0.0.1 only** (`main.py:902`, `desktop.py:132`): The server correctly binds to localhost only, preventing remote network access.

2. **CORS restricted to localhost** (`main.py:18-21`): CORS allows only `http://127.0.0.1:8766` and `http://localhost:8766`, blocking cross-origin requests from other domains.

3. **Log scrubbing infrastructure** (`key_manager.py:63-67`, `worker.py:52-54`): The `_ScrubFormatter` wrapping all log handlers is a proactive defense against accidental key leakage in logs. Both `user.log` and `debug.log` pass through `scrub_logs()` before being written to disk.

4. **Fernet encryption via PBKDF2** (`key_manager.py`): The encryption algorithm choice (Fernet with 100,000 PBKDF2 iterations) is sound. The main issue is integration, not algorithm selection — the lock is good, it just isn't being used.

5. **Machine-bound activation** (`main.py:286-302`): Activation files include a machine fingerprint, preventing simple file copying to other machines. The mechanism is not bulletproof but raises the bar above trivial bypass.

6. **No API keys on WebSocket**: WebSocket messages carry only progress metadata (stage, percent, message). API keys travel only over REST endpoints and are never broadcast to WebSocket listeners.

7. **Frontend strips keys from localStorage** (`frontend/index.html:739`): The `saveConfig()` function correctly strips key values before writing to `localStorage`, keeping plaintext keys only in JavaScript memory.

8. **All subprocess calls use list-form args** (`subprocess.Popen([...])`, `subprocess.run([...])`): No `shell=True` anywhere in the codebase. Zero command injection risk in subprocess calls.

9. **`type="password"` on API key input** (`frontend/index.html:327`): Browser will not autocomplete or save the key value.

10. **Deduplication does not leak data**: The hash index system (`GLOBAL_HASH_INDEX`) uses SHA256 hashes of file content + config, never storing or comparing plaintext API keys.

---

## Threat Model

Video Subber V2.11 is a **single-user local desktop application**. The primary threats are:

| Threat Actor | Capability | Mitigation Status |
|-------------|------------|-------------------|
| Malicious website (DNS rebinding to localhost) | Make HTTP requests to `127.0.0.1:8766` | CORS restricts to localhost origin ✅ |
| Local malware / another user on same machine | Read `~/.video-subber/config.json` | Plaintext key storage means no protection 🔴 F1 |
| Source code reader (open source, decompiled EXE) | Extract hardcoded credentials | Baidu keys exposed 🔴 F2; dev codes exposed 🟡 F6 |
| Network eavesdropper (same LAN) | Capture HTTP traffic to external APIs | TLS on all external API calls ✅; Gemini key in URL weakens this 🟡 F3 |
| Physical access to unlocked machine | Open pywebview dev tools, read files | Plaintext keys + dev tools = full exposure 🟢 F12 |
| Compromised PyPI package | Publish malicious update | `>=` pins without hashes 🟢 F16 |

---

## Prioritized Remediation Plan

### Before Release (Must Fix)

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| P0 | F1 | Medium | Wire `encrypt_api_key` into `PUT /api/config` save path; wire `decrypt_api_key` into worker config load |
| P0 | F2 | Trivial | Remove hardcoded Baidu credentials; use env var only; rotate compromised keys immediately |
| P1 | F3 | Small | Move Gemini API key from URL query string to `x-goog-api-key` request header |

### Next Iteration (Should Fix)

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| P2 | F4 | Small | Generate random salt on first run, store in `~/.video-subber/.salt` |
| P2 | F5 | Trivial | Raise error or log prominent warning if `cryptography` unavailable |
| P2 | F6 | Small | Remove hardcoded dev codes; add rate limiting to activate endpoint |
| P2 | F7 | Small | Add tier metadata to activation records; audit log for `paid_3` usage |
| P2 | F8 | Small | Add more entropy sources to machine fingerprint |
| P3 | F9 | Small | Sanitize exception messages in all translation backends |
| P3 | F10 | Trivial | Apply `scrub_logs()` in `_read_stderr` |
| P3 | F11 | Small | Restrict `local_path` to video extensions and/or home directory |

### Future (Nice to Have)

| Priority | Finding | Effort | Description |
|----------|---------|--------|-------------|
| P4 | F12 | Small | Disable context menu / dev tools in pywebview if possible |
| P4 | F15 | Small | Replace `innerHTML` with safe DOM construction or HTML-encoding |
| P4 | F16 | Medium | Add `--require-hashes` to build process |
| P4 | F17 | Trivial | Add plaintext-vs-encrypted guard in `get_config()` |
| P4 | F14 | Trivial | Use full UUID4 hex for job IDs |

---

## Conclusion

V2.11 has a solid security architecture for a local desktop application, with deliberate choices around localhost binding, CORS restriction, log scrubbing, and machine-bound activation. The three critical findings (F1, F2, F3) are all integration gaps rather than design flaws:

- **F1**: The encryption layer exists but is not connected to the save/load path (the lock is installed but the door isn't closed).
- **F2**: Production credentials in source code (a common but severe oversight — also flagged in V2.10 audit as S2, not yet fixed).
- **F3**: API key in URL (a well-known anti-pattern with a straightforward fix).

All three are fixable with targeted, low-risk changes. Once addressed, the application's security posture would be appropriate for a local desktop tool handling third-party API credentials.

**Security Readiness Score: 6.0/10** (downgraded from V2.10's 6.5 due to newly discovered F1, F2, F3)

Scoring breakdown:
- F1 (plaintext keys): -1.5
- F2 (hardcoded Baidu creds): -1.0
- F3 (Gemini key in URL): -0.5
- F4-F5 (encryption hardening): -0.5
- F6-F11 (defense-in-depth): -0.5

---

*Report generated by automated security audit. No dynamic testing was performed. Findings based on static code analysis of all backend Python files, frontend HTML/JS, and desktop launcher.*
