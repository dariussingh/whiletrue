"""Command-line argument parsing: a table of options, no per-option branches."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Args:
    positionals: list[str] = field(default_factory=lambda: [])
    file: str = "whiletrue.yaml"
    force: bool = False
    help: bool = False
    note: str | None = None
    step: str | None = None
    from_step: str | None = None
    follow: bool = False
    yes: bool = False


#: Options that take a value: every spelling to the field it sets.
VALUE_OPTIONS: dict[str, str] = {
    "-f": "file",
    "--file": "file",
    "--from": "from_step",
    "-m": "note",
    "--note": "note",
    "--step": "step",
}
#: Flags: every spelling to the field it turns on.
FLAG_OPTIONS: dict[str, str] = {
    "--follow": "follow",
    "--force": "force",
    "--yes": "yes",
    "-h": "help",
    "--help": "help",
}


def parse_args(argv: list[str]) -> Args:
    """Parse `argv`. Raises `ValueError` for an unknown option or a missing value."""
    args = Args()
    it = iter(argv)
    for arg in it:
        name, equals, inline = arg.partition("=")
        if name in VALUE_OPTIONS and name.startswith("--") and equals:
            setattr(args, VALUE_OPTIONS[name], inline)
        elif arg in VALUE_OPTIONS:
            value = next(it, None)
            if value is None:
                raise ValueError(f"Option '{arg}' argument missing")
            setattr(args, VALUE_OPTIONS[arg], value)
        elif arg in FLAG_OPTIONS:
            setattr(args, FLAG_OPTIONS[arg], True)
        elif arg.startswith("-") and arg != "-":
            raise ValueError(f"Unknown option '{arg}'")
        else:
            args.positionals.append(arg)
    return args
