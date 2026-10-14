---
name: tempo-logger
description: >
  Query, suggest and write the caller's own Tempo worklogs from natural language:
  fill in a timesheet, log hours against a work item, check what is logged, correct
  hours on an entry, or remove one. Learns which work items they use from their own Tempo
  history and writes nothing until they confirm.
  Use whenever someone wants to see, fill in or fix their own timesheet or
  worklogs, and for setting this tool up — its token, its cluster, an unauthorized
  error from this tool. Triggers: "log my hours", "fill in my timesheet", "log time in
  Tempo", "what did I log this week", "change Thursday to 6 hours", "delete Thursday's
  entry", "remove the hours I logged on Friday", "/tempo-logger".
  Do NOT trigger for: hours with no timesheet or Tempo context at all;
  adding a worklog through Jira's own worklog API (it bypasses Tempo's own work
  attributes, such as a required account); submitting or approving a timesheet;
  how the Tempo REST API itself works; anyone else's timesheet.
---

# tempo-logger

Every write goes through `apply.py`. Every proposal is rendered by `plan.py` and
relayed to the user **verbatim**.

**Pick your path first.** The two closing sections — **Never do these** and **What this
skill does not do** — are not part of either path: they apply to every run, so read them
whichever path you are on.

| The user wants | Do this |
|---|---|
| only to see what is logged, or types `/tempo-logger` alone | Steps 0, 1, 1R, then stop |
| something written or changed | Steps 0, 1, then 2 to 6 in order |
| a setting changed, or a setup error explained | Steps 0 and 1, then the `setup set` line in Step 1 |
| both — "what did I log, and fill in the rest" | the write path, and report the reading from the proposal's own table |

Step 1R is the read path's last step, and sits right after Step 1 because it needs
nothing else. The write path reaches it only when Steps 3 and 4 send it back for a date's
current hours, which does not end the run.

## Step 0 — Resolve the script directory once

Take the `scripts/` directory from the **base directory you were given when this skill
was loaded**, never from `$0` or the working directory — you are not running as a shell
script, and the working directory is the user's repo.

```
<skill-base-dir>/scripts/tempo.sh   <script-name> [args...]   # sh, macOS, Linux, Git Bash
<skill-base-dir>\scripts\tempo.cmd  <script-name> [args...]   # cmd.exe, PowerShell
```

The steps below write that path as `TEMPO`. Resolve it once. The first argument is the
script name without `.py`: `setup`, `catalogue`, `show`, `plan`, `apply`.

**Always go through the wrapper, never `python` directly.** It forces UTF-8 from outside
the interpreter, which cannot be done from inside one. Without it, printing a table
raises `UnicodeEncodeError` part-way — and in `apply.py` that lands *after* the writes
and before the result text, leaving hours written and nothing reported.

**The wrapper has its own exit 2** — no script name, no such script, or no python on
`PATH`. It means nothing ran at all, and **every script has an exit 2 of its own**, so
the code alone cannot tell them apart. It always says which on stderr, and **the name in
that message is what tells you whose exit 2 it is**:

- `tempo: no such script...` or `tempo: no python interpreter found...` — the wrapper.
  It says the latter when nothing on `PATH` accepts `-X utf8`, which includes a Python 2
  and a `py` launcher that cannot find a Python 3.
- `usage: tempo.sh <script-name>` or `usage: tempo.cmd <script-name>` — also the
  wrapper, called with no script name.
- `usage: setup.py ...`, `usage: plan.py ...` — the script itself; its own exit table applies.

For the first three, nothing ran: fix the path or the python installation, do not read it
against any step's exit table, and do not re-run unchanged. Reading one of them as
`apply.py`'s exit 2 sends you looking for a half-written run that never started.

Do not probe the path with a Tempo call first; a credential failure inside a step about
paths reads as a broken path. Step 1 is the probe.

## Step 1 — Check setup before anything else

```
TEMPO setup check
```

It prints a line reading `status: <slug>`. **Find that line anywhere in the output and
branch on the slug** — its position varies, and the exit code is coarser
(`unauthorized` and `error` share exit 1).

**If there is no `status:` line at all**, the script did not reach its own reporting: the
usual cause is a `config.json` that is not valid JSON, which raises before anything is
printed. Relay stderr, and say the config file needs fixing or deleting — do not treat it
as a credential problem, and do not re-run expecting a different answer.

<!-- generated: setup-status -->
| `status:` | Exit | Meaning |
|---|---|---|
| `ready` | 0 | setup is complete; continue |
| `no-token` | 3 | no token file was found |
| `cold-start` | 4 | no worklogs are visible, so there is no history to work from |
| `unauthorized` | 1 | Tempo refused the credentials; the output separates the three causes |
| `error` | 1 | anything else; the output says what |
<!-- /generated: setup-status -->

**On anything but `ready`, stop and relay what it printed.** The output is written to be
handed over: the `unauthorized` branch has already separated its three causes, and
`cold-start` says whether the account id is suspect or the history is genuinely empty.

One needs more than relaying:

- `cold-start` means nothing is logged on this timesheet at all. **If the user only
  wanted to look, tell them that first** — it is the answer to what they asked. It also
  blocks writing, and blocks reading too: Tempo has no endpoint saying who a token
  belongs to, so with no worklog to read there is no author to read it for. One worklog
  logged in the Tempo UI clears all of it, and supplies the numeric issue id and the
  project account a write needs, neither of which the API derives from a Jira key. But
  read the text first: if it names a configured `authorAccountId`, the id may simply be
  wrong, and `setup set` fixes that without logging anything.
Config goes through `TEMPO setup set <key> <value>`, which validates the value, keeps the
other keys, and carries a pre-XDG config forward rather than stranding it. Token and config
live under the XDG config directory, proposals and results under the XDG state directory
(`%APPDATA%` and `%LOCALAPPDATA%` on Windows). `setup check` prints the three it
uses — `baseUrl`, the config file, the token file — and says when it is still reading a
pre-XDG location. **It does not print the state directory**, so for the results file
Step 6 needs, take the path from `apply.py`'s own output rather than from here.

<!-- generated: setup-config-keys -->
| Key | Effect |
|---|---|
| `authorAccountId` | whose worklogs are read and written |
| `baseUrl` | which Tempo host is called |
| `defaultStartTime` | the earliest a created row may start |
<!-- /generated: setup-config-keys -->

The rendered output is English and cannot be configured. Say so plainly if asked. When
you are working in another language, a short summary beside an English block is fine;
replacing the block is not. Step 5 has the rule.

## Step 1R — read what is already logged

```
TEMPO show                                   # the approval period containing today
TEMPO show --from 2026-09-01 --to 2026-09-30
TEMPO show --from X --to X --json            # when you need a row's issue id
```

<!-- generated: show-exits -->
| Exit | Meaning |
|---|---|
| 0 | worklogs printed on stdout |
| 1 | the read failed; stdout is empty and stderr says why |
| 2 | bad arguments: --from and --to go together, and --to must not precede --from |
<!-- /generated: show-exits -->

**Never relay empty stdout as "you logged nothing".** A non-zero exit means nothing was
*printed*, whatever was read; the reason is on stderr. An empty range is different.
The text output says so in words and exits 0.
`--json` prints a bare `[]` and also exits 0, so `[]` is genuinely empty
while empty stdout is not. Silence is a failure, and reporting it as an empty
timesheet is what makes someone log the same hours twice. On a non-zero exit relay
stderr, and go back to Step 1 if you have not run it.

**Say which range you are reporting.** With no dates it returns the approval period, read
from `/4/periods` rather than computed, and its first output line states the span —
repeat that rather than calling it "this week". **Do not narrow it to Monday–Friday
yourself**: that is the write path's fallback for choosing days to *fill*, not a claim
about which days hold hours, and this skill proposes weekend rows whenever the user's
history has any. Pass explicit dates only when the user named a different span.

**On the read path, stop here.** `/tempo-logger` alone is this path: it shows the period
and stops, without going on to Step 2. It does **not** compare logged against expected,
and no script reads an expected figure at all. On some tenants there would be nothing to
read: `/4/user-schedule` has been seen returning `requiredSeconds: 0` for every day. Do not present a comparison as
unavailable *because of* the tenant, and do not invent one from a 40-hour week.

**Answer the question and let it end there.** A read that closes with "shall I fill that
week in?" reads as helpfulness and is not: it asserts that the empty days ought to hold
hours, which is the one judgement this skill refuses to make everywhere else. Nothing
tells you why a day is empty — leave, a public holiday, a four-day week, a day genuinely
not worked — and Tempo cannot tell you either, since `requiredSeconds` comes back 0 for
every day. So an offer to fill is a guess dressed as an offer, and it puts "write
something" in front of someone who asked only what was there.

An empty range is where the pull is strongest, and it is also where the guess is worst:
a week with nothing in it looks like an omission and may simply be a week off. Report it
and stop. If they want it filled they will say so, and what they say is what the write
path needs anyway.

**Steps 3 and 4 send you back here** to read what a date holds before computing
`finalHours`, and coming here for that does not stop the run.

What a verb needs is **that template's total on that date** — the sum of every worklog
matching it, not one of them and not the whole day's hours across all work items.

When one account appears once on the date, the text table's `Hours` cell is that total and
you are done. Otherwise:

1. Run `TEMPO show --from <date> --to <date> --json`. Each row carries `issue.id` and
   `attributes.values`.
2. Keep the rows whose `issue.id` and attribute values match the catalogue entry's
   `issueId` and `attributes`, comparing **only the keys in the catalogue's top-level
   `requiredAttributes`** — a worklog carries every attribute it has, and the template's
   identity is the required ones. **Do not try to compute `templateId`**: it is a hash of
   that identity, not something you can derive in conversation.
3. Sum their `timeSpentSeconds`. That is the number your `finalHours` is computed from.

One account can carry two issue ids, which is why the text table alone is not always
enough, and why the match is on the identity rather than on the account key.

## Step 2 — Build the catalogue

```
TEMPO catalogue --weeks 8
```

<!-- generated: catalogue-exits -->
| Exit | Meaning |
|---|---|
| 0 | a catalogue on stdout; templates may still be empty |
| 1 | the read failed; stdout is empty and stderr says why |
| 2 | bad arguments |
<!-- /generated: catalogue-exits -->

Returns JSON. **This is the one output you interpret rather than relay** — matching the
user's words onto these templates is your job. The window reaches both back and forward
from today, so leave booked ahead is visible.

Eight keys sit **top level, beside `templates` — they are not fields on a template**,
which is where they get looked for and not found. Five matter here:

| Top-level key | What it holds |
|---|---|
| `warnings` | free text; relay anything in it, every time — the only self-rescue hint lives here |
| `weekendHistory` | `count`, `lastUsed`, `dates` — **past dates only**, and what decides whether a vague range includes weekends |
| `accountNameCollisions` | the groups of account keys that share one name |
| `requiredAttributes` | one object per attribute this tenant requires, each with `key`, `name`, `type` — it is the `key` values you compare on |
| `author` | `accountId` and `source`, so you can confirm whose history this is |

The other three are `generatedAt`, `today` and `window`, which just record what was
built and when.

**`templates: []` on exit 0 is not cold start.** Step 1's `ready` proves the token can
see some worklog at some date; this window is only today ± 8 weeks. Retry wider —
`--weeks 26`, then `52` — before telling anyone they have no history.

**Always carry the number you used into the intent as `catalogueWeeks`**, even when it was
the default 8. Sending it is never wrong and omitting it after widening is: `plan.py`
rebuilds the catalogue at 8 weeks, cannot find your template, and exits 1 saying "it may
be from an older catalogue, or the id was mistyped. Rebuild the catalogue and pick again."
**That remedy is wrong for this cause** — rebuilding at 8 weeks will never surface the
template. Carrying the number over is what fixes it.

**Exit 1 is a failure, never an empty history.**

`templates[]`, ranked by occurrence then recency:

<!-- generated: catalogue-fields -->
| Field | What it is |
|---|---|
| `id` | hand this to plan.py; derived from the identity |
| `issueId` | the numeric Jira issue id |
| `attributes` | every required attribute value — the rest of the identity |
| `count` | how many worklogs this template came from |
| `lastUsed` | the most recent date it was used |
| `descriptions` | Tempo's own generated text, where a Jira key appears |
| `dailyTotalHours` | per-day totals; the only field that governs hours |
| `daysUsed` | how many distinct days those totals came from |
| `account` | key, name, raw status, resolved, attributeKey, collidesWith |
| `usable` | whether a write could use it |
| `unusableReason` | why not, when it could not |
<!-- /generated: catalogue-fields -->

**`dailyTotalHours` is what governs hours**, and it is per *day*: a template logged as
4h + 4h on one date counts as an 8h day, not two 4h ones. You may omit `hours` only when
that list holds a single value **and** `daysUsed` is more than one — one day is not a
pattern, and `plan.py` asks rather than derive from it. Anything else means supply the
hours or be asked.

**A template with `usable: false` is shown, not hidden.** Report its `unusableReason`.
Silently dropping a match looks like the user's own typo, and they retype it.

**When `account.collidesWith` is non-empty, always show `key` alongside `name`** —
another account carries the same name. `accountNameCollisions` lists the actual groups.

**`account.resolved: false`** means the key is no longer in `/4/accounts` — renamed or
removed, which is why `name` and `status` are `null`. That is a different remedy from an
account that resolved and is `CLOSED`.

Project accounts are a facet to filter and display by, **never** selectable on their
own: an account with no history yields no issue id.

## Step 3 — Resolve the intent

Your work, not a script's. Three things.

### Which template

Match the user's words: synonyms, typos, partial names, another language. `vacation`,
`休假`, `annual leave` all reach the same template if their history holds it.

| Input | How it lands |
|---|---|
| `10017` | exact match on a template's issue id |
| `ACME-182` | text match against the description Tempo generated |
| `200-17` | exact match on a catalogue account key |
| `widget dev` | account name or template description |

**Name the template you picked by its identity** — issue id plus attribute values —
never by name alone. Account names collide, and `category`/`customer` do not separate a
colliding pair.

**Do not pattern-match account keys.** Their shapes vary enough that any regex misses
one; match against the keys the catalogue returned.

**If more than one template is a candidate, list them and let the user pick — never pick
for them.** Ranking is count and recency, so after three weeks of leave the top template
is vacation, and picking automatically proposes a week of leave. That is the misjudgement
direction nobody catches.

**Give every candidate its Jira key, not only its account.** Account names cluster —
`Widget Platform – Development` and `Widget Platform - Governance` both answer to
"widget" — and what tells them apart is `ACME-182` against `ACME-177`, which the account
name does not carry. A list without the keys asks the user to remember a mapping. Show
the key, the account `key · name`, the count and the last used date; the key comes from
the template's `descriptions`, the same place the confirmation table takes it from.

**If nothing matches, refuse** and show recent templates. **Say why it did not match,
and be exact: it is absent from their history, not absent from Jira.** The catalogue is
built only from worklogs this caller has already logged, while Tempo's own picker
searches every issue, so a real and perfectly valid ticket is simply not here yet.
Telling someone "no such work item" sends them hunting for a typo in a key that is fine.

The remedy is the cold-start one, for the same reason: nothing in the Tempo API turns a
Jira key into the numeric issue id a write needs, so **one worklog against that issue,
logged in the Tempo UI, is what makes it available here** — after which it appears in the
catalogue like any other. Widening `--weeks` first is worth a try if they have used it
before, just longer ago than the window.

A numeric id with no template is refused outright, on the reasoning that Tempo can reject
an id that does not exist but has no way to reject one that exists and is not yours — so
a mistyped digit could write to a stranger's issue and return success. **That has not
been tested**, and it is not worth testing by writing to a stranger's issue; the refusal
stands on the risk, not on an observation.

**"Fill in my timesheet" names no template, and that is not a failed match.** Offer the
candidates the same way — Jira key, account `key · name`, count, last used — and let the
user pick one or several. The refusal above is for a request that names something and
misses.

**Vacation has no special case.** Tempo does not distinguish a holiday from personal
leave, so neither does this skill.

### Which dates

Classify the input; `plan.py` applies the rules.

| Input | `dateKind` | Send |
|---|---|---|
| days named one by one (`on 2026-09-20`, `Monday and Thursday`) | `explicit` | `dates: [...]` |
| a span the user named (`the 14th to the 18th`) | `explicit` | `range: {from, to}` |
| a span you inferred (`this week`, `September`) | `vague` | `range: {from, to}` |

Send **exactly one** of `dates` or `range`; both together is refused. `vague` must use
`range`.

**Only `vague` is filtered.** An `explicit` range is written out in full — so "Monday and
Thursday this week" must go as `dates`, or Tuesday and Wednesday get proposed too with no
exclusion line to reveal it.

**Never pre-filter a `vague` range.** Send the whole span; `plan.py` prints what it
removed and you would not. Its exclusion line begins `Excluded (` and names the reason,
the count and every date. Say the count aloud.

**Future dates are legal and nothing drops them.** A Wednesday run of "this week" that
quietly omitted Thursday and Friday would leave the user believing the week is done.

**Weekends are not simply excluded.** On `vague`, `plan.py` includes them whenever
`weekendHistory.count > 0` — one past Saturday is enough. Nothing says so in words, but a
week table grows a Saturday or Sunday column when that day carries something, so read the
columns before describing the range: saying "weekends excluded" when nine weekend rows
just went in is worse than saying nothing.

**On `vague`, expect the Mon–Fri fallback.** `plan.py` prefers `/4/user-schedule`, but a
tenant that reports every day as `NON_WORKING_DAY` gives it nothing to work from, and it
has been seen doing that. It then assumes Monday–Friday and says so:
"That is an assumption, not Tempo data."
Do not present the date set as Tempo's own, and do not strip that line.
The `explicit` paths never print it.

### Which action

**You cannot see what a date holds** — the catalogue has no per-date rows. Do not decide
CREATE versus UPDATE; `plan.py` reads the date. What only you can do is read which of
three requests was made.

| The request | Send |
|---|---|
| **Fill in the gaps** — "fill in my timesheet", "log 8h of ACME-182 this week" | `hours`, no `verb` |
| **Change what is there** — "add 2 hours", "change Thursday to 6" | `verb` + `hours` + `finalHours` |
| **Hours for named days** — "log 6 hours on Thursday" | `hours`, no `verb`, in `dates` |
| **Remove what is there** — "delete Thursday's entry" | `verb: "SET"`, `hours: 0`, `finalHours: 0` |

**A date that already holds this template still gets a row.** Tempo accepts several
worklogs for one issue on one date, so a second entry is legal, and whether it is a
genuine second entry or a repeat of a run already confirmed is something only the user
can tell. The row is proposed and its cell reads `7.00->15.00` — what the item comes to,
against what the date already held — so the repeat is visible in the cell itself.

**So say what the day will come to.** Re-running the same fill proposes the same rows
again, and nothing but that total stands between the user and a doubled day.

**A verb always needs `hours` sent explicitly.** With `hours` omitted the operand is
derived from the template's history, so `ADD` with no hours adds a whole historical day —
eight hours instead of the two the user said — and if `finalHours` is missing too, nothing
checks it. Omitting `hours` is only for the no-verb fill-the-gaps path.

**Reducing a row to nothing removes it.** Tempo stores no worklog of zero hours, so a
`SET` to 0 — or a `SUBTRACT` that reaches 0 — deletes that worklog outright. There is no
separate verb: the arithmetic says it, and the confirmation shows `8.00->0.00` before
anything is sent, so a removal is never an arithmetic surprise. A create of 0 hours is
still refused, because there is nothing to create.

**A verb needs something to act on.** `ADD` or `SUBTRACT` against a date holding nothing
for that template is a belief about the data that was wrong, so `plan.py` asks. `SET` is
the exception and simply creates the row.

**A verb acts on the day's total for that work item**, not on one worklog. A date holding
1h and 1h that is `SET` to 1.5 comes to 1.5, not 2.5: the plan puts the result on one
record and removes the others, and the confirmation shows `2.00->1.50` — the day's total
going to the asked-for number. **The removals have no cell of their own**, so the table
does not say how many records it takes. `finalHours` is the day's total for that item,
and there is no field naming which record to change.

**Say so when a verb will remove records.** The header's row count exceeds the cells
whenever a fold happens, and that difference is the only trace: on a date holding more
than one worklog for the item, a verb destroys the surplus ones irreversibly. Tell the
user before they confirm.

**One verb cannot span several dates.** `finalHours` is a single number checked against
each date's own current total, so send a verb for one date at a time.

**Every item lands on every date.** Two templates over a five-day week is ten rows — two
per day, and if both derive 8h that is five 16-hour days. The day total shows it; nothing
warns you. There is no way to scope an item to some of the dates, so "ACME-182 on Monday,
ACME-177 on Tuesday" needs two calls.

## Step 4 — Produce the proposal

Write the intent to a JSON file **outside the user's repository** — the system temp
directory, not the working directory, because a JSON file left in the working tree gets
swept into version control by the next `git add -A`. **Give it a name nothing else would
choose**, with a timestamp or a random suffix: a fixed name like `intent.json` is shared
with every other session on the machine, and one overwriting another between your write
and `plan.py`'s read produces a proposal for dates you never asked about. Then:

```
TEMPO plan --intent <path-to-intent.json>
```

```json
{
  "userInput": "log 6 hours of ACME-182 and 2 hours of ACME-177 on 2026-09-15",
  "dateKind": "explicit",
  "dates": ["2026-09-15"],
  "items": [
    {"templateId": "tf10a378a", "hours": 6.0},
    {"templateId": "taa744ba2", "hours": 2.0}
  ]
}
```

<!-- generated: intent-schema -->
| Field | Required | Meaning |
|---|---|---|
| `userInput` | no | the user's own words, echoed at the top of the confirmation |
| `dateKind` | **yes** | "explicit" or "vague"; "vague" must use range |
| `dates` | no | the dates, named one by one. Exactly one of dates or range |
| `range` | no | {from, to}. Exactly one of dates or range |
| `catalogueWeeks` | no | the --weeks used to build the catalogue; always send it |
| `items[].templateId` | **yes** | the id from the catalogue |
| `items[].hours` | no | the operand, not the result; omitting it derives from history, never do that with a verb |
| `items[].verb` | no | ADD, SUBTRACT or SET |
| `items[].finalHours` | no | your computed result; checked only when a verb is set |
<!-- /generated: intent-schema -->

**Always send `userInput`, verbatim.** The schema marks it optional and `plan.py` simply
omits the line when it is missing — leaving the confirmation table with nothing saying
which request it came from, which is what the user is checking it against.

Five more things this schema will not tell you, because they fail silently:

**`hours` is the operand, never the result.** With `verb: "ADD"` and 8 hours logged,
`hours: 2.0` means "add 2, giving 10". Putting your computed 10 there gives 18.

**`finalHours` is the only arithmetic check, and only with a verb.** Omit it or misspell
it and the check is skipped without complaint. It is never checked on a plain create.

**Only the last `finalHours` survives a merge, and it is taken from any item** —
including one with no verb. So a stray value becomes the one checked and an earlier wrong
one is never caught.

**Unknown and misspelled fields are ignored, not rejected.** `operand`, `finalHrs`,
`onexisting` all pass and take their default.

**Same `templateId` on the same date merges into one row.** Hours sum on a create — but
if any merged item carries a verb, the verb-less items' hours are **discarded**. So "4
hours this morning and 4 more this afternoon" is one 8-hour row, not two the user can
edit separately. If they want two, that needs the Tempo UI — say so rather than
re-running `plan.py`, which merges them again.

<!-- generated: plan-exits -->
| Exit | Meaning |
|---|---|
| 0 | a proposal was produced |
| 1 | an error; stderr says what |
| 2 | the intent file could not be read, or was not valid JSON |
| 3 | the instruction cannot be read without asking |
<!-- /generated: plan-exits -->

**On exit 0, search for the line `No row can be written, so there is nothing to
confirm.`** Its presence means one of two things and neither is done:

- **Rows exist but all are blocked** — relay their reasons and stop.
- **No rows at all** — every candidate was blocked, or the request resolved to nothing.
  Do not report it as complete; the user asked to log hours and none were.

**Read the table rather than counting rows.** A date is only left alone now when every
row on it was blocked, and that shows as a `NOT WRITEABLE` line rather than as a missing
row. Name any such date to the user.

**Exit 3 discards everything.** The question is raised from the first date that needs it
and propagates out, so a five-day request whose third day is ambiguous produces **no rows
at all** — not four rows and a question. Never read an exit-3 output as a partial
proposal, and after the user answers, re-send **every** date, not just the one asked about.

**On exit 3, read the printed text for the remedy** rather than matching a list — it
usually names the field. Three situations name none:

- `ADD` on an empty date — it tells you to use `SET`.
- `SUBTRACT` on an empty date — it offers "drop this date, or log a fresh row for it".
- **hours that cannot be derived** — `How many hours for <account>?`, either because
  the template's daily totals in history differ or because it has no history to derive
  from. This one is asking the user, not you.

Put all three to the user. **Do not guess past any of them** — "add 2 hours" and "make
it 2 hours" differ by six hours, and a guessed number here is this skill inventing hours.

**On exit 1, relay stderr.** If it says your `finalHours` disagrees with its arithmetic,
re-read that row's hours with `TEMPO show` rather than resending the same number.

**If stderr is a Python traceback rather than a sentence, the intent file is at fault** —
almost always a number sent as text (`"hours": "six"`). Fix the value and re-run; do not
relay a traceback to the user as though it were Tempo's own message.

## Step 5 — Relay the confirmation text verbatim

**Confirmation means the user replying after you have shown them the rendered text.** A
"go ahead, don't ask me" from earlier is not confirmation of a proposal they had not
seen. This is the gate the step exists to protect.

`plan.py` prints three machine lines first — `status: proposal`, `proposal: <id>`,
`file: <path>` — then a blank line, then the confirmation text. **The confirmation text is
everything after that blank line**, and it is what gets relayed. Keep the `proposal: <id>`
value for Step 6; the `file:` path is a local absolute path and there is no reason to show
it to the user.

**Output that text exactly as `plan.py` produced it.** Do not reformat, summarise,
translate, or drop rows. It is the main safety mechanism, and reformatting is how one of
its fields goes missing.

**One table per week: days across, work items down.** Each cell is what that work item
comes to on that day, so the table answers in one place which days are affected and what
each holds. A week is a readable amount, not a claim about Tempo: whether an approval
period lines up with a working week is unverified, and `show.py` reads the period from
`/4/periods` rather than computing it for exactly that reason. Do not tell the user a
table is one thing they submit.

```
  week of 2026-09-14                                 Mon 14  Tue 15  Wed 16  Thu 17  Fri 18
  -----------------------------------------------------------------------------------------
  200-17 · Widget Platform – Development (ACME-182)   +8.00   +8.00   +8.00   +8.00   +8.00
  200-16 · Widget Platform - Governance (ACME-177)    +1.00   +1.00   +1.00   +1.00   +1.00
  LEAVE_VAC · Absence - Vacation (ACME-106)            8.00    8.00    8.00    8.00    8.00
  day total                                           17.00   17.00   17.00   17.00   17.00
```

- **A cell** reads `+8.00` where that item had nothing on the day, `2.00->1.50` where it
  had something, and a plain number where a worklog is already there and this run leaves
  it alone. The number after the arrow is what the day will hold for that item.
- **The weekday is the column**, so no row repeats it, and a day with nothing is a gap
  rather than something to count. Only weeks with content appear, and Saturday and Sunday
  columns only when that day carries something.
- **`day total`** closes each week and is what that day comes to across every item. A
  blocked cell contributes nothing, so a total lower than the cells suggest is the
  blocked cell showing. A date in the proposal that comes to nothing reads `0.00`; a
  blank means the date is not in the proposal at all.
- **A blocked cell is marked `!`**, and under the table sit that week's notes — the
  blocked reasons in full, plus `month may be closed` where a date falls in an earlier
  month. **Relay them.** Nothing else is annotated: a note restating the cell beside it
  is read past, and it buries the one note that changes whether a row lands.
- **The row label is `key · name (JIRA-KEY)`.** The account key and the Jira key are
  different vocabularies — someone asks for `ACME-182` and the account is `200-17` — and
  Tempo's own UI leads with the Jira key, so both belong in the label. **No endpoint
  returns that key**: it is scraped from the description Tempo generates server-side, one
  key per issue id, so a worklog whose description was edited still shares its siblings'
  label. It is simply absent when nothing yields a key, and would vanish altogether if
  Tempo reworded that description — so do not tell the user it is a field they can rely
  on. There is no legend below the tables: the label says which work item it is, and the
  cells say what happens to it.

**What the table does not show is how many worklogs a cell takes.** A day holding 1h and
1h that is `SET` to 1.5 shows `2.00->1.50`, and behind it one record is changed and the
other removed. That is deliberate: the user asked for the day to come to a number, and
which records carry it is not theirs to manage. The header's row count is what they see;
the second confirmation above 50 rows counts the writes.
The account part has other shapes, and they are content rather than noise, so they must
survive relaying: `(no project account)` when there is none, `key · (name not found —
renamed or removed?)` when the key no longer resolves, and otherwise a trailing
`  [<status>]` whenever the status is neither `OPEN` nor absent — `[CLOSED]` and
`[ARCHIVED]` are the ones seen, but it is printed as it comes. An unresolved key never
carries a status suffix; that branch returns first. A numeric issue id is appended only
when the proposal puts two issue ids on one account, where the Jira key may not separate
them either.

Rows carrying an indented `NOT WRITEABLE:` line are shown deliberately. **Do not count
the reasons** — more of them exist than the shapes suggest, because one forwards three
different messages from the catalogue. Key on the text instead.

One is weaker than it reads: **`past this tenant's limit of N
(numberOfDaysAllowedIntoFuture)`** says Tempo is *expected* to refuse it. That is this
skill reading a tenant setting, not an observed refusal, and it is the ceiling on
"future dates are legal" — so present it as expected, not as Tempo's answer.

The rest describe refusals Tempo is expected to make, and two of them are on record as
verified: a closed or archived account, and hours of zero. **Relay whatever the reason
says and no more** — do not upgrade one to "Tempo will reject this" on your own.

One thing the table does not show: **start times**. `apply.py` chooses them at write
time, and only then: a created row starts at `defaultStartTime`, or after the day's
existing rows when they end later, and each further created row on that day follows the
one before it.
On a date that fills up, later rows are pushed towards the end of the day
and can end up at `23:59:00`.
The confirmation table has no time column, so none of this is visible before the write.
Say so if the user cares where in the day their rows sit; rearranging them is the Tempo UI.

**Nothing is withheld.** Every date in the proposal is in one of the week tables, so the
text you relay is the whole of it — there is no budget, no truncation line, and no case
where the user confirms something that was not shown.

**Above 50 writeable rows a second confirmation is printed**, stating how many rows will
be attempted split into new, changed and removed, and separately how many fall in months
that may be closed. **Say the removed count aloud** — it is the irreversible one.
50 is the bulk endpoint's `maxItems`.
That closure figure is information, not a deduction: every row is attempted, because
closure cannot be read from any endpoint. **That question needs its own answer.**
A "yes" to the table is not one, and if the user answers only once, **ask again** rather
than deciding which question they meant.

Then wait, and write nothing until they reply to the row table, and to the second
question when there is one.

## Step 6 — Apply, and relay the result verbatim

```
TEMPO apply --proposal <proposal-id>
```

**Use the id from the most recent `plan.py` call, and only if its text is what the user
just confirmed.** `apply.py` refuses a proposal a newer one has replaced, comparing the
dates each actually covers. There is no override, and two proposals built over the same
dates before either is applied leave the older one unappliable.

**That guard has holes, and covering them is the model's job.** It compares the dates
each proposal has rows on, so a revision that moves to **different dates** — "Thursday,
not Wednesday" — shares none with the original and supersedes nothing, leaving the
Wednesday proposal appliable with exit 0. A revision that fails before saving anything
(exits 1, 2 or 3) supersedes nothing either, for the same reason. In both cases the
earlier id is still valid and is still the wrong answer: apply only the proposal whose
text the user just confirmed.

<!-- generated: apply-exits -->
| Exit | Meaning |
|---|---|
| 0 | every row written |
| 1 | at least one row was not written, or the run failed before starting |
| 2 | stopped part-way, or nothing was sent; untried rows exist |
| 3 | refused to run: the id was not found, or its file is not valid JSON |
| 4 | refused to run: a newer proposal shares a date with this one |
<!-- /generated: apply-exits -->

**Decide what landed from the result text, never from the exit code.** Exit 0 also covers
a proposal with no rows in it, which wrote nothing.

- **A result text was printed** — it is the authority. Read the per-row statuses. This is
  normal for exits 1 and 2, including a revoked token, which fails the opening snapshot
  read and marks every row `untried` *with* a full result text.
- **stdout is empty** — usually nothing was attempted. Relay stderr.

**But empty stdout does not prove nothing was written.** Each row's outcome is appended
and flushed to a record file as it happens, while the result text prints only at the end —
so a run cut short has written rows and no stdout. **Read that file before telling anyone
nothing was written.** Reporting written hours as unwritten is the one direction that
makes a user log them twice.

Finding it: when a result text was printed, its last line is `Row-by-row record: <path>` —
use that. When there is none, the file is `results/<proposal-id>.jsonl` under the state
directory. That directory is `$XDG_STATE_HOME` + `/tempo` when that variable is exported —
on Windows as well — and otherwise `%LOCALAPPDATA%` + `/tempo` on Windows or
`~/.local/state/tempo` elsewhere. **One `tempo` in the path, not two:** the state
directory already ends in it, so the file sits directly under `results/`.

It accumulates across runs, so take the latest entry per row by its `run` and `at`
fields.

<!-- generated: apply-statuses -->
| Status | Meaning |
|---|---|
| `written` | written to Tempo |
| `skipped-precondition` | not sent: the row changed under the proposal, or was already unwriteable |
| `skipped-propagated` | not sent, and NOT recorded anywhere: an earlier failure covered it |
| `failed` | sent and refused; the reason carries Tempo's own message |
| `untried` | never sent |
<!-- /generated: apply-statuses -->

**`skipped-propagated` is never to be softened.** "Skipped" reads as "handled", and these
hours are still missing from Tempo. Relay its reason, which names the month or account
that caused it.

**`failed` is relayed verbatim** — its reason carries Tempo's own wording, and a
paraphrase loses whether it was a closed period or something else.

**`untried` means re-run.** A re-run usually recognises rows an earlier run of the same
proposal wrote — creates by their worklog id, updates by the worklog already holding
exactly the intended hours — and reports them `written` with the run that wrote them,
without duplicating them.

**One gap survives, in the direction that costs the user.** A create whose worklog id the
bulk response did not return is recorded `written` with no id (its reason says so), and a
re-run cannot then recognise it: the row comes back `skipped-precondition` saying worklogs
now exist on a date proposed as having none. So on a re-run, treat a `skipped-precondition`
that names existing worklogs, or one naming any of `timeSpentSeconds`, `billableSeconds`,
`startDate` or `startTime`, as possibly your own earlier write — **check the record file
before telling the user it did not land.**

**A proposal that has been sitting a while may be stale**, since `plan.py` could not have
checked what changed after it ran. There is no way to test that without writing: re-run
`plan.py` instead, which both re-reads the dates and supersedes the stale proposal.

**Finishing.** The run is over when you have told the user, from the result record and not
from the exit code: the hours that were written and on which dates, every row that was
not written and its reason, and what to do about each. A run holding `untried` rows is not
finished — re-run it. A run holding `failed` rows is usually finished, and the answer is
the reason Tempo gave: relay it, and do not re-plan the same dates hoping for a different
one. A closed period does not open by being asked twice.

**One kind of `failed` is the exception.** A reason about throttling or rate limiting,
rather than a refusal, means the write ran out of attempts rather than being rejected.
That one is worth re-running after a pause. Read the reason for which it is; do not
match it against a string.

## Never do these

**Never assemble a Tempo write call by hand** — not with `curl`, not with `python`, not
by copying a body from this repository, and not with any other HTTP tool, fetch, or MCP
server. The rule is about the destination, not the instrument: nothing but `apply.py`
sends a write to Tempo. `billableSeconds` follows `timeSpentSeconds`
upward but not downward, so a hand-written `PUT` that changes 8 hours to 6 records six
hours billed as eight, with no error. `apply.py` is the only thing that corrects for it.

This holds especially when a script has just refused something. **A refusal is the
answer, not an obstacle to route around**: relay why, let the user decide, and if they
still want it the route is the Tempo UI.

**Never write through Jira's worklog API** — it has no concept of Tempo's project
account, which your Tempo may require, so it produces an incomplete entry.

**Never use `bypassPeriodClosuresAndApprovals`.** `apply.py` does not implement it, so
when a period is closed, say so and stop. Two things are on record: the flag exists on
create and delete and needs Tempo Administrator plus Override Mode, and `WorklogUpdate`
has no such field, so an update has no override even in principle. Whether that makes a
worklog in a closed month unmodifiable in practice is **inferred, not established**.
Whether the Tempo UI can do what the API cannot is not recorded either way, so do not
send the user there as though it were the way around it, and do not tell them it is
closed there too. Say the period is closed and let them decide.

**Never claim a row was written without the result record saying so.**

## What this skill does not do

Submitting or approving a timesheet, detecting public holidays, setting a work
attribute your history does not already carry, and anything involving Jira.

**Moving a worklog to a different issue** is impossible through the update endpoint:
`WorklogUpdate` has no `issueId` field, which is on record. This skill does not offer it
as delete-plus-create either, because the two halves are separate writes and a failure
between them loses the hours. Removing the row and logging a fresh one is the user's own
call to make, in two steps they can see.

**Changing an existing row's project account** is likewise not implemented — `apply.py`
does not put attributes in the update payload. A `PUT` is a merge, which makes it look as
though sending attributes would work,
but the merge behaviour has been observed only for omitted fields,
never for attributes. So say the tool does not do it,
and stop there: do not tell the user Tempo would accept a hand-made call, because the next
thing they do is make one.

For both, point at the Tempo UI with the date, hours and project account. **Do not
approximate either by creating a row on the right template and leaving the wrong one** —
that doubles the day's hours.
