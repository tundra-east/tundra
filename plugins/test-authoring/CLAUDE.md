# CLAUDE.md

This covers the `test-authoring` plugin, and the skill evals of all four issue-to-pr plugins, most of which are this plugin's. The registry-level rules are in the repository root's `CLAUDE.md`.

## Subagents

Only `test-authoring` ships subagents, in a flat `agents/` dir with bare-name files (e.g. `add-unit-test-agent.md`). Frontmatter carries `name` (bare) and `description` — **and deliberately no `model:`**. At runtime Claude Code applies the plugin namespace, so they are spawned as `Agent(subagent_type="test-authoring:<agent-name>")`.

**Do not add `model:` to an agent's frontmatter.** It overrides the model the user chose for their session, and this is a publicly-installed plugin: picking the cost/quality trade-off is the consumer's call, not ours. The predecessor plugin (`acme/test-skills`) pinned `model: sonnet` on all 12 of its agents — that was an internal plugin, and it is not the precedent to follow here. Its absence is a decision, not an oversight; if cost guidance is wanted, put a measured figure in the README and let the consumer choose.

The architecture is **orchestrator → writer → verifier**:
- A skill body is the *orchestrator* (top-level caller). It resolves scope, delegates to writer agents, spawns read-only verifier agents, and runs the fix→verify loop.
- Writers generate/update tests. Verifiers are strictly read-only and report violations.
- **Subagents CAN spawn subagents, up to a depth limit.** This corrects a claim that stood here for months, marked unverifiable — "a subagent cannot spawn another subagent" — and was described as the thing the whole orchestrator/writer/verifier split rests on. It does not rest on it, because the prohibition does not exist. Verified 2026-08-28 two ways: Claude Code's own docs (`code.claude.com/docs/en/sub-agents.md`) state that nesting works recursively to a **default depth of 3 layers total** (main -> L1 -> L2 -> L3), that at the limit the `Agent` tool is simply **withheld**, and that the limit is set by `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (`"1"` disables nesting entirely); and directly, by running `review-plan-risk` inside a subagent, which spawned its own finder fan-out one level below and returned their merged results. A separate cap, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, defaults to 20 concurrent subagents. Neither variable is set on this machine.
- **So the writer/verifier split is a design choice, not a substrate limit — but keep it.** Writers and verifiers still emit structured output for the orchestrator to merge rather than spawning follow-ups themselves, because a verifier that can spawn its own helpers stops being the independent read the orchestrator needed. What changes is which arguments are available: "the runtime forbids it" is not one of them, and an argument that leans on it is unsupported.
- **What the depth limit does constrain is where a skill is invoked from.** A skill whose body spawns agents costs one layer at whatever depth its caller sits: invoked from the main session its own agents land at depth 1, and from inside a subagent they land at depth 2. That leaves room, but it is a budget, and the failure at the limit is silent — the `Agent` tool is withheld, so a fan-out designed for several blind readers quietly becomes one. Before making any skill spawn a wrapper subagent of its own by default, count the layers its deepest caller would need, and remember that a subagent has **no channel to prompt the human**: `review-plan-risk` run that way returned its findings but could not put its scope gate or its edge-case opt-in to anyone, so a whole tier of its output stayed unactioned.

## What ships vs what is generated

`setup-test-context` is an **optional accelerator**, not a prerequisite. Every test workflow works with or without it:
- **Rule books are never copied.** Every skill and agent reads `resources/templates/{rules,shared}/` from the plugin, in both cases below. A skill resolves that root once in Step -1 and passes it to every subagent (a subagent cannot resolve it itself); if it cannot be resolved, the skill stops rather than running unruled.
- **Without setup**: conventions are discovered from the nearest sibling tests at runtime.
- **With setup** (run once in the consumer repo): the cross-layer map is cached under `.claude/conventions/tests/`, and it is **one or two files, not always two** — `project-architecture.md` always, `common-verification-patterns.md` only when §1.4 found at least one qualifying pattern (layer-common or cross-layer-common). Its absence is the normal outcome on a repo with no such pattern, not a failed run. Nothing else is written per-repo — no rule books, no README, no manifest, and no backup: an overwrite has no undo, and the confirmation gate is the protection.

Distinction that matters when editing content: **rules are non-negotiable**; **conventions are descriptive patterns** that observed sibling tests can override. `resources/templates/rules/` = strict, and shipped; the per-repo `.claude/conventions/tests/` files setup generates = descriptive, and generated. There is no conventions *template* dir, and after the rule books stopped being copied there is no template *filling* either — the shipped files carry no placeholders and every convention file is written from analysis.

`common-*` files are role-lifecycle documents (one per actor: orchestrator/writer/update-writer/verifier); the other rule files are rule books. Put an actor's procedure in its `common-*` file and a constraint in the matching rule book — never both, or the rule drifts into two sources of truth.

**No versioning of the generated files, and no state between runs.** There is no manifest, no recorded hashes, and no per-file `schema_version`: `setup-test-context` knows only the fixed set of paths the current version writes. An existing file at one of those paths is rewritten with no undo — the confirmation gate labels it `OVERWRITE` beforehand, and that is the only protection; anything else under `.claude/{conventions,rules,shared}/tests/` is reported and left alone. Re-running is the refresh, so a template change reaches a repo when someone re-runs setup — nothing detects staleness for them.

## Citing a file from a rule book, or adding any name that crosses a role boundary

The rule used to be
"check the citation against the orchestrators' Step -1 reading lists". It has now bitten **four** times
under that wording, which is three more than a rule needs before you conclude the rule is not the problem.
Checking meant opening five skill files and reading a section of each, so the check was skipped every time.
The table below is that check, collapsed to one glance. It was extracted from the files on 2026-08-26 —
from each skill's Step -1 **Now** / **Never** lists and each agent's own rule-book references — not recalled.

| Party | Reads | Never opens |
|---|---|---|
| **Orchestrator** (all five skills) | `common-orchestrator-flow.md` (immediately), then `test-rules.md`, `fix-protocol.md`, `shared/scope-resolution.md`, `static/status-legend.md` lazily, and `sut-analysis.md` on one narrow path | `common-writer-instructions.md`, `common-verifier-checks.md`, `test-writer-rules.md`, plus `common-update-instructions.md` for the add flows |
| **Writer** (add and update) | `common-writer-instructions.md`, `test-writer-rules.md`, `test-rules.md`, `sut-analysis.md`, plus `common-update-instructions.md` for update writers | not declared, but it is handed no orchestrator or verifier rule book |
| **Verifier** (all four) | `common-verifier-checks.md`, its own per-type agent file, `fix-protocol.md`, `test-rules.md` | not declared, but it is handed no writer rule book |

**The one fact that explains all four bites: `test-rules.md` is the only file all three roles read.**
Orchestrator and writer otherwise share only `sut-analysis.md`, which the orchestrator opens on a single
narrow path and is therefore not a general declaration site. So a field, flag, or state that a writer
produces and an orchestrator must act on has **no shared home by default**, and declaring it once always
leaves one of them unable to see it.

Three rules follow, and the third is the one that keeps being missed:

1. A document on a reader's **Never** list cannot be cited to that reader as the definition of anything it
   must produce or act on. Inline what it needs and say which copy is authoritative.
2. A name that crosses a role boundary is declared **at both ends**, or in `test-rules.md` if every role
   needs it. Inlining twice is the cheap error here; declaring once is the expensive one.
3. **A flag is not reachable just because its definition is.** Check the party that must *set* it has a
   field for it in the prompt template it fills, and the party that must *branch* on it is told to look
   for it at the step where the branch happens. Three of the four bites were this: the definition was fine
   and the value had nowhere to travel — a spawn template with no slot for the flag, a handler sitting under
   a heading gated on a signal the producer was told not to send, and a carve-out reachable from one of the
   three callers that needed it.

The four bites, so the shape is recognisable: `bootstrap_authorisation` and `bootstrap_seed` (each cited
to a reader forbidden to open the definition), `seed_request` (no slot in the template that had to carry
it, so the whole feature would have silently never fired), and a U1 carve-out reachable from one add-flow
caller of three. All four passed a behavioural fixture before a review caught them.

**A mechanical gate for this was built and cut on 2026-09-21 — do not rebuild it.** It flagged every name present
in a rule book and absent from every `SKILL.md`, and it caught 3/3 of the historical bites at their defective commits.
On the live tree it reported 8 names and **all 8 were fine**. Three false-positive modes defeat it and no mechanical
rule separates them: an **upward** signal whose handler legitimately lives in a rule book (`stop_reason`); an enum
**value** rather than a field (`not_run` — a value needs no slot, its field does); and a field carried inside a
**named block** whose schema sits in another file (`previously_produced`). The one direction heuristic tried
misclassified `bootstrap_seed`, a real bite, as upward. Per the guard rule above — prefer cutting to a third
carve-out — it was cut. The audit itself is worth keeping: **zero downward-carriage defects** on the tree that day.

## Running skill evals

Each `evals/evals.json` pairs prompts with an `expected_output` and a list of `assertions`. Author these via the `skill-creator` skill, not a standalone test runner. Coverage is **7 of 14 skills** (`disconfirm-first` 1/3, `pr-lifecycle` 0/2, `test-authoring` 6/6, `issue-to-pr-pipeline` 0/3).

**Three different tools touch evals; do not collapse them into one word ("the runner"). Verified 2026-08-14 by reading the installed `skill-creator` at `~/.claude/plugins/cache/claude-plugins-official/skill-creator/<hash>/skills/skill-creator/`:**

1. **`evals.json` is skill-creator's OWN documented schema, and it has a working consumer.** `references/schemas.md:7` defines it at exactly our path (`evals/evals.json`, with `evals[].prompt` and `evals[].expected_output`), and `SKILL.md:221-234` is the workflow that eats it: run the skill per prompt, spawn a grader subagent (`agents/grader.md`) to judge each assertion into `grading.json`, then `python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>` for `benchmark.json` (pass_rate, time, tokens, mean ± stddev, **and the delta**), then an analyst pass via `agents/analyzer.md` that specifically hunts **"assertions that always pass regardless of skill (non-discriminating)"** — i.e. the discriminating-power check this file elsewhere says we lack. **So our format was never orphaned, and there is nothing to convert.**
2. **`scripts/run_eval.py` is TRIGGER-only and takes a different file.** Its own `--help` says "Run trigger evaluation for a skill description"; `--eval-set` points at a separate flat `[{"query":…, "should_trigger":…}]` set, not `evals.json`. This is the one the Windows `select.select` defect below belongs to. It is not "the runner" — it is one narrow tool. **And its method plants a decoy, which makes it the wrong instrument for comparing siblings.** Read off the source 2026-08-31: it never exercises the installed skill. It writes a throwaway command file at `.claude/commands/<skill>-skill-<uuid8>.md` carrying only the description, runs `claude -p <query> --output-format stream-json --include-partial-messages` from the discovered project root with `CLAUDECODE` stripped from the env, and counts the query as triggered when that decoy is selected. For measuring one description's pull in isolation that is the right shape. For asking whether two sibling descriptions still route apart — `disconfirm-first`'s three, say — the real installed skill competes against its own decoy twin, so a miss is unattributable. Use a plain `claude -p` run instead and read the selection off the transcript (`{"name":"Skill","input":{"skill":"<plugin>:<skill>"}}` — the higher-precision of the two detectors the trigger-harness plan measured). Side effect worth knowing before pointing it at a repo: it creates `.claude/commands/` in whatever project root it finds.
3. **`claude plugin eval` is a third, unrelated thing, and it is gated.** It reads `evals/**/case.yaml` or `prompt.md` + `graders/*.md`, offers `--ablation with-without`, `--runs`, `--threshold`, `--max-cost-usd`, and an HTML report that **publishes to claude.ai by default** (`--no-publish` keeps it local) — but every invocation including `init --bare` returns `` `plugin eval` is currently in early access ``, and no `case.yaml` exists on disk to copy the schema from. Do not author for it blind. **Re-probed 2026-08-31 — still gated, and the way it is still gated is a trap.** The `--help` output has grown a lot since (an `--allow-tools` operator grant, `--mocks`, `--judge-model`, `--case` / `--tag` filters, `--json`, and a `tool_used: Skill` grader described as a plugin-fired indicator under `--ablation with-without` — which is precisely the triggering check this file says we lack). That reads exactly like availability. It is not: `init --bare` in a scratch dir still returns the early-access line. Richer help was mistaken for a lifted gate once already this way, so probe the gate, never the help text.

**What actually blocks running the grading workflow is therefore NOT the format** — it is (a) the phantom-domain prompts in point 3 of the list below, (b) cost, since it executes the skill N times per prompt, and (c) it needs subagents (skill-creator's own Cowork note says the benchmark's baseline comparison "aren't meaningful without subagents"). **This paragraph replaces an earlier version of itself that claimed "no runner reads our format" — that was wrong: it came from collapsing the three tools above into one, and from a `find` that missed skill-creator because the install path contains a hash segment.**

**They have never been executed, and three things block running the trigger eval from this repo on Windows — verified 2026-08-06, do not re-discover them:**

1. **Wrong artifact.** `skill-creator`'s `scripts/run_eval.py` does not read `evals/evals.json`. It wants a separate flat trigger set, `[{"query": ..., "should_trigger": bool}]`. Ours is the qualitative set (prompt / expected_output / assertions), which belongs to the grader workflow instead. A trigger set can be derived: `should_trigger` = the eval has no `skill_not_triggered` assertion.
2. **The runner is Windows-incompatible.** Its read loop gates on `select.select([process.stdout], ...)`; on Windows `select` supports sockets only, so it raises `OSError` WinError 10093, unwinds to `return triggered` with `triggered` still `False`, and reports **every** query as not-triggered. Positives all fail, negatives all pass vacuously — a clean 19/19 + 21/21 split that looks like data and is not. Replace the streaming read with `communicate(timeout=...)` to fix it.
3. **The `test-authoring` prompts name a domain this repo lacks — but read this as an EXECUTION limit, not a prompt defect.** Four of those eval files name a C#/.NET billing domain (`BillingService.CreateInvoice`, `POST /invoices`, `InvoiceConsumer`); run *as executions* the model greps, finds nothing, and correctly declines. **Do not "fix" this by rewriting the prompts to this repo's own subject matter** — that trades unrunnable for *unrepresentative*, and these skills exist for consumer code repos, so a prompt about a Markdown plugin marketplace measures a case no user has. The earlier version of this entry offered that rewrite as a valid option; it is not.

   The census that settles it (2026-08-14, all 7 sets, **82** assertions, 17 kinds — an earlier version of this line said 78, which was simply wrong arithmetic): **`skill_triggered` ×23 + `skill_not_triggered` ×25 = 48 of 82 (59%) need no codebase at all** — they measure which skill Claude *selects*, where a nonexistent class is arguably better than a real one because it cannot pass for the wrong reason. Only ~26 (33%: gate / protocol / delegation / audit / table) need real execution, plus `risks_calibrated` ×4 needing a real artifact. So the phantom domain blocks a third of the value, not all of it.

   **But "needs no codebase" is not "is cheap" — costed 2026-08-14 and this is the binding constraint on the whole trigger tier.** Every probe pays a fixed floor before it measures anything: the issue-to-pr repository's CLAUDE.md alone was 33,536 bytes ≈ 8.3–8.8k tokens, plus the system prompt, every installed plugin's descriptions, and auto-memory. A full ~46-query sweep is ≈ **400k tokens** single-shot and ≈ **1.2M** at the 3-runs-per-query the comparable tool defaults to, and it must re-run after every description edit — against a benefit ceiling of ~940 tok/session (the six `test-authoring` skill descriptions; the other ~1,270 of its ~2,198 always-on total is *agent* descriptions, which triggering never touches). Break-even sits past 1,500 sessions. So measuring this tier is a **cost decision, not a free win** — see `.claude/plans/issue-to-pr/backlog/plan-trigger-harness.md`, which is not approved to build for exactly this reason. A small probe set (~8) is the only version whose arithmetic works.

What each tier actually needs: the 59% needs only **lookup-free prompt shapes** (prefer `"add unit tests for the files I just changed"` — Mode A, git-diff scoped — over `"add unit tests for BillingService.CreateInvoice"`, because caveat 4 below counts the first `tool_use` and a phantom target invites an exploratory `Glob` that reads as a miss). The 33% needs a code fixture repo, which is a **separate investment and not currently recommended** — five of six `test-authoring` skills have already executed in real consumer repos under the current architecture, which is stronger evidence of "does it work" than a synthetic fixture; a fixture buys *repeatability* (a regression gate), not first proof. **`review-plan-risk` is the exception and the natural pilot**: 0 of its 12 entries mention that domain, its subject *is* design artifacts, and this repo is made of them — but note it **auto-fixes what it reviews**, so point it at a few deliberately-flawed throwaway markdown artifacts (which also gives a known-answer case), never at live skill files across 12 × N runs.

A fourth, milder caveat: the runner counts the **first** `tool_use`, so any exploratory `Glob`/`Grep` before the skill is selected reads as not-triggered. A skill whose own first step is "locate the artifact" (e.g. `review-plan-risk`) will look like a miss even when it behaves correctly.
