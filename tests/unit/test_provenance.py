import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_provenance", ROOT / "scripts" / "check_provenance.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HEADER = (
    "# Copyright 2026 Google LLC\n#\n# Licensed under the Apache License, Version 2.0 (the)\n"
    "#\n# Modified by the whiletrue authors: ported.\n"
)


def make_repo(tmp_path: Path, *, listed: bool = True) -> Path:
    (tmp_path / "third_party" / "harness2").mkdir(parents=True)
    (tmp_path / "third_party" / "harness2" / "LICENSE").write_text("Apache")
    row = "| `harness2/core/x.py` | `whiletrue/rsi/harness2/x.py` | role |\n" if listed else ""
    (tmp_path / "third_party" / "harness2" / "PROVENANCE.md").write_text(
        "| Upstream path | whiletrue path | Role |\n| --- | --- | --- |\n" + row
    )
    (tmp_path / "whiletrue" / "rsi" / "harness2").mkdir(parents=True)
    (tmp_path / "whiletrue" / "rsi" / "harness2" / "x.py").write_text(HEADER + "x = 1\n")
    return tmp_path


def test_the_real_repository_passes() -> None:
    assert load_script().problems(ROOT) == []
    assert load_script().main() == 0


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    assert load_script().problems(make_repo(tmp_path)) == []


def test_original_files_need_no_entry(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "whiletrue/rsi/harness2/own.py").write_text("y = 2\n")
    assert load_script().problems(root) == []


def test_each_kind_of_drift_is_reported(tmp_path: Path) -> None:
    script = load_script()
    root = make_repo(tmp_path, listed=False)
    assert any("not listed" in p for p in script.problems(root))
    root2 = make_repo(tmp_path / "b")
    (root2 / "whiletrue/rsi/harness2/x.py").write_text("# Copyright 2026 Google LLC\nx = 1\n")
    found = script.problems(root2)
    assert any("Apache notice is missing" in p for p in found)
    assert any("modification notice" in p for p in found)
    root3 = make_repo(tmp_path / "c")
    (root3 / "whiletrue/rsi/harness2/x.py").write_text("x = 1\n")
    assert any("no upstream header" in p for p in script.problems(root3))
    root4 = make_repo(tmp_path / "d")
    (root4 / "whiletrue/rsi/harness2/x.py").unlink()
    assert any("does not exist" in p for p in script.problems(root4))
    (root4 / "third_party/harness2/LICENSE").unlink()
    assert any("LICENSE is missing" in p for p in script.problems(root4))
    (root4 / "third_party/harness2/PROVENANCE.md").unlink()
    assert script.problems(root4) == ["missing third_party/harness2/PROVENANCE.md"]


def test_main_reports_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()
    monkeypatch.setattr(script, "ROOT", make_repo(tmp_path, listed=False))
    assert script.main() == 1
    assert "provenance:" in capsys.readouterr().out
