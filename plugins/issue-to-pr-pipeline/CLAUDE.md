# CLAUDE.md

This covers the `issue-to-pr-pipeline` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## The plugins it builds on

`plugin.json` declares `dependencies` on `disconfirm-first` (`review-issue-fact`, `review-plan-risk`, `review-code-risk`), `test-authoring` and `pr-lifecycle` (`open-pr`, `resolve-pr-comments`), and `resolve-issue` chains their skills into one pipeline. When you change a component skill's inputs or outputs, check that `resolve-issue` still calls it correctly.

## Changing the step registry

`resources/resolve-issue-steps.json` is canonical. `parse_session.py` builds `STEPS` from it at import and falls back to its embedded `_DEFAULT_STEPS` only when the file cannot be read or holds no steps, so keep the two in the same order. The fallback is silent — a bare `pass`, reported nowhere — so a registry left with `"steps": []` brings back every step you deleted; the `json` gate catches only a registry that does not parse.

Removing a step id needs no redirect in `resolve-issue`'s State reconcile: a cursor lives in one machine's gitignored `.claude/resolve/`, and the staleness gate archives a foreign run anyway. One was carried for two months and never fired across 23 audited runs.

## resolve-issue-dashboard

The plugin's only executable code is `skills/resolve-issue-dashboard/scripts/`, a read-only localhost observer, and its suites under `tests/` run with `python tools/run_tests.py`.

**Where the screen's data comes from.** `state.md` supplies the ticket, the cursor, the branches, `plan-approved`, `pr-url`, `attention` and `started`/`ended`, and `timings.md` the per-step durations and the `×N` count. Everything else comes from the transcript — `~/.claude/projects/<encoded-cwd>/<session>.jsonl` and its `subagents/agent-*.jsonl`: the activity feed, the token and tool-call totals, the session id, running versus parked, and the cross-repo run list. So a fixture built from the two Markdown files alone renders an empty feed, zero counters, one repo and an idle status.

**Transcript record shape**, read off `Collector._parse_line`, for synthesising a transcript such as the README screenshot's. One JSON object per line, and only `type` `"user"` and `"assistant"` are read; `timestamp` places a record inside the run's window. Usage comes from `message.usage` — `input_tokens`, `output_tokens` and the two `cache_*_input_tokens` — counted once per `(file, message.id)`. A `tool_use` block in `message.content[]` pairs with the later `tool_result.tool_use_id` for status and duration. A main-session assistant record whose `stop_reason` is `"tool_use"` keeps the run running, and any other stop parks it. The top-level `cwd` maps a project dir back to its repo.
