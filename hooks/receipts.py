#!/usr/bin/env python3
"""receipts: check the agent's final message against what actually happened in the session.

Stop hook for Claude Code. Reads the final message and the session transcript, and looks for
claims without receipts:

  "all tests pass"            but tests never ran, the last run failed, or code changed after it
  "the build succeeds"        same for build, lint and type checks
  "47 tests pass"             but no run reported 47 passing
  "I ran `npm run e2e`"       but no such command ran
  "updated `src/api.ts`"      but nothing wrote to that file
  "deleted `old.py`"          but it's still there
  "these failures are pre-existing / flaky"   but nothing ran the tests without the agent's changes

It sends the agent back once to correct its reply. It never blocks twice in a row.

    python3 receipts.py --test
"""
import json
import os
import re
import sys

COMMANDS = {kind: re.compile(rx) for kind, rx in {
    "tests": r"\b(pytest|py\.test|python3? -m (pytest|unittest)|nose2|tox|nox|jest|vitest|mocha|ava|playwright test|cypress run"
             r"|go test|cargo (test|nextest)|rspec|rake (test|spec)|rails test|phpunit|pest|mix test|dotnet test"
             r"|mvnw? (test|verify)|gradlew? (test|check)|ctest|swift test|deno test|bun test"
             r"|(npm|pnpm|yarn|bun) (run )?test\S*|make (test|check)\b|just test"
             r"|node --test|\s--test\b|\brun[-_]?tests?\b|\btests?\.(sh|py)\b)",
    "build": r"\b((npm|pnpm|yarn|bun) (run )?build\S*|cargo (build|check)|go (build|vet)|make\b(?! (test|check))"
             r"|gradlew? (build|assemble)|mvnw? (package|compile|install)|dotnet build|next build|vite build|webpack|tsc\b"
             r"|swift build|xcodebuild)",
    "lint": r"\b(eslint|ruff|flake8|pylint|clippy|rubocop|golangci-lint|biome (check|lint)|(npm|pnpm|yarn|bun) (run )?lint\S*"
            r"|prettier (--check|-c)|stylelint|shellcheck|standardrb)",
    "types": r"\b(tsc|mypy|pyright|pytype|vue-tsc|(npm|pnpm|yarn|bun) (run )?(typecheck|type-check|types|tsc)\b|flow check)",
}.items()}

CLAIMS = {kind: re.compile(rx, re.I) for kind, rx in {
    "tests": r"\b(tests?|test suite|specs?|pytest|jest|vitest|rspec)\b[^.\n]{0,30}?\b(pass|passes|passing|passed|green"
             r"|succeed|succeeds|succeeded)\b|\ball green\b|\btests?\s*[:-]\s*✅|✅\s*(all\s+)?tests?\b",
    "build": r"\b(build|builds|compiles?|compilation|bundle)\b[^.\n]{0,30}?\b(succeed\w*|pass\w*|clean(ly)?|success\w*"
             r"|works|green|without errors)\b",
    "lint": r"\b(lint\w*|eslint|ruff|flake8|pylint|clippy|rubocop|golangci-lint|biome|prettier)\b[^.\n]{0,30}?"
            r"\b(pass\w*|clean|no (errors|warnings|issues|problems)|green)\b",
    "types": r"\b(type[- ]?check\w*|types|tsc|mypy|pyright|typescript)\b[^.\n]{0,30}?\b(pass\w*|clean|no (type )?errors|green)\b",
}.items()}
SATISFIED_BY = {"tests": {"tests"}, "build": {"build", "types"}, "lint": {"lint"}, "types": {"types", "build"}}
# sentences that hedge, plan or report a failure aren't claims of success
HEDGE = re.compile(r"\b(not|n't|never|no longer|unable|without running|skipped|fail\w*|should|would|will|once|if|"
                   r"to verify|please|make sure|expect|might|may|could|hopefully)\b|\?", re.I)

COUNT = re.compile(r"\b(?:all\s+)?(\d+)(?:\s*/\s*\d+|\s+of\s+\d+)?\s+(?:unit\s+|integration\s+|e2e\s+)?(?:tests?|specs?)\s+"
                   r"(?:are\s+|now\s+)?(?:pass|passing|passed|green)\b", re.I)
PASSED = re.compile(r"\b(\d+) (?:passed|passing|tests? passed)\b|\bTests:\s+(?:\d+ \w+, )*(\d+) passed", re.I)
FAILED_OUTPUT = re.compile(r"\b[1-9]\d* (failed|failing|failures?|errors?)\b|^(FAIL|FAILED|not ok)\b|--- FAIL:|error TS\d+"
                           r"|panicked at|^E\s{2,}\S|Traceback \(most recent call last\)", re.I | re.M)

RAN = re.compile(r"\b(?:ran|executed|re-?ran)\s+`([^`\n]{2,120})`", re.I)
CHANGED = re.compile(r"\b(updated|added|created|modified|changed|edited|wrote|fixed|refactored|rewrote|moved|renamed)\b"
                     r"[^\n.`]{0,40}`([\w@./-]+\.[A-Za-z0-9]{1,8})`", re.I)
DELETED = re.compile(r"\b(?:deleted|removed)\s+(?:the\s+)?(?:file\s+)?`([\w@./-]+\.[A-Za-z0-9]{1,8})`", re.I)
PREEXISTING = re.compile(r"pre-?existing|already (failing|broken|red)|(were|was) (already )?failing before|\bflaky\b|\bflakiness\b"
                         r"|unrelated to (my|our|these|this|the) (change|changes|work|edits?|pr)"
                         r"|not (caused|introduced) by (my|our|these|this|the)", re.I)
NEGATED = re.compile(r"\b(not|n't|never|nothing|no need|without)\b", re.I)
BASE_RUN = re.compile(r"\b(wasitme|was-it-me)\b|\bgit\s+(stash|worktree|checkout|switch)\b")

# heredoc bodies and quoted strings are data, not commands (`git commit -m "make tests pass"`), except `sh -c "..."`
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?(\n\s*\2[ \t]*(?=\n|$)|\Z)", re.S)
QUOTED = re.compile(r"(?<!-c )(\"(?:[^\"\\]|\\.)*\"|'[^']*')")

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_EDIT = re.compile(r"\bsed\s+(-\w+\s+)*-i|\bperl\s+-\w*i|\bgit\s+(apply|am|restore)\b|\bpatch\s+-"
                        r"|(?<![0-9&>])>{1,2}\s*['\"]?[\w./-]+\.(py|[cm]?[jt]sx?|go|rs|rb|java|kt|php|cs|swift|c|cc|cpp|h|vue|svelte)\b")

REASON = """receipts: parts of your final message don't match what happened in this session:

{list}

Fix your reply before you finish: run what you said you ran and report the real result, or say plainly what you did and didn't check. Don't report a result you haven't seen."""


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read_transcript(path):
    """Tool calls in order, each {name, input, ok, output}, plus the trailing assistant text."""
    calls, order, tail = {}, [], []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return [], ""
    with fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user"):
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("id") not in calls:
                    ev = {"name": b.get("name"), "input": b.get("input") or {}, "ok": None, "output": ""}
                    calls[b.get("id")] = ev
                    order.append(ev)
                    tail = []
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                    ev = calls[b["tool_use_id"]]
                    ev["output"] = text_of(b.get("content"))
                    ev["ok"] = not b.get("is_error") and not re.match(r"Exit code [1-9]", ev["output"])
                    tail = []
                elif b.get("type") == "text" and entry.get("type") == "assistant":
                    tail.append(b.get("text", ""))
    return order, "\n".join(tail)


def clean(cmd):
    return QUOTED.sub('""', HEREDOC.sub("<<heredoc", cmd or ""))


def sentences(message):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", message) if s.strip()]


def edited_path(ev):
    if ev["name"] in EDIT_TOOLS:
        return ev["input"].get("file_path") or ev["input"].get("notebook_path") or "a file"
    if ev["name"] == "Bash" and SHELL_EDIT.search(clean(ev["input"].get("command"))):
        return "files via the shell"
    return None


def touched(events, path):
    """Did anything in the session write to (or mention, in a shell command) this path?"""
    base = os.path.basename(path)
    for ev in events:
        target = edited_path(ev) if ev["name"] in EDIT_TOOLS else None
        if target and (target == path or target.endswith("/" + path) or (path.count("/") == 0 and os.path.basename(target) == base)):
            return True
        if ev["name"] == "Bash" and base in (ev["input"].get("command") or ""):
            return True
    return False


def runs_of(events, kinds):
    return [(i, ev) for i, ev in enumerate(events)
            if ev["name"] == "Bash" and any(COMMANDS[k].search(clean(ev["input"].get("command"))) for k in kinds)]


def failed(ev):
    return ev["ok"] is False or (ev["ok"] and FAILED_OUTPUT.search(ev["output"] or "") is not None)


def short(cmd):
    cmd = " ".join(cmd.split())
    return cmd if len(cmd) <= 80 else cmd[:77] + "..."


def audit(message, events, cwd):
    out = []
    last_edit = max([i for i, ev in enumerate(events) if edited_path(ev)], default=-1)
    lines = sentences(message)

    for kind, rx in CLAIMS.items():
        claim = next((s for s in lines if rx.search(s) and not HEDGE.search(s)), None)
        if not claim:
            continue
        runs = runs_of(events, SATISFIED_BY[kind])
        quoted = claim if len(claim) <= 100 else claim[:97] + "..."
        if not runs:
            out.append(f'You said "{quoted}", but no {kind} command ran in this session.')
            continue
        i, ev = runs[-1]
        cmd = short(ev["input"].get("command", ""))
        if ev["ok"] is None:
            out.append(f'You said "{quoted}", but the last {kind} run (`{cmd}`) never reported a result.')
        elif failed(ev):
            out.append(f'You said "{quoted}", but the last {kind} run failed: `{cmd}`.')
        elif i < last_edit:
            out.append(f'You said "{quoted}", but you changed {edited_path(events[last_edit])} after the last {kind} run (`{cmd}`).')
        elif kind == "tests":
            m = COUNT.search(claim)
            counts = [int(next(g for g in p if g)) for _, e in runs for p in PASSED.findall(e["output"] or "")]
            if m and counts and int(m.group(1)) not in counts + [sum(counts)]:
                out.append(f'You said "{quoted}", but no test run reported {m.group(1)} passing (saw: {", ".join(map(str, counts[-4:]))}).')

    commands = [" ".join((ev["input"].get("command") or "").split()) for ev in events if ev["name"] == "Bash"]
    stated = "\n".join(s for s in lines if not NEGATED.search(s))
    for m in RAN.finditer(stated):
        want = " ".join(m.group(1).split())
        if not any(want in c for c in commands):
            out.append(f"You said you ran `{want}`, but no command like that ran in this session.")
    seen = set()
    for m in CHANGED.finditer(stated):
        verb, path = m.group(1).lower(), m.group(2)
        if path not in seen and not touched(events, path):
            seen.add(path)
            out.append(f"You said you {verb} `{path}`, but nothing in this session wrote to it.")
    for m in DELETED.finditer(stated):
        if os.path.exists(os.path.join(cwd, m.group(1))):
            out.append(f"You said you deleted `{m.group(1)}`, but it's still there.")

    if any(PREEXISTING.search(s) for s in lines):
        tests = runs_of(events, {"tests"})
        rerun_differs = any(failed(a) != failed(b) and not any(edited_path(e) for e in events[i + 1:j])
                            for (i, a), (j, b) in zip(tests, tests[1:]))
        if not rerun_differs and not any(BASE_RUN.search(clean(ev["input"].get("command"))) for ev in events if ev["name"] == "Bash"):
            out.append("You said some failures are pre-existing, unrelated or flaky, but nothing in this session ran the tests "
                       "without your changes or showed them flipping between runs. Check (for example with was-it-me: "
                       "https://github.com/nuttakulsv/was-it-me) or say you haven't verified it.")
    return out


def handle(data):
    if data.get("hook_event_name") != "Stop" or data.get("stop_hook_active"):
        return
    events, tail = read_transcript(data.get("transcript_path"))
    message = data.get("last_assistant_message") or tail
    if not message:
        return
    found = audit(message, events, data.get("cwd") or os.getcwd())
    if found:
        print(json.dumps({"decision": "block", "reason": REASON.format(list="\n".join(f"- {f}" for f in found[:10]))}))


def test():
    import tempfile

    def bash(cmd, ok=True, output=""):
        return {"name": "Bash", "input": {"command": cmd}, "ok": ok, "output": output}

    def edit(path):
        return {"name": "Edit", "input": {"file_path": path}, "ok": True, "output": ""}

    def check(message, events, *want, cwd="/nonexistent"):
        found = audit(message, events, cwd)
        joined = "\n".join(found)
        if not want:
            assert not found, (message, found)
        for w in want:
            assert w in joined, (w, found)

    pytest_ok = bash("cd api && pytest -q", output="........ 8 passed in 0.4s")
    pytest_bad = bash("pytest -q", ok=False, output="Exit code 1\n2 failed, 6 passed")

    check("Done. All tests pass.", [edit("/r/api/x.py")], "no tests command ran")
    check("Done. All tests pass.", [edit("/r/api/x.py"), pytest_bad], "last tests run failed")
    check("Done. All tests pass.", [pytest_ok, edit("/r/api/x.py")], "after the last tests run")
    check("Done. All tests pass.", [edit("/r/api/x.py"), pytest_ok])
    check("Done. 8 tests pass.", [edit("/r/api/x.py"), pytest_ok])
    check("Done. All 47 tests pass.", [edit("/r/api/x.py"), pytest_ok], "no test run reported 47 passing")
    check("Tests pass.", [bash("npm test | tail -5", output="Tests:       1 failed, 9 passed, 10 total")], "last tests run failed")
    check("I haven't run the tests yet. They should pass once the fixture is updated.", [edit("/r/x.py")])
    check("The tests are failing on the CI runner.", [])
    check("The build succeeds and lint is clean.", [edit("/r/a.ts"), bash("npm run build", output="built in 2s")],
          "no lint command ran")
    check("TypeScript compiles with no type errors.", [edit("/r/a.ts"), bash("npx tsc --noEmit", output="")])

    check("I ran `npm run e2e` and it was fine.", [bash("npm test", output="5 passed")], "you ran `npm run e2e`")
    check("I ran `npm test`, everything is fine.", [bash("npm test", output="5 passed")])
    check("I updated `src/api.ts` and added a test in `tests/test_api.py`.",
          [edit("/r/src/api.ts")], "you added `tests/test_api.py`")
    check("Nothing changed in `package.json`. I didn't run `npm audit`.", [])
    check("All 8/8 tests pass.", [edit("/r/api/x.py"), pytest_ok])
    check("The build succeeds.", [bash("git commit -m 'make it build'"), bash("python3 - <<'EOF'\nprint('npm run build')\nEOF")],
          "no build command ran")
    check("Tests pass.", [bash("bash -c 'cd api && npm test'", output="3 passed")])
    check("Tests pass.", [bash("./node_modules/.bin/vitest run", output="3 passed"), bash("cat > notes.py <<'EOF'\nx = 1\nEOF")],
          "after the last tests run")
    check("Created `scripts/seed.sh`.", [bash("cat > scripts/seed.sh <<'EOF'\necho hi\nEOF")])
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "old.py"), "w").close()
        check("I deleted `old.py`.", [], "it's still there", cwd=d)
        check("I deleted `gone.py`.", [], cwd=d)

    check("The remaining 2 failures are pre-existing and unrelated to my changes.", [pytest_bad], "pre-existing")
    check("The remaining failures are pre-existing.", [pytest_bad, bash("python3 wasitme.py -- pytest -q", ok=False)])
    check("test_upload is flaky.", [pytest_bad, pytest_ok])
    check("test_upload is flaky.", [pytest_bad, edit("/r/x.py"), pytest_ok], "pre-existing, unrelated or flaky")

    # transcript parsing, with the duplicate request blobs real transcripts contain
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.jsonl")
        use = {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "pytest"}}
        res = {"type": "tool_result", "tool_use_id": "a", "content": "Exit code 1\n1 failed", "is_error": True}
        with open(path, "w") as fh:
            for e in [{"type": "assistant", "message": {"content": [use]}}, {"type": "api-request-blob", "message": {"content": [use]}},
                      {"type": "user", "message": {"content": [res]}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": "All tests pass now."}]}}]:
                fh.write(json.dumps(e) + "\n")
            fh.write("not json\n")
        events, tail = read_transcript(path)
        assert len(events) == 1 and events[0]["ok"] is False and tail == "All tests pass now.", (events, tail)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            handle({"hook_event_name": "Stop", "transcript_path": path, "cwd": d})
        assert '"decision": "block"' in buf.getvalue() and "last tests run failed" in buf.getvalue(), buf.getvalue()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            handle({"hook_event_name": "Stop", "transcript_path": path, "cwd": d, "stop_hook_active": True})
        assert buf.getvalue() == "", "never block twice in a row"
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        test()
    else:
        try:
            handle(json.load(sys.stdin))
        except Exception as e:  # a broken hook must never break the session
            print(f"receipts: {e}", file=sys.stderr)
