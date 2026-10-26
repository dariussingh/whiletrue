# Reference pipeline: the issue loop

Takes every open issue in a milestone from open to merged, four at a time:

1. Each issue is an item (blocked-by relations become dependencies; `spec: path#L1-L9` lines in the issue body become `item.spec_refs`) with its own git worktree and branch `wt/<issue>`.
2. `implement` (an agent) does the work; the `gate` (tests) must pass, with retries that feed the failure back.
3. A PR is opened (once), and the item parks at a **review gate**. Every comment on the PR gets a `[whiletrue]` reply from the `respond` agent, in its own thread. A rejection (`whiletrue reject <issue> --note "..."`, or "changes requested" on GitHub) sends your note to the `fix` agent and asks for review again.
4. After approval the branch is rebased onto `main`, a final **merge gate** waits, and the PR is merged under the lock `repo:main`.

## Try it for real

```
# edit whiletrue.yaml: repo, milestone, and the gate command for your project
gh auth login                       # whiletrue talks to GitHub through the gh CLI
whiletrue validate && whiletrue plan
whiletrue run                       # exits 2 when everything is waiting for you
whiletrue status                    # what is waiting
whiletrue approve 12                # or: whiletrue reject 12 --note "add a test"
whiletrue resume                    # continue; run it again after each decision
```

Killed or crashed? `whiletrue resume` recovers: finished steps are replayed, nothing is repeated.

## Try it without GitHub

`WHILETRUE_FAKE_GITHUB=/tmp/gh.json` swaps GitHub for a JSON file (create it with `whiletrue.github.FileBackedGitHub`, set `issues`, call `save()`; play the reviewer with `add_comment` and `review`). PRs are still pushed to your `origin`, so point that at a scratch bare repository.
