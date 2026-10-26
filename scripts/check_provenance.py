"""provenance:check - every file ported from Harness² keeps its Apache header and is listed.

A ported file starts with the upstream copyright header and carries a modification notice.
`third_party/harness2/PROVENANCE.md` lists each ported file in a table. Files without the upstream
header are original whiletrue code and need no entry.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTED_DIR = "whiletrue/rsi/harness2"
HEADER = "# Copyright 2026 Google LLC"
APACHE = "Licensed under the Apache License, Version 2.0"
MODIFIED = "# Modified by the whiletrue authors"
ROW = re.compile(r"\|\s*`[^`|]+`\s*\|\s*`(" + re.escape(PORTED_DIR) + r"/[^`|]+)`\s*\|")


def listed(provenance: Path) -> set[str]:
    return set(ROW.findall(provenance.read_text(encoding="utf-8")))


def problems(root: Path = ROOT) -> list[str]:
    provenance = root / "third_party" / "harness2" / "PROVENANCE.md"
    if not provenance.is_file():
        return [f"missing {provenance.relative_to(root)}"]
    table = listed(provenance)
    found: list[str] = []
    ported: set[str] = set()
    for path in sorted((root / PORTED_DIR).glob("*.py")):
        rel = path.relative_to(root).as_posix()
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
        if HEADER in head:
            ported.add(rel)
            if APACHE not in head:
                found.append(f"{rel}: upstream header present but the Apache notice is missing")
            if MODIFIED not in head:
                found.append(f"{rel}: ported file lacks the modification notice")
            if rel not in table:
                found.append(f"{rel}: ported file is not listed in PROVENANCE.md")
    for rel in sorted(table - ported):
        if not (root / rel).is_file():
            found.append(f"{rel}: listed in PROVENANCE.md but the file does not exist")
        else:
            found.append(f"{rel}: listed in PROVENANCE.md but has no upstream header")
    if not (root / "third_party" / "harness2" / "LICENSE").is_file():
        found.append("third_party/harness2/LICENSE is missing")
    return found


def main() -> int:
    found = problems(ROOT)
    for line in found:
        print(f"provenance: {line}")
    if found:
        return 1
    print("provenance:check ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
