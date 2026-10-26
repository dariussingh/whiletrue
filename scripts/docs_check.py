"""docs:check - validates that markdown links between repo docs resolve.

Starts minimal (link validation); extend with example/CLI-reference checks.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_provenance as provenance_check

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"node_modules", ".git", ".devenv", ".direnv", ".venv", "dist", "coverage", ".whiletrue"}
FENCE = re.compile(r"```.*?```", re.DOTALL)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def markdown_files(directory: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.name in SKIP:
            continue
        if path.is_dir():
            found += markdown_files(path)
        elif path.suffix == ".md":
            found.append(path)
    return found


def main() -> int:
    errors = 0
    for file in markdown_files(ROOT):
        text = FENCE.sub("", file.read_text(encoding="utf-8"))
        for match in LINK.finditer(text):
            target = match.group(1).split("#")[0]
            if not target or re.match(r"^[a-z]+:", target, re.IGNORECASE):
                continue
            if not (file.parent / target).exists():
                print(f"{file.relative_to(ROOT)}: broken link -> {match.group(1)}")
                errors += 1
    if errors:
        return 1
    print("docs:check ok")
    return provenance_check.main()


if __name__ == "__main__":
    sys.exit(main())
