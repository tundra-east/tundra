# CLAUDE.md

This covers the `issue-to-pr-pipeline` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## The plugins it builds on

| Plugin | Contains |
|---|---|
| `disconfirm-first` | Adversarial review at 3 altitudes: `review-issue-fact` (issue), `review-plan-risk` (plan/design), `review-code-risk` (implemented fix) |
| `pr-lifecycle` | PR lifecycle (Azure DevOps or GitHub): `open-pr`, `resolve-pr-comments` |
| `test-authoring` | Test authoring: 6 skills + 8 subagents (see below) |
| `issue-to-pr-pipeline` | `resolve-issue` (full issue→PR pipeline), `resolve-issue-dashboard`, `resolve-issue-learnings` |

Current versions live in each plugin's `.claude-plugin/plugin.json` — do not duplicate them here.

`issue-to-pr-pipeline` declares `dependencies` on `disconfirm-first`, `test-authoring`, and `pr-lifecycle` in its `plugin.json` — `resolve-issue` chains those component skills into one pipeline. When changing a component skill's inputs/outputs, check `resolve-issue` still calls it correctly.

## Changing the step registry

`plugins/issue-to-pr-pipeline/resources/resolve-issue-steps.json` is canonical, and the dashboard's index arithmetic runs over `parse_session.py`'s `STEPS` — which `_load_steps()` builds **from that JSON** whenever it is readable and non-empty (`:42-68`). `_DEFAULT_STEPS` is a fallback reached only on `OSError` / `ValueError` / an empty `steps` list, so on the normal path it is dead code. Keep it in the same order anyway — but know that order-parity is load-bearing only on the fallback path, and two other things bite first:
- **The fallback is silent.** `JSONDecodeError` subclasses `ValueError`, the `except` body is a bare `pass`, and nothing in the model or `/health` reports that the fallback is in use. So a *malformed* registry edit (a trailing comma, `"steps": []`) resurrects the step you just deleted, with no signal anywhere.
- **`STEPS` is evaluated once at import** (`:68`), and the dashboard is deliberately long-lived — `SKILL.md` says launch it once and reuse it, and `IDLE_SHUTDOWN_S` is 30 minutes after the last viewer disconnects. A registry edit therefore takes effect on **restart**, not on a browser reload, and a server left running for hours serves an import-time snapshot.

Removing a step id does **not** need a redirect in `resolve-issue/SKILL.md`'s State reconcile. One was carried for `b-code-review` / `b-commit-tests` from 2026-07-28 until 2026-09-17 and never fired: the handoff artifacts are per-machine and gitignored, so an old cursor has no route into anyone else's tree, and the staleness gate archives a foreign run anyway. Audited across 23 real runs, no live `state.md` carried either id. Say the removal in the commit and move on. (This entry previously claimed the index arithmetic ran over `_DEFAULT_STEPS`. It does not, and that wrong summary was later used to design a test fixture — a reminder that a paraphrase in this file gets trusted as ground truth, so correct it here rather than only in your head.)

## resolve-issue-dashboard

The only executable code lives under `plugins/issue-to-pr-pipeline/skills/resolve-issue-dashboard/`: `scripts/{serve_progress.py,parse_session.py}` — a read-only, localhost-only observer that tails Claude Code transcripts and `.claude/resolve/<ticket>/state.md` (plus the run's append-only `timings.md`, for per-step durations) to visualise a `resolve-issue` run (it never writes to transcripts or `.claude/resolve/`) — plus `tests/{parse_session,serve_progress}_tests.py`, stdlib deterministic self-checks of each script's pure logic. The server suite deliberately stops at the pure core — the `Hub` fan-out, `RunManager`'s selection and archived-run rules, `build`'s two load-bearing orderings, `plugin_version` and `tick`'s error envelope — and leaves the HTTP / SSE / browser surface untested, which is the same exclusion the parser suite's own docstring declares. Run the dashboard: `python serve_progress.py [--cwd PATH] [--ticket TICKET] [--port N] [--no-browser]`; run the checks: `python tests/parse_session_tests.py` and `python tests/serve_progress_tests.py`.

**Where the screen's data comes from.** Most of it is *not* `state.md`. That file supplies the ticket, the cursor, the branches, `plan-approved`, `pr-url`, `attention`, and `started`/`ended`; `timings.md` supplies the per-step durations and the `×N` re-entry count. Everything else is derived from the Claude Code **transcript** — `~/.claude/projects/<encoded-cwd>/<session>.jsonl` and that session's `<session>/subagents/agent-*.jsonl`: the agent-activity feed, the token and tool-call totals, the session id in the header, whether the run reads as running or parked, and (via each transcript's `cwd`) the cross-repo run list in the left panel. Worth knowing before debugging a blank panel — and it is why a fixture built from the two Markdown files alone renders an empty feed, zero counters, a single repo, and an idle status.

**Transcript record shape.** Claude Code does not document it, so this was read off `Collector._parse_line`; you need it whenever you synthesise a transcript, e.g. to rebuild the README screenshot after a pipeline change. One JSON object per line. Only `type` `"user"` and `"assistant"` are parsed — everything else is skipped silently. Tokens come from `message.usage.{input_tokens,output_tokens}`. Tool events come from `tool_use` blocks in `message.content[]`, and a tool's duration from pairing its `tool_use.id` with the later `tool_result.tool_use_id`. A main-session assistant record whose `stop_reason` is `"tool_use"` is what marks the session as still working; any other stop reads as yielded to the human, which is what flips the run from running to parked. The top-level `cwd` field is how a project dir is mapped back to its repo.
