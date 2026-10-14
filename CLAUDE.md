# CLAUDE.md

This file covers the repository as a whole. A plugin under `plugins/` may carry its own `CLAUDE.md` next to its `plugin.json`; where it does, that one is the authority on the plugin's internals.

## What this is

`tundra` is a public Claude Code plugin marketplace. `.claude-plugin/marketplace.json` at the root lists what is on offer, and every entry's `source` is a relative path into `plugins/`.

**Nothing specific to one organisation goes into a plugin here** — no tenant, no internal service, no internal process — because the repository is public. A plugin that needs any of that belongs in a marketplace of its own from the start. Moving it later means every user has to uninstall and re-add, because a plugin's identity is `<plugin>@<marketplace>` and nothing migrates across marketplaces.

## Layout

```
.claude-plugin/marketplace.json     the catalogue, and the only thing /plugin marketplace add reads
plugins/<name>/                     a plugin, with everything it needs inside its own folder
tools/pre_publish_check.py          the publish gates; tools/README.md says what each one catches
tools/run_tests.py                  every *_tests.py under a tests/ directory
.githooks/pre-push                  runs both before a push
.github/workflows/checks.yml        runs both again after one, on Linux and Windows
README.md                           the English README, translated by README.zh-TW.md and README.zh-CN.md
docs/<plugin>/                      the root READMEs' pictures; docs/issue-to-pr/ also holds the issue-to-pr README and its translations
docs/banner/                        the root README's banner
docs/translation-glossary.md        the vocabulary every translation follows
LICENSE                             covers everything under the root
```

**A plugin's folder is the unit that gets copied, so everything a plugin needs lives inside it.** A copied plugin cannot reach a `../shared-utils` outside its own directory, and organisation sync packages each plugin folder on its own.

Test suites live beside their code and ship with the plugin on purpose: the plugin format has no exclusion mechanism and nothing reads them at runtime. Revisit only if one appears.

A plugin is registered in exactly one marketplace, and for a plugin here that marketplace is this one. Two listings would give the same plugin two identities, `<plugin>@tundra` and `<plugin>@<theirs>`, with nothing to stop a user installing both.

## Versions live in plugin.json, and only there

Every plugin declares `version` in its own `.claude-plugin/plugin.json`, and a marketplace entry never does: Claude Code silently prefers `plugin.json`, so a second copy only masks a stale one, and with neither the version shown is a commit SHA. A push that changes a plugin without changing its version delivers nothing; the version gate catches that.

Any install instruction uses the full HTTPS URL `https://github.com/tundra-east/tundra.git`, because the `owner/repo` shorthand clones over SSH.

## Skills and agents

A skill's `description` is how Claude decides to trigger it: what it does, "Use when…" phrases, and a "Do NOT trigger for…" list routing look-alike requests to siblings. Keep that structure.

The `descriptions` gate holds every skill and agent description to the Agent Skills specification's 1,024 characters (Claude Code itself truncates at 1,536). Keep a new one at or under about 1,000; when it is over, cut mechanism detail and quotes that repeat the trigger list, never the "Do NOT trigger for…" routing.

**No `model:` in an agent's or skill's frontmatter.** It overrides the model the user chose for their session, and the cost/quality trade-off is the consumer's call. If cost guidance is wanted, put a measured figure in the README.

**Ship the behaviour, not the observation.** These plugins run in repositories whose conventions we cannot see, so where a value can be derived from the consumer's own repository (commit subject shape, trailers, default branch, pull request format), ship the derivation, with an observed value at most as the named fallback when there is no history. The tell is a literal token nobody would invent, or a justification naming an authority the consumer is not under ("team-wide").

**Adding a guard to a skill or agent:** its inputs must be something a party is obliged to produce (check the producer's contract); never take its trigger, file list or baseline from the party it distrusts; grep what already covers the state first; prefer cutting the guard to a third carve-out; and before writing it, a fixture shows the state occurs and the guard fires.

A check is trusted only after a known-answer case made it fail, drawn from every direction of the input and run before reviewing it, and after deleting any one check turns its suite red; until then say "not validated" rather than quoting a result. A measurement justifies a concern, not a line in a skill: a line ships only if removing it changes a fixture's outcome, and giving the executor the data beats telling it the conclusion.

## Making changes

- **Editing a skill or agent**: edit the `.md`, then run `/reload-plugins` in a consumer session to pick it up. There is nothing to build.
- **Releasing**: bump only the plugins whose content changed, in their `plugin.json`; the push to `main` is the release.
- **Loading the plugins from a clone**: `claude --plugin-dir ./plugins/<name>` loads one plugin as `<name>@inline`, and `claude --plugin-dir ./plugins` loads them all. Never `/plugin marketplace add` the clone: it is a marketplace named `tundra`, so adding it replaces the `tundra` you installed from without warning, and its plugins then fail with `Plugin <name> not found in marketplace tundra`.
- **The gates**: enable the hook once per clone with `git config core.hooksPath .githooks`. It runs `python tools/pre_publish_check.py` and then `python tools/run_tests.py`, and it is the only thing that stops a push; `checks.yml` reports after it on Linux and Windows. The version gate diffs the published ref against `HEAD`, so it sees only committed work. What each gate catches is in `tools/README.md`; anything none of them covers is prose on purpose.
- **A plugin hook** goes in `hooks/hooks.json` at the plugin root, not inside `.claude-plugin/`, or in a file that `plugin.json`'s `hooks` field names; a hook file in any other place silently never loads.
- **Skill evals**: a skill's `evals/evals.json` runs through `skill-creator`'s grader flow, with and without the skill, at least three runs per arm on the rule that changed; nothing runs them in CI. The traps of the eval tools are in the maintainer's working notes.

## Translations

Only the root README and the pages a reader reaches from it are translated (the pages it links to, and from `docs/issue-to-pr/README.md` the resolve-issue and test-authoring READMEs), into `README.zh-TW.md` and `README.zh-CN.md`, because readers stop there; an edit to an English one makes both stale. The `translations` gate finds them, checks their recorded digest and structure, and prints the digest to record.

A translated heading that a link points at carries the English `<a id>`, and a link into a translated README goes to its same-language version. Mermaid labels, and the description column of a block fenced as ```tree, are translated in place; every other code block stays English. A ```tree block is compared by its names alone, so keep a description on the same line as its entry.

Whether a translation means what the English means is not mechanical. Translate from the skills' own `SKILL.md` and README rather than word by word, follow `docs/translation-glossary.md`, re-translate only the paragraphs that changed, and then have an independent read-only subagent compare each translation against the English for meaning shifts, glossary violations and the other region's vocabulary. The digest proves only that someone touched the translation after the English changed.

A translation shows screenshots of the page in its own language, with the language before the extension, such as `session-dark.zh-TW.jpg`, beside the English ones. The root README's thumbnails are the exception: four small crops laid out two by two, not meant to be read, so one light and one dark version serve all three languages; a one-page picture such as the dashboard is shown whole rather than cut.

Root README pictures live in `docs/<plugin>/`, since the root READMEs are their only readers and every install copies the plugin folder; they show fictional data, because the full-size image can be zoomed until a name reads. Each plugin or suite gets a heading, a line on what it does, its picture, then a lead-in and its install command, with a suite's table before the command; the line comes before the picture because a line under a picture with no heading of its own reads as its caption. The root README is a short catalogue for people deciding what to install: it names what a plugin does without listing its skills or how it works inside. Depth goes in the plugin's own README, which is for its users who want more and for its maintainers.

## Reading Claude Code transcripts

The dashboard, whoami and remind-me all read them.

- Each assistant record of one response repeats `message.usage`; de-duplicate on (source file, `message.id`) while reading, since no fixed ratio corrects it afterwards. `tool_use` blocks are written once. Ids repeat across files, so pair `tool_use` and `tool_result` on (file, id). `input_tokens` is only the cache-miss part.
- Cleanup is keyed on a session's top-level file's `mtime` (`cleanupPeriodDays`), and its `subagents/` directory appears to go with it (inferred, not observed); a resume resets it, so `mtime` is last activity, not age.
- A backgrounded subagent's `tool_use` → `tool_result` pair records dispatch; its runtime is the span of its own `subagents/agent-*.jsonl`.
- A `<task-notification>` is a plain `user` record: count a user record as human only after excluding `tool_result` blocks and the `<task-notification>`, `<system-reminder>` and `<local-command>` prefixes.
- A per-step window includes whatever else the human did, so per-step figures are upper bounds.

## Conventions

Working notes live in `.claude/plans/`, which holds a `.gitignore` of its own that ignores the folder and itself. Nothing there is ever committed, and this repository is public, so that is the place for anything that must not be.

`.gitattributes` pins the working tree to LF on every platform, `*.cmd` excepted.

**This repository is public.** Nothing that reaches the remote may carry internal detail — no organisation's name, no ticket ids in commit subjects, bodies or branch names, and no internal host or repository names. Skill prose uses `acme-…` as the placeholder ticket format. The `leaks` gate catches hosts, GitHub accounts, ticket-shaped tokens and addresses; for anything else, grep a diff before committing.

Commits: a plain summary of 50 characters or fewer, with no prefix and no attribution trailer, committed to `main`.
