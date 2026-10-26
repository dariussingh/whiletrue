---
agent: respond
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read]
limits: { budget_usd: 1, turns: 8, wall: 3m }
outputs: { replies: CommentReplies }
---
Reviewers left comments on a pull request. The data is: {{input}}
For every comment in "comments", write one short, polite reply that answers it. Do not change any files.
Return a reply for each comment, using that comment's "id" as comment_id and classify it as one of question, change_request, nit, approval, out_of_scope.
