# Qwen3-ASR Russian Accuracy Research

**Date:** 2026-06-05

---

## 1. Qwen3-ASR Russian FLEURS WER (Exact Numbers)

Source: Qwen3-ASR Technical Report, Appendix Table A.2(b) [arXiv:2601.21337](https://ar5iv.labs.arxiv.org/html/2601.21337)

| Model | Russian (ru) FLEURS WER |
|-------|------------------------:|
| Qwen3-ASR-0.6B | **9.91** |
| Qwen3-ASR-1.7B | **5.99** |

Both numbers are **WER (Word Error Rate), where lower is better.**

---

## 2. Whisper large-v3 Russian FLEURS WER (Comparison)

From OpenAI's official large-v3 release data and third-party reproductions:

| Model | Russian (ru) FLEURS WER |
|-------|------------------------:|
| Whisper large-v3 | **~5.0** |

Whisper large-v3 has a slight edge over Qwen3-ASR-1.7B on Russian specifically (5.0 vs 5.99), but both are in the "excellent" range. Note that the Qwen3 paper does NOT publish per-language Whisper comparisons -- only aggregate scores.

**Aggregate FLEURS comparison (Qwen3 report Table 5):**

| Language Set | Whisper large-v3 | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B |
|---|---|---|---|
| Fleurs (12 core langs) | 5.27 | 7.57 | **4.90** |
| Fleurs-dagger (+8 langs) | 6.85 | 10.37 | **6.62** |
| Fleurs-dagger-dagger (+10 more) | **8.16** | 21.80 | 12.60 |

Key insight: Qwen3-ASR-1.7B **beats** Whisper on the 12 core languages (4.90 vs 5.27) and on the 20-language set (6.62 vs 6.85), but **loses** on the full 30-language set (12.60 vs 8.16). This means Qwen3's weakness is in the long-tail/low-resource languages (Hungarian, Persian, Czech, etc.), not in the core languages like Russian.

---

## 3. What WER (Word Error Rate) Means

### Is lower better?
**Yes. Lower WER = better accuracy.** WER 0% means perfect transcription.

### Formula
```
WER = (Substitutions + Deletions + Insertions) / Total Reference Words x 100%
```
- **Substitutions:** A word replaced with a wrong word
- **Deletions:** A spoken word missing from the output
- **Insertions:** An extra word in the output that wasn't spoken

### What does 5.99 WER mean in practical terms?
**~94% of words are correctly transcribed.** About 6 words out of every 100 are wrong (substituted, deleted, or inserted).

### What's considered "good" vs "bad" WER?

| WER | Quality | Practical Meaning |
|-----|---------|-------------------|
| 0-5% | Human-level / Excellent | Comparable to professional human transcription on clean speech |
| 5-10% | Excellent / Production-ready | Suitable for most applications; ~90-95% accuracy |
| 10-15% | Very Good | Acceptable for many business uses |
| 15-25% | Good | Needs some review; useful for training data |
| 25-50% | Moderate | Every 2nd-4th word wrong; needs significant review |
| 50-75% | Poor | Mostly unusable |
| 75%+ | Very Poor | Essentially broken |

**Human baseline:** Professional human transcribers achieve ~4-5% WER on clean speech.

### Why CJK languages use CER (Character Error Rate) instead
Chinese, Japanese, and Korean use **CER (Character Error Rate)** because:
- These languages don't use spaces between words
- "Word" boundaries are ambiguous -- what counts as a "word" in Chinese?
- Characters are the unambiguous unit of written text
- CER works the same formula as WER but operates on individual characters instead of words

For Chinese languages, Qwen3-ASR reports CER, not WER (e.g., zh = 2.41 CER).

---

## 4. Qwen3-ASR Complete FLEURS Per-Language WER Table

Source: Qwen3-ASR Technical Report, Appendix Table A.2(b)

**Sorted by Qwen3-ASR-1.7B WER (best to worst):**

| Rank | Language | Code | 0.6B WER | 1.7B WER |
|------|----------|------|----------|----------|
| 1 | Italian | it | 4.99 | **2.41** |
| 2 | Mandarin | zh | 2.88 | **2.41** (CER) |
| 3 | Korean | ko | 3.72 | **2.57** |
| 4 | English | en | 4.39 | **3.35** |
| 5 | Spanish | es | 4.94 | **3.36** |
| 6 | German | de | 6.48 | **3.92** |
| 7 | Portuguese | pt | 6.21 | **3.92** |
| 8 | Cantonese | yue | 5.79 | **3.98** |
| 9 | French | fr | 7.72 | **4.75** |
| 10 | Indonesian | id | 7.92 | **5.16** |
| 11 | Japanese | ja | 8.33 | **5.20** |
| 12 | Vietnamese | vi | 8.52 | **5.55** |
| **13** | **Russian** | **ru** | **9.91** | **5.99** |
| 14 | Thai | th | 8.34 | **6.32** |
| 15 | Dutch | nl | 14.02 | **7.04** |
| 16 | Turkish | tr | 16.18 | **9.47** |
| 17 | Malay | ms | 17.66 | **10.39** |
| 18 | Polish | pl | 24.71 | **12.54** |
| 19 | Arabic | ar | 25.51 | **16.98** |
| 20 | Hindi | hi | 19.12 | **17.15** |
| 21 | Macedonian | mk | 37.26 | **19.05** |
| 22 | Swedish | sv | 35.87 | **19.36** |
| 23 | Romanian | ro | 44.26 | **20.70** |
| 24 | Danish | da | 36.36 | **21.00** |
| 25 | Czech | cs | 47.67 | **22.42** |
| 26 | Filipino | fil | 36.10 | **24.29** |
| 27 | Finnish | fi | 46.59 | **25.23** |
| 28 | Greek | el | 49.67 | **28.08** |
| 29 | Persian | fa | 53.76 | **29.90** |
| 30 | Hungarian | hu | 59.47 | **34.22** |

**Comparison with Whisper large-v3:** The Qwen3 paper does NOT publish per-language Whisper comparisons. Only aggregate numbers are available. Whisper's aggregate FLEURS on the full 30-language set is **8.16** (vs Qwen3-1.7B's 12.60), suggesting Whisper does better on the long-tail languages at the bottom of this table.

---

## 5. Is Russian a "Weak" Language for Qwen3-ASR? NO.

### The Numbers Say Russian is STRONG

Russian ranks **13th out of 30 languages** on the FLEURS benchmark for Qwen3-ASR-1.7B. At 5.99 WER:
- Russian is in the **excellent/production-ready** range (top half)
- ~94% of words are correctly transcribed
- Russian outperforms Thai, Dutch, Turkish, Polish, Arabic, Hindi, and all Eastern European languages

Russian is firmly in Qwen3-ASR's **strong language tier** (the top 14 languages all have WER < 10%).

### The REAL Weak Languages

The languages Qwen3-ASR genuinely struggles with (all WER > 20%) are:
- Hungarian (34.22), Persian (29.90), Greek (28.08), Finnish (25.23), Filipino (24.29), Czech (22.42), Danish (21.00), Romanian (20.70)

These are mostly Eastern European / Finno-Ugric languages. Russian is NOT among them.

### If You're Getting Very Few Output Sentences for Russian Audio...

Since Qwen3-ASR's Russian accuracy is actually good, the problem is likely NOT with the model's Russian language capability. Possible causes:

1. **VAD (Voice Activity Detection) cutting out speech** -- The VAD module may be filtering out Russian speech segments, especially if speech is soft, fast, or has background noise
2. **Language auto-detection failing** -- Qwen3-ASR may misidentify Russian audio as another language. The technical report notes confusion between Malay/Indonesian. A troubleshooting guide (Chinese) documents that auto-detection can misidentify accented speech. **Fix:** Manually specify `language="ru"` instead of using auto-detect
3. **Audio format/quality** -- Wrong sample rate (expects 16kHz), corrupted segments, or very long audio (>10-20 min) can cause empty outputs
4. **Known Russian-specific issue #1561** -- Qwen3-ASR has a bug where it inserts spaces before punctuation in Russian (`"привет , как дела ?"` instead of `"привет, как дела?"`). This is a formatting issue, not a missing-output issue
5. **Hallucination/silence issue** -- Some ASR models output nothing (rather than garbage) when they're uncertain about the audio content

### Recommended Fixes

1. **Explicitly set language:** `language="ru"` (never rely on auto-detect for Russian)
2. **Verify audio is valid:** 16kHz sample rate, WAV or MP3, no corrupted segments
3. **Check your VAD settings:** Lower the VAD threshold or disable VAD entirely for testing
4. **Test with a known-good Russian audio clip** from FLEURS to isolate whether it's an audio issue or a model/pipeline issue

---

## Sources

- [Qwen3-ASR Technical Report (arXiv:2601.21337)](https://ar5iv.labs.arxiv.org/html/2601.21337) -- Appendix Table A.2(b) for per-language FLEURS WER
- [Qwen3-ASR Official Page](https://qwen-ai.com/qwen-asr/#benchmarks) -- Aggregate benchmarks
- [Qwen3-ASR-1.7B HuggingFace Model Card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- [Whisper large-v3 Release Discussion](https://github.com/openai/whisper/discussions/1762) -- Official benchmark chart (image only, not text)
- [Whisper large-v3 Russian Fine-tuned Model](https://huggingface.co/antony66/whisper-large-v3-russian)
- [QwenLM/Qwen3 Issue #1561](https://github.com/QwenLM/Qwen3/issues/1561) -- Russian spacing bug
- [QwenLM/Qwen3 Issue #928](https://github.com/QwenLM/Qwen3/issues/928) -- Russian/Chinese language mixing (Qwen2.5, not ASR)
