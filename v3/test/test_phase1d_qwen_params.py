"""P147 → P207: Qwen3-ASR generation parameters.

P147 originally added anti-collapse kwargs (max_tokens, repetition_penalty)
to _qwen_gen_kwargs. P156 recalibrated them. P207 (qwen-asr v0.0.6) removed
the need for external anti-collapse params entirely:

  - max_new_tokens → built into constructor (default 512)
  - repetition_penalty → unnecessary (temperature=0.0, deterministic output)
  - repetition_context_size → unnecessary (no randomness to loop)
  - _QWEN_TOKENS_PER_SEC / _QWEN_MIN_TOKENS → deleted (no dynamic budget needed)

The _qwen_gen_kwargs function itself was inlined per Jobs' review.
Behavior is now verified by the P207 (test_p207_fa_official_api.py)
and P212 (test_p212_fa_quality.py) integration tests.
"""
