"""Shared model metadata cache — avoids circular dependency between main.py and translation layer.

P186: _MODEL_CONTEXT_CACHE was previously defined in main.py and imported via
lazy import from translation/base.py. Moving it here breaks the reverse dependency.
"""

_MODEL_CONTEXT_CACHE: dict[str, int] = {}  # {model_id: max_input_tokens}
