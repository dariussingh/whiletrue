"""Spec links in issue bodies (OQ-5).

An issue points at the spec text an agent should read with a line of the form

    spec: specs/m3/api.md#L40-L88

(also `specs:`, case-insensitive; several links separated by commas or spaces). A bare path means
the whole file; `#L40` is one line and `#L40-L88` a range. Links are returned as written, so the
knowledge provider can resolve them later.
"""

from __future__ import annotations

import re

_LINE = re.compile(r"^[ \t>*-]*specs?[ \t]*:[ \t]*(.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
_REF = re.compile(r"^[^\s#]+(?:#L\d+(?:-L\d+)?)?$")


def parse_spec_refs(body: str | None) -> list[str]:
    refs: list[str] = []
    for match in _LINE.finditer(body or ""):
        for raw in re.split(r"[,\s]+", match.group(1)):
            ref = raw.strip("`<>()[]")
            if ref and _REF.match(ref) and ref not in refs:
                refs.append(ref)
    return refs


def blocked_by_in_body(body: str | None) -> list[int]:
    """`Blocked by #12` lines: the fallback when the API has no dependency data."""
    found: list[int] = []
    for match in re.finditer(
        r"blocked[ \t]+by[ \t:]*((?:#\d+[ \t,]*(?:and[ \t]*)?)+)", body or "", re.I
    ):
        for number in re.findall(r"#(\d+)", match.group(1)):
            if int(number) not in found:
                found.append(int(number))
    return found
