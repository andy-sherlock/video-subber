# Steve Jobs Final Review -- Video Subber V2.11

**Date**: 2026-05-28
**Reviewer**: Steve Jobs perspective
**Sources**: Code Review, Security Audit, Design Review, Health Report, Desktop Port Design, QA Report (6 reports)
**Question**: Is V2.11 ready to ship as a Windows EXE?

---

## Verdict: No-Go

This is not ready to ship. There are six problems that a real user would hit in their first five minutes with the EXE. Three of them are deal-breakers on their own. Fix these six, and we have something worth putting in front of beta testers. Ship without fixing them, and we have an EXE where the progress bar never moves, API keys sit in plaintext on disk, and anyone who runs `strings` on the binary can steal paid API credentials.

Let me be precise.

---

## The Six Things That Must Be Fixed Before Anyone Touches This EXE

These are not debatable. They are not "nice to have." They are things a user will encounter, will be confused or angry about, and will tell their friends not to use this software.

### 1. FROZEN Mode Progress Is Completely Broken (C1 from Code Review)

**Files**: `desktop.py:81-90`, `backend/worker.py:538-545`

This is the most embarrassing one. The entire point of V2.11 is to package as a Windows EXE. In that EXE, the progress bar stays at 0%. The whole time. From start to finish. The user sits there staring at a dead screen while the app does real work underneath.

The root cause: `stderr_progress()` in `worker.py` writes progress to stdout -- but in FROZEN/exe mode, there is no parent process reading stdout. The 17 progress calls that drive the pipeline UI (extract, transcribe, translate, subtitle, burn) all vanish into a pipe nobody reads. Meanwhile, `desktop.py` correctly patches `_worker.emit` to route through WebSocket -- but `stderr_progress` bypasses `emit` entirely.

The fix is straightforward: either replace `stderr_progress` in the same way `desktop.py` already replaces `emit`, or make `stderr_progress` internally detect FROZEN mode and call the emit path. One file, one concept, about 20 lines of code.

**If we ship with this, the EXE is broken. There is no point in packaging.**

### 2. User API Keys Are Stored in Plaintext on Disk (F1 from Security + D2 from Health)

**Found independently by two different reviewers using two different methods. This is the strongest signal in all six reports.**

**Files**: `backend/main.py:526-531`, `backend/config_manager.py:35-38`, `backend/key_manager.py:39-42`

The encryption infrastructure exists. Fernet with 100,000 PBKDF2 iterations. The lock is manufactured and bolted to the door. But nobody ever connected the lock to the door frame.

The save path (`PUT /api/config` -> `config_manager.save_config()`) does a raw `json.dump()`. The load path (`worker.py` reading config) reads plaintext directly. `encrypt_api_key()` and `decrypt_api_key()` exist, are correctly implemented, and have zero callers in the production code path. The health report flagged `encrypt_api_key()` as dead code. The security audit traced the entire save flow end-to-end and confirmed the gap.

Two changes needed:
- In `main.py` `save_config()`: iterate `apiKeys`, call `encrypt_api_key()` on each plaintext key before writing
- In `worker.py`: call `decrypt_api_key()` on each key when loading config for translation
- In `main.py` `get_config()`: add a guard for legacy plaintext values (they won't look like Fernet tokens starting with `gAAAAA`) so existing keys aren't corrupted

The encryption code is already written. It needs to be called.

**Users trust us with their API keys. Plaintext storage is a betrayal of that trust.**

### 3. Hardcoded Baidu Production Credentials in Source Code (F2 from Security)

**File**: `backend/translation/baidu.py:5-6`

```python
BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID", "20260520002616499")
BAIDU_SECRET = os.environ.get("BAIDU_SECRET", "NGw5pcrCkfDqe6gpygvT")
```

These are live, paid Baidu Translate API credentials. They ship in the source. Anyone who runs `strings` on the EXE can extract them. Anyone who reads the open-source repo can use them. This was flagged in the V2.10 security audit (S2) and was not fixed. It is now flagged again in V2.11.

The fix is trivial: remove the hardcoded defaults, use environment variables only with empty-string fallbacks. Then rotate the compromised credentials on Baidu's console immediately (this report will exist on disk; treat the current credentials as burned).

**Two audits, same finding, still not fixed. This is not a technical problem. It is a discipline problem.**

### 4. Gemini API Key Leaked Through URL Query String (F3 from Security)

**File**: `backend/translation/gemini.py:29-36`

The Gemini backend puts the API key in the URL as a query parameter:
```python
url = f"...?key={self._key}"
```

When the HTTP request fails -- and HTTP requests do fail -- the `HTTPError` exception includes the full URL in its string representation. That traceback goes into `debug.log`. Now the user's Gemini API key is sitting in a plaintext log file on disk.

Google's Gemini API supports the `x-goog-api-key` HTTP header. The key belongs in a header, not in a URL. This is a one-line change to the request construction plus adding the header to `_build_headers()`.

**Keys in URLs is a well-known anti-pattern. The fix is documented in the security audit report. Do it.**

### 5. Double-Clicking the EXE Opens Two Broken Windows (A4 from Desktop Port Design)

**File**: `desktop.py` (no single-instance lock exists)

Current behavior when the user double-clicks the EXE twice:
1. Instance 1 starts, uvicorn binds port 8766, works fine
2. Instance 2 starts, uvicorn fails to bind port 8766 (address in use)
3. Instance 2's health poll loop times out silently (see item 6 below)
4. Instance 2 opens a webview window anyway, pointed at a server that isn't running
5. User sees two windows -- one working, one showing a connection error

The fix: before opening any window, try to bind port 8766. If it's taken, another instance is already running. Bring the first instance's window to front (or just exit cleanly). About 30 lines of Python in `desktop.py`.

**First-run experience matters. Two windows, one broken, is a terrible first impression.**

### 6. Health Poll Timeout Opens a Blank Error Window (A1-1 from Desktop Port Design)

**File**: `desktop.py:143-151`

When the backend server fails to start within 5 seconds (slow machine, cold start, model scanning), the health poll loop exits silently. No error check. No "did it actually connect?" The code proceeds to `webview.create_window()` pointing at a server that isn't running. The user sees a blank white page with a connection error. No explanation. No retry. No "something went wrong" dialog.

Fix: after the 50-iteration poll loop, check whether the health check actually succeeded. If not, show a clear error message (at minimum `print()` and `sys.exit(1)`, ideally a native error dialog). Do not open a window pointed at nothing.

**Silent failure is the worst kind of failure. The user should never see a blank white page.**

---

## What Should Be Fixed Before Giving This to Beta Testers

These are not release blockers. An internal tester can work around them. But they cause friction, and friction in beta means bad feedback and wasted time.

### 7. Window Close Kills Workers Without Cleanup (A1-2 from Desktop Port Design)

Closing the window kills uvicorn's daemon thread. Worker subprocesses become orphans, keep consuming CPU/GPU. Cache files being written at close time may be corrupted. No "a job is running, are you sure?" confirmation.

Fix: register a close handler via pywebview's `window.events.closing` that cancels all running jobs, waits briefly, then shuts down cleanly.

### 8. Download Links Don't Work in pywebview (A6-1 from Desktop Port Design)

In a browser, `<a href="/api/jobs/x/download/srt">` triggers the browser's download manager. In pywebview, the file either opens in the system browser or gets rendered inline. The user clicks "Download SRT" and nothing visible happens.

Fix: expose a native save-file dialog through pywebview's JS bridge (`window.pywebview.api`). Download button calls the bridge, Python shows a "Save As" dialog via `tkinter.filedialog` or `win32com`, then downloads to the chosen path.

### 9. Activation Code System Has No Tier Differentiation (B2 from Desktop Port Design)

All activation codes grant full `paid_3` access. There is no way to give a beta tester `paid_1` (bring your own keys) without also giving them platform-provided LLM keys. Every activation code is a master key.

Fix: add tier metadata to activation codes (`{"code": "...", "tier": "paid_1", "fingerprint": "..."}`), then cap `_effective_tier()` based on the activation code's granted tier. The 7 specific changes are documented in the Desktop Port Design report, Section B2.

### 10. Frontend Tier Display Uses Wrong Data Source (W1 from Code Review)

**File**: `frontend/index.html:510-531`

The frontend decides what tier UI to show based on `State.settings.tier` (local JS state) instead of `data.current_tier` (the backend's authoritative tier resolution). In normal operation they match. But when the backend downgrades a request, the frontend shows features the user can't actually use.

Fix: switch three tier checks from `State.settings.tier` to `data.current_tier`.

### 11. Minimum Window Height Is Too Small (D1 from Design Review)

The minimum window is 800x500. Actual content needs ~1182px vertically. On first open, even with everything collapsed, the user sees only the upload area and maybe one setting. Everything below requires scrolling.

Fix: raise `min_size` from `(800, 500)` to `(800, 650)` in `desktop.py:162`. One line.

### 12. Progress Bar Pulse Animation Only During Transcription (D16 from Design Review)

The progress bar's `working` pulse animation only activates during the transcribe stage (10-35%). Translation can consume 45% of total pipeline time with a completely static progress bar. The user thinks the app froze.

Fix: extend the pulse animation to all `running`/`queued` states. About 5 lines in `handleCardMsg()`.

---

## What Can Wait (Public Release / V2.12)

These are real issues. They should be fixed eventually. They do not block beta.

### From Design Review -- 8 Quick Wins (~20 minutes total)

| ID | Issue | Fix |
|----|-------|-----|
| D3 | Dark mode dashed border barely visible | Change `--border` dark value to `#8e8e93` |
| D6 | Subtitle preview box invisible in dark mode | Add `border:1px solid #555` in dark mode |
| D17 | Two overlay backgrounds have different opacity (0.4 vs 0.5) | Unify to 0.5 |
| D18 | Five buttons use inline styles instead of CSS classes | Extract button classes |
| D19 | Input padding inconsistent (6px vs 8px) | Unify to 8px 12px |
| D2 | Toast: no exit animation, no stacking, no close button, no type differentiation | Rewrite with CSS transitions |
| D8 | Model dots have no hover state | Add `:hover { border-color: var(--blue) }` |
| D20 | Native `confirm()` dialog breaks visual consistency | Replace with custom modal |

### From Health Report -- Dead Code Cleanup (~15 minutes)

- Remove 4 unused imports: `key_manager.py:os`, `gemini.py:time`, `gemini.py:TranslationResult`, `base.py:field`
- Delete `get_video_duration()` in `extractor.py` (V1.3 legacy, zero callers)
- Mark 7 `understand_context()` methods as `# TODO(V3)` or move to base class
- Mark `usage_reporter.py` as V3 placeholder (90 lines, all no-op until `SERVER_URL` is set)

### From Security Audit -- Defense-in-Depth

| ID | Issue |
|----|-------|
| F4 | PBKDF2 salt is hardcoded -- generate random salt per installation |
| F5 | `encrypt_api_key()` silently returns plaintext if `cryptography` unavailable |
| F6 | Hardcoded dev codes "ANDY"/"MENG" in source |
| F8 | Machine fingerprint uses only MAC + OS name -- add more entropy |
| F9-F11 | Log sanitization gaps in Baidu backend and worker stderr reader |

### From Code Review -- Minor

| ID | Issue |
|----|-------|
| C2 | Five dead DOM elements from old single-task UI (never visible to user) |
| C3 | `showError()` function never called (dead code) |
| W2 | Font list hardcoded to macOS fonts only |
| W3 | `onModelChange()` flash when switching to custom platform |
| W4 | `usage_reporter.replay_fallback()` never called |
| W5 | `DEFAULT_CONFIG` missing `burn`, `subtitles`, `contextAware` fields |
| W6 | `_llmFallbackPending` declared but never used |
| W7 | `handleCardMsg` ignores `token_usage` messages |
| W8 | `GET /api/jobs/{id}` fallback path returns incomplete fields |

### From QA

| ID | Issue |
|----|-------|
| Q4 | Download endpoint 404 error is in English ("not found") while job query endpoint is Chinese ("未找到该任务") |

---

## What Is Actually Good

I want to be clear: a lot of this product is right.

**The pipeline works.** QA tested a 718MB real video end-to-end. Transcription, hallucination filtering, language detection, translation, checkpoint caching -- all correct. The worker correctly identified and discarded a music hallucination segment ("ТРЕВОЖНАЯ МУЗЫКА"). That is non-trivial engineering that took multiple versions to get right.

**The security architecture is well-designed.** Localhost binding only. CORS restricted to 127.0.0.1. Log scrubbing infrastructure in place. Fernet encryption with 100,000 PBKDF2 iterations. All subprocess calls use list-form args. Zero `shell=True`. The architecture has taste. The failures are integration failures (the encryption exists but isn't connected), not design failures.

**The UI has soul.** The design review gave an A- on the AI Slop Score. No purple gradients. No floating blobs. No three-column emoji cards. Apple HIG influence throughout. 12 CSS custom properties with proper light/dark theming. A clear, restrained design language. The hero copy is human: "拖进去，等一会，就能看懂" -- that is a product definition, not marketing copy.

**The API error messages are in Chinese.** QA verified 6 of 8 error paths return Chinese messages. The 404 that was English in V2.10 (Q1) is now Chinese in V2.11. This attention to detail matters for the target audience.

**V2.10 bugs were actually fixed.** Health report confirmed all three V2.10 critical issues resolved (dead imports removed, unreachable code eliminated, 13 console.log statements cleaned). QA confirmed the 404 localization and version number fixes. Follow-through is real.

**Frontend key hygiene is thoughtful.** `saveConfig()` explicitly strips API key values from localStorage before writing. Only metadata is persisted client-side. The encryption key manager exists and is correctly implemented. The architecture anticipates the threat model.

---

## Cross-Report Validation

One of the most useful signals in a multi-review process is when different reviewers, looking at different things, independently find the same problem. Here are the cross-validated findings:

| Problem | Found By | Signal Strength |
|---------|----------|----------------|
| API keys stored in plaintext | Security (F1) + Health (D2) | **Strongest** -- security traced the save path forward, health found the dead encrypt function backward |
| Hardcoded Baidu credentials | Security V2.10 (S2) + Security V2.11 (F2) | **Strong** -- same finding across two audit cycles, still not fixed |
| FROZEN progress loss | Code Review (C1) + QA (FROZEN mode untested) | **Strong** -- critical path broken for the exact use case V2.11 is built for |
| No graceful shutdown | Desktop Design (A1-2) | **Standalone but well-analyzed** |
| No single-instance lock | Desktop Design (A4) | **Standalone but high user impact** |
| Activation tier system missing | Desktop Design (B2) + Security (F7) | **Moderate** -- both note lack of tier gating |

The strongest signal is the API key encryption gap. The security audit found it by tracing the save path end-to-end. The health report found it by scanning for dead functions. Two completely different methodologies converged on the same conclusion. This is not a false positive.

---

## The Scorecard

| Dimension | Score | What It Means |
|-----------|-------|---------------|
| Core pipeline | 9/10 | Works. Tested with real video. No crashes. Hallucination filter active and correct. |
| Security architecture | 7/10 | Well designed. Three integration gaps. |
| Security implementation | 4/10 | Plaintext keys + hardcoded creds + key in URL. |
| UI design quality | 8/10 | Tasteful. Apple-influenced. Not AI slop. |
| UI polish | 6/10 | 17 spacing values, 11 font sizes across 995 lines. |
| Desktop readiness | 3/10 | No progress in EXE. No single-instance lock. No graceful shutdown. Downloads broken in webview. |
| Code quality | 5.5/10 | Zero tests. Dead code. But clean syntax, good types, solid architecture. |
| QA coverage | 7/10 | Core flows tested. Windows, WebSocket, 7 LLM backends untested. |
| **Overall** | **5.5/10** | **Not ready. Clear path to ready.** |

---

## Path to Go

Fix these six items, in this order:

| # | Item | Time | Source |
|---|------|------|--------|
| 1 | FROZEN progress loss -- route `stderr_progress` through emit in FROZEN mode | 30 min | Code Review C1 |
| 2 | Plaintext API key storage -- wire encrypt/decrypt into save/load paths | 1 hour | Security F1 + Health D2 |
| 3 | Hardcoded Baidu credentials -- remove defaults, rotate keys on Baidu console | 15 min | Security F2 |
| 4 | Gemini key in URL -- move to `x-goog-api-key` header | 20 min | Security F3 |
| 5 | Single-instance lock -- port-binding check before window open | 45 min | Desktop A4 |
| 6 | Health poll timeout -- error check after poll loop, don't open blank window | 15 min | Desktop A1-1 |

**Total: about 3 hours of focused work.**

After these six are fixed, the EXE will function correctly for a user. Then fix the six Should-Fix items (7-12 above, about 4 more hours) before giving it to beta testers.

After beta feedback, tackle the Nice-to-Have items before public release.

---

## Final Word

This product has a soul. The pipeline is real engineering -- hallucination filtering, checkpoint caching, multi-backend translation with priority fallback. The design has taste -- someone sat down and thought about it. The error messages are in Chinese because someone cared enough to translate them. These are not small things.

But shipping an EXE where the progress bar never moves is like shipping an iPod where the screen stays black while music plays. It does not matter how good the rest is. The thing the user sees is broken.

Fix the six. Then ship the beta. Then make it great.

**No-Go. Fix the six blocking items. Come back.**

---

*"Details matter. It's worth waiting to get it right."*
