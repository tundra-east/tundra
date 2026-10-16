# CLAUDE.md

This covers the `remind-me` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## What it is

One skill and two scripts. `scripts/collect.py` turns a day, or a range of up to 31 days, of transcripts into a digest and looks up what is true now; `scripts/render.py` turns the model's judgement and that digest into an HTML page and the summary the session prints. SKILL.md is the authority on the method; this file records what must not be undone and what was tried and dropped.

## Decisions that are easy to undo by accident

- **Live state is required, not an option.** The manual reconstruction the plugin came from listed six pull requests as awaiting review that had been completed the same day. Every pull request matching the `AZURE_PR` and `GITHUB_PR` patterns in `collect.py` is looked up, and one whose state cannot be read is reported as not checked, never as pending.
- **Figures come from scripts, judgement from the model.** Counts, times, token usage, folders and resume commands are copied from the digest by the renderer, never written by the model. The renderer shows no pull request's state of its own, so a merged or abandoned one reaches the page only through the item text SKILL.md asks for. The model writes the headline, topics, items and an emoji per repository; an unusable emoji falls back to the initial without failing the run. Item text can still quote a number from a session, and nothing checks it.
- **Resuming is a `claude-cli://open` link** with the session's start folder as `cwd` and `/resume <id>` filled in, behind Claude Code's own warning (checked by hand on Windows, 2026-10-06); the copyable command stays for when the handler is not registered. The folder is where the session started, never a later transcript `cwd`, which follows the shell.
- **Cache reads are their own figure, not dropped.** They measure how long and large the sessions grew, and at the base input price they outweighed the day's cache writes.
- **No session title.** Claude Code titles a session after its first prompt and sessions drift, so the model lists topics.
- **No tables in the session summary.** The terminal prints a `<br>` inside a table cell literally.
- **A session is one bar, from its first entry to its last.** Drawing each stretch of activity read as several sessions, so the collector no longer computes stretches. On a range a session resumed on later days is still one bar, and rows are packed by where bars land on the page, since a month's columns are too narrow for two sessions an hour apart.
- **Each button has one home.** A repository's header holds the folder's ways in plus one shortcut, resuming its most recent stopped session; a session's header holds only its own way back.
- **A repository git could not read is marked**, since without a mark it looks exactly like a clean one.
- **Retention is not read.** The skill names `cleanupPeriodDays` only on an empty run; reading the value properly meant five sources, one an undocumented cache file, for one sentence.
- **The days are judged from their own prompts alone.** Judging open items through prompts from after the range wrote a later day's decisions into this day's lists and was reversed on 2026-10-08, so the collector no longer gathers them. Pull requests, branches and uncommitted work are still read as of now.
- **New pull requests are the person's**, matched against the accounts the CLIs are signed in as plus the repositories' git emails; a git email alone fails on GitHub, where it is often a noreply address.
- **A range is never continued from an earlier report.** Five reports of the same day and sessions listed four to ten open items (2026-10-09), so items cannot be matched across reports.
- **One reader per repository, whatever the size.** On one real week (2026-10-12, 419 KB, six repositories) one reader for the whole digest caught 55% and 73% of the open items that one reader per repository caught 87–94% of.
- **Repositories are chosen in the collector, before anything else.** The filter runs before the default day is picked, so a run naming a repository without days reports that repository's latest day of work, and before the live lookups, which are what a month spends its time on. Filtering is safe because an item settles within its repository. A bare name is matched to the folder's name and never resolved on disk, so a subfolder of here sharing it cannot win, and a name no transcript started in exits 4 with the folders on record, since otherwise a misspelt name reads exactly like a repository with no work.
- **A repository over 400 KB is refused, not cut.** A reader judged 208 KB well; the largest seen over a month was 764 KB.
- **The overview shows each repository's latest three items**, with the repositories most open first.
- **A maximised card replaces its row**, with a tab per card of that row only; tabs for every card were set aside (2026-10-12) because they would sit under the wrong row's title. The state is in the anchor as `<panel>~<kind>`, so back and reload keep it. A maximised card shows at most two columns, one below 1200 pixels.
- **Dates are written by the page script with `Intl.DateTimeFormat`**, month as a word, because `10/9` reads either way round (2026-10-12); without the script, and in the terminal summary, they are `YYYY-MM-DD`.
