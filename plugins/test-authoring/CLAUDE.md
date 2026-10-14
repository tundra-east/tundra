# CLAUDE.md

This covers the `test-authoring` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## Subagents

The 8 subagents sit in a flat `agents/` dir as bare-name files whose frontmatter carries `name` and `description`, and Claude Code applies the plugin namespace, so they are spawned as `Agent(subagent_type="test-authoring:<agent-name>")`.

The architecture is **orchestrator → writer → verifier**. A skill body orchestrates: it resolves scope, delegates to writers, spawns read-only verifiers and runs the fix→verify loop. Writers and verifiers return structured output for it to merge rather than spawning follow-ups, because a verifier that spawns its own helpers stops being an independent read. That is a design choice, not a runtime limit — subagents nest to a default depth of 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`) — so do not argue from "the runtime forbids it".

**The depth is a budget.** A skill whose body spawns agents costs one layer wherever its caller sits, and at the limit the `Agent` tool is withheld without a word, so a fan-out of several blind readers quietly becomes one. Before making a skill spawn a wrapper subagent by default, count the layers its deepest caller needs. A subagent also cannot prompt the human, so a skill run inside one leaves its gates unanswered.

## What ships vs what is generated

The rule books under `resources/templates/{rules,shared}/` ship with the plugin and are read from there, never copied. Each skill that spawns subagents resolves that root once in its Step -1 and passes it to every subagent, which cannot resolve it itself; when it cannot be resolved, the skill degrades loudly and passes a `fallback_rules` block instead. `setup-test-context` is optional, and its `SKILL.md` owns what it writes and how it overwrites.

**Rules are non-negotiable; conventions are descriptive**, and observed sibling tests override them. `resources/templates/rules/` is shipped and strict, and the `.claude/conventions/tests/` files setup generates are descriptive and have no template. `common-*` files are role-lifecycle documents (orchestrator, writer, update-writer, verifier) and the other rule files are rule books: put an actor's procedure in its `common-*` file and a constraint in the matching rule book, never both.

## Citing a file from a rule book, or adding any name that crosses a role boundary

Check the citation against this table, extracted on 2026-08-26 from each skill's Step -1 **Now** / **Never** lists and each agent's rule-book references.

| Party | Reads | Never opens |
|---|---|---|
| **Orchestrator** (all five skills) | `common-orchestrator-flow.md` (immediately), then `test-rules.md`, `fix-protocol.md`, `shared/scope-resolution.md`, `static/status-legend.md` lazily, and `sut-analysis.md` on one narrow path | `common-writer-instructions.md`, `common-verifier-checks.md`, `test-writer-rules.md`, plus `common-update-instructions.md` for the add flows |
| **Writer** (add and update) | `common-writer-instructions.md`, `test-writer-rules.md`, `test-rules.md`, `sut-analysis.md`, plus `common-update-instructions.md` for update writers | not declared, but it is handed no orchestrator or verifier rule book |
| **Verifier** (all four) | `common-verifier-checks.md`, its own per-type agent file, `fix-protocol.md`, `test-rules.md` | not declared, but it is handed no writer rule book |

**`test-rules.md` is the only file all three roles read**, so a field, flag or state that a writer produces and an orchestrator must act on has no shared home by default. Three rules follow, and the third is the one that keeps being missed:

1. A document on a reader's **Never** list cannot be cited to that reader as the definition of anything it must produce or act on. Inline what it needs and say which copy is authoritative.
2. A name that crosses a role boundary is declared **at both ends**, or in `test-rules.md` if every role needs it.
3. **A flag is not reachable just because its definition is.** Check that the party that must *set* it has a field for it in the prompt template it fills, and that the party that must *branch* on it is told to look for it at the step where the branch happens.

Four defects of this shape passed a behavioural fixture before a review caught them: `bootstrap_authorisation` and `bootstrap_seed`, each cited to a reader forbidden to open the definition, `seed_request`, with no slot in the template that had to carry it, and a U1 carve-out reachable from one add-flow caller of three. A mechanical gate for this was built and cut on 2026-09-21; do not rebuild it, since all 8 of its hits on the live tree were false, from three false-positive modes no mechanical rule separates.
