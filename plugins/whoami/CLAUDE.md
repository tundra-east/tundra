# CLAUDE.md

This covers the `whoami` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## What it is

One skill, a `SKILL.md`, and three scripts: `scripts/identity.py` finds and checks the person's git identities, `scripts/extract_prompts.py` gets the person's prompts out of the transcripts, and `scripts/render_report.py` turns the report document into HTML. SKILL.md is the authority on the method; this file records what must not be undone and what was tried and dropped.

- **The scripts only extract and render; the model does all the analysis.** In the trial every finding a script could compute (commit bursts, ratios, where contributions concentrate) dissolved once the author named the constraint behind it, and every finding that survived came from reading the code. Do not add a mechanical analysis layer.
- **No fixed table mapping finding shapes to constraints.** One was tried and failed on cases it was not built from, so the skill carries the rule (the constraint sits at the other end of a seam) and no table.

## Decisions that are easy to undo by accident

- **User-invoked only** (`disable-model-invocation: true`). It is never the answer to a task, so its description in every session's context would buy nothing, and model invocation would let it fire on "who am I" questions.
- **No question beyond scope, sources, identity and language.** Every finding is tested by the model against the material, never put to the person: the first version asked, and a gap dissolved whenever the person accepted a proposed constraint unchecked.
- **The arguments are one free-text request.** `$ARGUMENTS` arrives as the whole string, so nothing parses it positionally; an earlier version read each word as a path and turned "and" into a repository. The scope is only what the person names, never a scan of the machine.
- **How far back is not a parameter.** Nobody in the trials asked for one and a person cannot judge what number to give, and a period starting after AI shows up would remove the instances step 5 calibrates against.
- **Who typed the code is never asked or detected.** Asking failed (nobody remembers the date), detecting AI-written code by style failed, and matching commits against the transcripts' file edits found 1 of 14 commits in a wholly AI-implemented repository.
- **The sample is per repository, with no total cap.** A total thins each repository below what calibration needs, and a cap with per-repository shares was worked through and dropped as guesses stacked on guesses. Spreading the older sample by calendar, reading heavy stretches more closely, and weighting by commit size were each declined as one more judgement to vary between runs.
- **Other authors only in aggregate.** A per-person figure for anyone else is a workplace-monitoring output this plugin must never produce.

## The identity script

The rule that the report is about the person running it is code, not prose, because the model could be argued out of prose, and the prose once let a colleague's address be confirmed through the question's "Other" option.

- **It is a deterrent, not a boundary.** The seed is local configuration and `git log --author` needs no plugin; the check makes getting past it a deliberate edit. The Claude account's email was declined as a second seed: it is missing for an API-key login, and people often commit under another address.
- **The renderer runs the check again and exits 4, not 2.** Exit 2 tells the model to fix the document, and this failure must never be fixed by editing the addresses.

## The prompt extractor

The transcript format is undocumented, so what counts as a typed prompt rests on fields observed in real transcripts and pinned by known-answer cases in `tests/extract_prompts_tests.py`. A new record kind, or a field seen to change, gets a case there before the extractor is changed.

## The report renderer

The model writes a JSON document following `report-schema.md` and the renderer produces the HTML and the Markdown summary. The model never writes HTML, because a layout it writes differs per run and cannot be tested.

- **The page loads nothing.** Inline styles, no script, nothing from a CDN, so it opens offline and sends nothing; the colour switch and the collapsed evidence are pure CSS and `<details>`.
- **Declined, so not to be re-proposed:** diagrams as text boxes or SVG (CJK text misaligns or does not wrap); d3 or any charting library (CDN or size, no wrapping text, untestable by known answers); charts of the scope's counts (they describe the sample, not the person, and "2 prompts" reads as a score); a tick per change on the timeline, and splitting its solid stretch at gaps (both read as noise); a line-length measure on text, in any language.
- **The report is written under `${CLAUDE_PLUGIN_DATA}`**, outside every repository, so it cannot be committed by accident.

## Tests

`python tools/run_tests.py` from the repository root runs all three suites under `skills/whoami/tests/`, and each also runs alone as `python <file>`.

## Measure before claiming

Before a claim about what whoami finds goes into its README, run it on developers from populations the trial did not cover: a team repository and a solo one, greenfield and maintenance work. One developer's run proves only that developer's case.

## Conventions

The description stays a one-line summary: a user-invoked skill's description triggers nothing, so the trigger phrases and routing list a model-invoked skill needs would be dead weight here.
