---
name: receipts
description: Use before you write the final message of a task, or any message that reports results ("tests pass", "build is green", "I updated X", "these failures are pre-existing"). Every result you report must come from something you actually ran or saw in this session, after your last change.
---

# Receipts

The user reads your last message and decides whether to trust the work. If it says "all tests pass", they won't run them. So everything in it has to be something you saw, not something you expect.

## Rules for the final message

- **Only report results you saw.** "Tests pass" means you ran them after your last edit and the output said they passed. If you didn't run them, say "I didn't run the tests".
- **Re-run after you change code.** A green run from before your last edit doesn't count.
- **Read the output, not just the exit code.** `npm test | tail` exits 0 even when tests fail. Look for "failed", "error", tracebacks.
- **Use the real numbers.** If the run said `45 passed, 2 skipped`, say that, not "all 47 pass".
- **Name what you checked and how.** "Ran `pytest tests/api` (12 passed). Didn't run the e2e suite." is better than "Everything works."
- **Only list files you changed.** Check with `git status` or `git diff --stat` before you write the list.
- **"Pre-existing" and "flaky" are claims too.** Before you say a failure isn't yours, run the same test without your changes (a clean worktree of the base commit, or `was-it-me`), or rerun it and see it flip. Otherwise say "I haven't checked whether this fails without my changes."
- **Say what's left.** Anything you skipped, couldn't run, or aren't sure about goes in the message, plainly.

## A good final message

```
Fixed the rounding in calculate_tax (src/tax.py).

Checked:
- pytest tests/test_tax.py: 14 passed
- full suite: 212 passed, 1 failed (test_export_pdf, fails the same way on main: ran it in a clean worktree)

Not checked: the PDF export itself, I don't have the fonts installed here.
```
