from whiletrue.github import blocked_by_in_body, parse_spec_refs, repo_from_remote


def test_finds_spec_links_in_issue_bodies() -> None:
    body = """Implement the API.

spec: specs/m3/api.md#L40-L88
- Spec: docs/errors.md, docs/auth.md#L3
> specs: `docs/quoted.md`
not a spec line: nope
spec: https
"""
    assert parse_spec_refs(body) == [
        "specs/m3/api.md#L40-L88",
        "docs/errors.md",
        "docs/auth.md#L3",
        "docs/quoted.md",
        "https",
    ]
    assert parse_spec_refs(None) == [] and parse_spec_refs("nothing here") == []


def test_ignores_malformed_ranges_and_duplicates() -> None:
    assert parse_spec_refs("spec: a.md#bad a.md a.md") == ["a.md"]


def test_blocked_by_lines() -> None:
    assert blocked_by_in_body("Blocked by #12, #7 and #3\nblocked by: #12") == [12, 7, 3]
    assert blocked_by_in_body("no deps") == [] and blocked_by_in_body(None) == []


def test_repo_names_from_remotes() -> None:
    assert repo_from_remote("git@github.com:dariussingh/whiletrue.git") == "dariussingh/whiletrue"
    assert repo_from_remote("https://github.com/o/r\n") == "o/r"
    assert repo_from_remote("https://gitlab.com/o/r.git") is None
