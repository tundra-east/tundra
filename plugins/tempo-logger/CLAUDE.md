# CLAUDE.md

This covers the `tempo-logger` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## What this is

A Claude Code plugin whose skill fills in a Tempo timesheet from natural language. The model reads `skills/tempo-logger/SKILL.md` and follows it; six Python scripts under `scripts/` do everything deterministic. Nothing is written to Tempo until the user has seen a rendered table and replied to it.

The split matters when changing anything: a rule that lives in SKILL.md is executed by a model and can be ignored, while a rule that lives in a script is mechanical. Moving a guarantee from one to the other changes how strong it is.

## Commands

Paths below are relative to this plugin's own directory, `plugins/tempo-logger/`.

Every script goes through the wrapper, never `python` directly. It forces `-X utf8` from outside the interpreter, which cannot be done from inside one — without it, printing a table raises `UnicodeEncodeError` part-way, and in `apply.py` that lands after the writes and before the result text.

```
cd skills/tempo-logger
scripts/tempo.sh <script-name> [args...]     # sh, macOS, Linux, Git Bash
scripts\tempo.cmd <script-name> [args...]    # cmd.exe, PowerShell
scripts/tempo.sh ../tests/tempo_logger_tests # offline, no token, touches no user state
scripts/tempo.sh ../tests/live_check         # needs a live token; reads only
scripts/tempo.sh ../tests/contract --check   # exit 1 when SKILL.md's generated blocks are stale
scripts/tempo.sh ../tests/contract --write   # regenerate those blocks from the scripts
```

`tempo_logger_tests` also runs in the pre-push hook and CI through the repository's `tools/run_tests.py`, so it must stay offline; anything needing a tenant goes in `live_check`, which nothing runs automatically.

## Testing has two layers and they cover disjoint halves

The tests check the scripts with no model in the loop. `skills/tempo-logger/evals/` checks whether a model reading SKILL.md does what it says; its README says how to run it. When changing a behavioural rule in SKILL.md, run the evals before believing the change landed; three runs per arm is the minimum that shows run-to-run variance. No eval harness ships with the plugin: the evals run through `skill-creator`, and a run that can write needs a step that isolates the Tempo data first and fills in the `<DATE>` and `<ITEM>` placeholders, which lives in the maintainer's working notes.

## Architecture

**The pipeline is catalogue → resolve → plan → confirm → apply**, and the middle step is the model's. `catalogue.py` returns the work items the caller has logged against before; matching the user's words onto one of them is judgement. `plan.py` reads the live dates, decides CREATE/UPDATE/DELETE, and renders the confirmation table. `apply.py` writes.

**`apply.py` is the only writer, enforced by the client.** `tempo.py`'s `MUTATING` set lists the endpoints that change data; `read()` and `read_post()` refuse them and `write()` refuses everything else. The line is drawn around endpoints, not the HTTP verb, because some `POST` endpoints only read.

**Work items come from the caller's own history because nothing else can supply them.** No Tempo endpoint turns a Jira key into the numeric issue id a write needs, and none says who a token belongs to. A work item with no history is unusable until one worklog exists for it, made in the Tempo UI. Any feature idea that starts "just look up the issue" dies here.

**SKILL.md is partly generated.** Nine blocks between `<!-- generated: name -->` markers are rendered from constants in the scripts by `tests/contract.py`; never hand-edit them.

**One renderer serves both the read and the confirmation.** `show.py` calls `plan.py`'s `_body` rather than drawing its own grid.

**Never report "nothing was written" without reading the result file.** `apply.py` appends each row's outcome to a per-run file under the state directory as it happens, so a run cut short is still answerable.

## Conventions

Its pictures are of Tempo, an external product whose Chinese interface cannot be checked from here, so all three of its READMEs show the same English ones, unlike the other plugins, whose translations show screenshots in their own language. The week views are HTML mock-ups with made-up data, and the token-settings picture is a crop of the real Tempo panel with nothing identifying left in it.

Nothing that holds for one tenant only goes into the plugin. Facts about Tempo's API are kept, tagged by how they were established, outside the plugin in the maintainer's working notes.

A rule in SKILL.md written as a bare negation inside a paragraph tends not to steer the model: one was violated in 3 of 18 runs, and its own paragraph with the reason it exists fixed it in 6 of 6. The reason is what works, not the emphasis.
