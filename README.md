# receipts

[ภาษาไทย](README.th.md) · From the maker of [honest-tests](https://github.com/nuttakulsv/honest-tests) and [leave-no-trace](https://github.com/nuttakulsv/leave-no-trace)

"✅ All tests pass." Did they, though? Did it run them after the last edit? Did it read the output, or just see `| tail` exit 0?

receipts is a Claude Code hook that fact-checks the agent's final message against what actually happened in the session. When the summary claims something the session can't back up, the agent gets sent back to fix its reply before you read it.

## What it checks

| The agent says | receipts checks the session for |
|---|---|
| "All tests pass" | a test command that ran **after the last edit** and didn't fail |
| "The build succeeds", "lint is clean", "no type errors" | the same, for build, lint and type-check commands |
| "All 47 tests pass" | a run that actually reported 47 passing |
| "I ran \`npm run e2e\`" | that command, or one containing it |
| "Updated \`src/api.ts\`" | an edit to that file, or a shell command that touched it |
| "Deleted \`old.py\`" | that the file is really gone |
| "These failures are pre-existing / flaky / unrelated to my changes" | a run without the agent's changes (`git stash`, a worktree, [was-it-me](https://github.com/nuttakulsv/was-it-me)), or the same test passing and failing with no edit in between |

A failed run counts as failed even when the exit code is 0, if the output says so: `2 failed`, `FAIL`, `error TS2345`, a Python traceback. So `npm test | tail -20` doesn't get a free pass.

Here's what the agent sees when it gets caught:

```
receipts: parts of your final message don't match what happened in this session:

- You said "All tests pass now.", but you changed /repo/src/cart.ts after the last tests run (`npm test`).
- You said you updated `docs/api.md`, but nothing in this session wrote to it.

Fix your reply before you finish: run what you said you ran and report the real result,
or say plainly what you did and didn't check. Don't report a result you haven't seen.
```

Usually it reruns the tests and writes an honest summary. Sometimes the tests fail, and that's exactly what you wanted to know.

## What it won't flag

- Hedged or planned statements: "should pass once the fixture is updated", "I didn't run the tests", "make sure the build passes".
- Claims about things that aren't commands or files.
- The same stop twice. If the agent has already been sent back once, the next attempt goes through, so you never get stuck in a loop.

## Install

Claude Code:

```
/plugin marketplace add nuttakulsv/receipts
/plugin install receipts@receipts
```

The skill on its own works with Codex, Cursor and anything else that reads SKILL.md. It's a short set of rules for writing a final message that only reports what was seen:

```
npx skills add nuttakulsv/receipts
```

## How it works

When the agent stops, the hook reads the session transcript that Claude Code already keeps (every command, its output, whether it failed, every file edit) and the final message. It matches claims in the message with regular expressions and checks each against the transcript. Python standard library only, no network, nothing leaves your machine.

## Limits

- Claims are found by pattern, in English. Unusual phrasing gets through.
- File edits made by a script the agent ran (say, a Python one-liner that rewrites a file) don't count as edits. Edits through the Edit/Write tools, `sed -i`, `git apply` and `> file.py` redirects do.
- Test commands are recognized by name: pytest, jest, vitest, go test, cargo test, rspec, `npm test`, `make test` and about thirty more. A custom `./ci.sh` isn't. Open an issue with yours.
- It checks the claim, not the work. It can tell you the tests ran after the last edit, not that they test the right thing. For that, see [honest-tests](https://github.com/nuttakulsv/honest-tests).

## Tests

```
python3 hooks/receipts.py --test
```

## License

MIT
