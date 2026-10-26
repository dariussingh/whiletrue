from pathlib import Path

from whiletrue.github import FileBackedGitHub, GhIssue


async def test_state_survives_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "gh.json"
    first = FileBackedGitHub(path)
    first.issues = [GhIssue(1, "A", "body", "u", ["x"], blocked_by=[])]
    first.save()
    pr = await first.create_pr("o/r", head="wt/1", base="main", title="T", body="")
    comment = first.add_comment(pr.number, "why?")
    first.save()

    second = FileBackedGitHub(path)
    assert [
        i.title for i in await second.list_issues("o/r", milestone=None, state="open", labels=[])
    ] == ["A"]
    found = await second.find_pr("o/r", "wt/1")
    assert found is not None and found.number == pr.number
    assert [c.body for c in await second.list_comments("o/r", pr.number)] == ["why?"]
    await second.reply("o/r", pr.number, comment, "[whiletrue] because")
    await second.merge_pr("o/r", pr.number, "squash")

    third = FileBackedGitHub(path)
    assert third.replies == [(pr.number, comment.id, "[whiletrue] because")]
    assert third.merged == [(pr.number, "squash")]
    assert (await third.get_pr("o/r", pr.number)).state == "merged"
    assert third.created == [pr.number]


async def test_changes_made_by_another_process_are_seen(tmp_path: Path) -> None:
    path = tmp_path / "gh.json"
    mine = FileBackedGitHub(path)
    pr = await mine.create_pr("o/r", head="b", base="main", title="T", body="")
    theirs = FileBackedGitHub(path)
    theirs.review(pr.number, "approved")
    theirs.save()
    assert (await mine.get_pr("o/r", pr.number)).review == "approved"


def test_a_missing_file_starts_empty(tmp_path: Path) -> None:
    gh = FileBackedGitHub(tmp_path / "none" / "gh.json")
    assert gh.issues == [] and gh.prs == {}
    gh.save()
    assert (tmp_path / "none" / "gh.json").exists()
