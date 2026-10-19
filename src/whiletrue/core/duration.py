from __future__ import annotations

import math
import re

_UNITS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}
_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)$")


def parse_duration(value: str | int | float) -> int:
    """Parses "90m", "30s", "1h", "250ms" (or a bare number of milliseconds) into milliseconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid duration: {value}")
        return int(value)
    match = _PATTERN.match(str(value).strip())
    if not match:
        raise ValueError(f'Invalid duration "{value}" (use e.g. 30s, 90m, 1h)')
    return round(float(match.group(1)) * _UNITS[match.group(2)])
