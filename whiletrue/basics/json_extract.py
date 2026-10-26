"""Pull structured output out of a model's free-text reply."""

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    """The last JSON object or array in a model's reply (raw, or inside a code fence)."""
    candidates = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates.append(text)
    for chunk in reversed(candidates):
        decoder = json.JSONDecoder()
        found: Any = None
        for start in (m.start() for m in re.finditer(r"[{\[]", chunk)):
            try:
                value, _ = decoder.raw_decode(chunk[start:])
            except ValueError:
                continue
            found = value
            break
        if found is not None:
            return found
    return None
