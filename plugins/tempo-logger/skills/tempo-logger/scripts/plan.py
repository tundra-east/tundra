"""Turn an intent into a proposal, and render the confirmation text.

Reads. Never writes to Tempo.

Why so much lives here rather than in the model's prose: the confirmation table is
the skill's main safety mechanism, and it carries about a dozen format rules. Every
annotation on it is a function of data a script already holds — the day total, the
template's own history, the future and weekend flags, the closure risk, the billable
change. Rendering here turns "the model formatted it wrong" into "the model did not
relay it verbatim", which is a far more visible deviation.

Two things this file refuses to do, both because a script cannot do them safely:
it never guesses how to read an ambiguous instruction, and it never accepts the
model's arithmetic without recomputing it from its own snapshot.
"""

import argparse
import calendar
import datetime
import io
import json
import re
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalogue  # noqa: E402
import tempo  # noqa: E402

PROPOSAL_DIR = os.path.join(tempo.STATE_DIR, "proposals")

# 50 is not invented: it is maxItems on POST /4/worklogs/issue/{id}/bulk,
# the point past which a write stops being one atomic call per issue,
# so it is where the risk of partial failure begins.
BULK_MAX_ITEMS = 50

EXIT_PROPOSAL = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NEEDS_CLARIFICATION = 3

VERBS = ("ADD", "SUBTRACT", "SET")

# a row that only removes a record whose hours the primary row has already absorbed.
# It is a real write and is counted as one, but it has nothing of its own to show.
FOLDED = "folded into the row above"

# the contract this script publishes; see setup.py's note.
EXIT_MEANINGS = (
    (EXIT_PROPOSAL, "a proposal was produced"),
    (EXIT_ERROR, "an error; stderr says what"),
    (EXIT_USAGE, "the intent file could not be read, or was not valid JSON"),
    (EXIT_NEEDS_CLARIFICATION, "the instruction cannot be read without asking"),
)

# required is what resolve_dates / build_rows refuse to proceed without
INTENT_FIELDS = (
    ("userInput", False, "the user's own words, echoed at the top of the confirmation"),
    ("dateKind", True, '"explicit" or "vague"; "vague" must use range'),
    ("dates", False, "the dates, named one by one. Exactly one of dates or range"),
    ("range", False, "{from, to}. Exactly one of dates or range"),
    ("catalogueWeeks", False, "the --weeks used to build the catalogue; always send it"),
    ("items[].templateId", True, "the id from the catalogue"),
    ("items[].hours", False,
     "the operand, not the result; omitting it derives from history, never do that with a verb"),
    ("items[].verb", False, "ADD, SUBTRACT or SET"),
    ("items[].finalHours", False, "your computed result; checked only when a verb is set"),
)


class Clarify(Exception):
    """The instruction cannot be read without asking. Carries the question's material."""

    def __init__(self, lines):
        super().__init__("\n".join(lines))
        self.lines = lines


# ---------------------------------------------------------------- date resolution

def resolve_working_days(date_from, date_to, author_account_id, config, token):
    """Decide which dates in the span are working days.

    Queried across the WHOLE span rather than per date. A four-day-week user running
    "log hours for friday" would otherwise get NON_WORKING_DAY for that single day,
    fall back to Mon-Fri, and have Friday treated as a working day — while the same
    user running "for this week" would get the opposite answer.
    """
    schedule = tempo.read_all(
        "/4/user-schedule/" + author_account_id,
        {"from": date_from.isoformat(), "to": date_to.isoformat()},
        config, token,
    )
    working = {row["date"] for row in schedule if row.get("type") != "NON_WORKING_DAY"}
    if working:
        return working, "schedule"

    fallback = set()
    date = date_from
    while date <= date_to:
        if not tempo.is_weekend(date):
            fallback.add(date.isoformat())
        date += datetime.timedelta(days=1)
    return fallback, "fallback"


def resolve_dates(intent, author_account_id, weekend_history, config, token):
    """Return the dates to propose on, plus what was left out and why."""
    kind = intent.get("dateKind")
    if kind not in ("explicit", "vague"):
        raise tempo.ConfigError('dateKind must be "explicit" or "vague"')

    excluded = []
    if intent.get("dates") and intent.get("range"):
        # preferring one silently discarded the other, so a five-day span sent
        # alongside a single date proposed one row and reported exit 0
        raise tempo.ConfigError(
            "the intent carries both dates[] and range{} — send exactly one, "
            "since which of them you meant changes how many rows are proposed"
        )
    if intent.get("dates"):
        dates = sorted({tempo.parse_date(d) for d in intent["dates"]})
        if not dates:
            raise tempo.ConfigError("dates was empty")
        if kind == "vague":
            raise tempo.ConfigError(
                'dateKind "vague" needs a range, not a dates list — '
                "the exclusions can only be reported against a span"
            )
        return dates, excluded, "dates"

    span = intent.get("range") or {}
    if not span.get("from") or not span.get("to"):
        raise tempo.ConfigError("give either dates[] or range{from,to}")
    date_from = tempo.parse_date(span["from"])
    date_to = tempo.parse_date(span["to"])
    if date_to < date_from:
        raise tempo.ConfigError("range.to is before range.from")

    every_date = []
    date = date_from
    while date <= date_to:
        every_date.append(date)
        date += datetime.timedelta(days=1)

    if kind == "explicit":
        # an explicit span is taken at its word: no rule of ours removes a date the
        # user named, including a weekend or a future date.
        return every_date, excluded, "range"

    working, _source = resolve_working_days(
        date_from, date_to, author_account_id, config, token)

    # weekends come in only when this user has actually logged on one before,
    # and then the evidence is shown rather than a threshold applied —
    # a threshold is a hidden judgement, and this fires at n=1.
    include_weekend = weekend_history["count"] > 0
    dates = []
    weekend_excluded = []
    for date in every_date:
        text = date.isoformat()
        if tempo.is_weekend(date):
            if include_weekend:
                dates.append(date)
            else:
                weekend_excluded.append(date)
            continue
        if text in working:
            dates.append(date)
        else:
            excluded.append((date, "not a working day per Tempo's schedule"))

    if weekend_excluded:
        excluded.extend((d, "weekend") for d in weekend_excluded)
    return dates, excluded, "range"


# ---------------------------------------------------------------- snapshot

def read_snapshot(dates, author_account_id, config, token):
    """One range read covering the whole proposal, reused for every precondition.

    Completeness is not optional. If pagination were truncated, the missing days
    would look empty, every CREATE precondition would pass, and the write would
    duplicate hours on days that already have them — so tempo.read_all raises a
    global failure on the page cap rather than returning a short list.
    """
    rows = tempo.read_all(
        "/4/worklogs/user/" + author_account_id,
        {"from": min(dates).isoformat(), "to": max(dates).isoformat(), "limit": 100},
        config, token,
    )
    return rows


def _index_snapshot(rows, required_keys):
    """Group snapshot rows by date and by (date, template identity)."""
    by_date = {}
    by_key = {}
    for row in rows:
        date_text = row.get("startDate")
        by_date.setdefault(date_text, []).append(row)
        issue_id = (row.get("issue") or {}).get("id")
        values = {
            value["key"]: value.get("value")
            for value in ((row.get("attributes") or {}).get("values") or [])
        }
        attributes = {key: values.get(key) for key in required_keys}
        template = catalogue.template_id(issue_id, attributes)
        by_key.setdefault((date_text, template), []).append(row)
    return by_date, by_key


# ---------------------------------------------------------------- row building

def _hours_for(item, template):
    """The hours for a row, and how they were arrived at.

    Derived only when every day this template was used carried the same total.
    "Every day", not "every row": a day split into 4h + 4h is a day of 8h here.
    """
    if item.get("hours") is not None:
        return float(item["hours"])
    totals = template.get("dailyTotalHours") or []
    # one day is not a pattern. Deriving from it turned a template used once for an hour
    # into a week of one-hour days, and the shape of the output is the same either way,
    # so nothing in the proposal would look unusual enough to catch it.
    if len(totals) == 1 and template.get("daysUsed", 0) > 1:
        return totals[0]
    if len(totals) == 1:
        raise Clarify([
            "How many hours for %s?" % tempo.format_account(template.get("account")),
            "",
            "It cannot be derived: this template has been used on one day only (%.2fh), "
            "which says nothing about what a usual day looks like." % totals[0],
        ])
    raise Clarify([
        "How many hours for %s?" % tempo.format_account(template.get("account")),
        "",
        "It cannot be derived: this template's daily totals in history are not all the "
        "same (%s), so any single value would be this script's guess."
        % ", ".join("%.2fh" % t for t in totals) if totals else
        "It cannot be derived: this template has no history to derive from.",
    ])


def build_rows(intent, dates, templates_by_id, by_key, required_keys):
    """Produce one candidate row per (date, item), then merge and resolve the action."""
    items = intent.get("items") or []
    if not items:
        raise tempo.ConfigError("items was empty — nothing to propose")

    merged = {}
    order = []
    for date in dates:
        date_text = date.isoformat()
        for item in items:
            template_id = item.get("templateId")
            template = templates_by_id.get(template_id)
            if template is None:
                raise tempo.ConfigError(
                    "template %r is not in the catalogue — it may be from an older "
                    "catalogue, or the id was mistyped. Rebuild the catalogue and pick again."
                    % template_id
                )
            existing = by_key.get((date_text, template_id), [])
            key = (date_text, template_id)
            if key not in merged:
                merged[key] = {"date": date_text, "templateId": template_id,
                               "template": template, "existing": existing, "steps": []}
                order.append(key)
            merged[key]["steps"].append(item)

    rows = []
    for key in order:
        candidate = merged[key]
        rows.extend(_resolve_action(candidate, required_keys))
    return rows


def _resolve_action(candidate, required_keys):
    """Decide CREATE, UPDATE, or no row at all, and verify the model's arithmetic."""
    existing = candidate["existing"]
    template = candidate["template"]
    steps = candidate["steps"]
    date_text = candidate["date"]

    verbs = [s.get("verb") for s in steps if s.get("verb")]
    for verb in verbs:
        if verb not in VERBS:
            raise tempo.ConfigError("verb must be one of %s, got %r" % (", ".join(VERBS), verb))
    if not existing:
        # a verb names a change to something, so a verb against a date holding nothing
        # is a belief about the data that turned out false. SET is the exception:
        # "make Thursday 6 hours" reads the same whether or not Thursday had any.
        if "SUBTRACT" in verbs:
            raise Clarify([
                "Nothing is logged for this template on %s, so there is nothing to "
                "subtract from." % date_text,
                "",
                "Say what you meant: drop this date, or log a fresh row for it.",
            ])
        if "ADD" in verbs:
            raise Clarify([
                "Nothing is logged for this template on %s, so there is nothing to add to."
                % date_text,
                "   %s" % tempo.format_account(template.get("account")),
                "",
                "If you meant to log a fresh row of that many hours, re-run with "
                "verb SET instead of ADD.",
            ])

        if "SET" in verbs:
            # the last SET wins, exactly as it does against an existing row
            value = None
            for step in steps:
                if step.get("verb") == "SET":
                    value = _hours_for(step, template)
            _verify_arithmetic(steps, round(value, 2), 0.0, date_text)
            return [_make_row(candidate, "CREATE", value, None)]

        total = 0.0
        for step in steps:
            total += _hours_for(step, template)
        return [_make_row(candidate, "CREATE", total, None)]

    if not verbs:
        # verified live: Tempo accepts several worklogs for one issue on one date, so an
        # occupied date is no reason to withhold a row. Reading "log N hours" as a change
        # to the existing row would mean choosing between "add N" and "make it N", which
        # differ by the whole amount — so it is taken at face value as a new worklog, and
        # the table shows the existing rows and the day total for the reader to judge.
        total = 0.0
        for step in steps:
            total += _hours_for(step, template)
        return [_make_row(candidate, "CREATE", total, None)]

    # the verb applies to what the date holds for this template, summed. The user asked
    # for the day to come to a number; which records carry it is this script's problem.
    target = existing[0]
    current_seconds = sum(r.get("timeSpentSeconds") or 0 for r in existing)
    current_hours = round(current_seconds / 3600.0, 2)

    # a SET resets the base, and ADD or SUBTRACT after it apply to that new base.
    # the displayed reading is regenerated from the merged result
    # rather than reusing any step's own wording, which described a pre-merge intent.
    running = current_hours
    applied = []
    for step in steps:
        verb = step.get("verb")
        if verb is None:
            continue
        operand = _hours_for(step, template)
        if verb == "SET":
            running = operand
        elif verb == "ADD":
            running += operand
        else:
            running -= operand
        applied.append((verb, operand))

    unrounded = running
    running = round(running, 2)
    _verify_arithmetic(steps, running, current_hours, date_text)
    reading = _reading(current_hours, applied, running)
    # Tempo stores no worklog of zero hours, so reducing one to nothing is a delete.
    # No separate verb says so: the arithmetic already does, and the confirmation table
    # shows it as `N -> 0` before anything is sent.
    # one record carries the result and any others are removed, so the date ends up
    # holding exactly what the arithmetic said.
    # Only a value that was already nothing before rounding deletes. Rounding happens
    # first, so a residue on either side — fourteen seconds is enough — arrives as a
    # clean 0.00 and would otherwise read as a deliberate removal.
    action = "DELETE" if running == 0 and unrounded == 0 else "UPDATE"
    rows = [_make_row(candidate, action, running, target, reading=reading)]
    for extra in existing[1:]:
        folded = _make_row(candidate, "DELETE", 0.0, extra, reading=FOLDED)
        # so a refusal of the row carrying the result reaches the removals it implied
        folded["foldedInto"] = rows[0].get("worklogId")
        # the day's total belongs to the row carrying the result; this one only
        # removes its own record, and showing the day going to zero would be a lie
        folded.pop("dayBeforeSeconds", None)
        rows.append(folded)
    return rows


def _verify_arithmetic(steps, computed, current_hours, date_text):
    """Refuse the proposal when the model's final value disagrees with ours.

    The model's reading of the wording is the part a script cannot do; its
    arithmetic is the part a script can check. Checking it converts an
    uncheckable string into an assertable number.
    """
    claimed = [s.get("finalHours") for s in steps if s.get("finalHours") is not None]
    if not claimed:
        return
    if abs(float(claimed[-1]) - computed) > 0.005:
        raise tempo.ConfigError(
            "the intent says the result for %s is %.2fh, but applying those verbs to the "
            "%.2fh currently logged gives %.2fh — refusing to propose either, because "
            "one of the two readings is wrong and this script cannot tell which. "
            "Re-read the current hours and resend."
            % (date_text, float(claimed[-1]), current_hours, computed)
        )


def _reading(before, applied, after):
    parts = []
    for verb, operand in applied:
        if verb == "SET":
            parts.append("set to %.2fh" % operand)
        elif verb == "ADD":
            parts.append("add %.2fh" % operand)
        else:
            parts.append("subtract %.2fh" % operand)
    # kept short because the Hours column already shows before->after —
    # what it cannot show is which verb produced it, and that is the model's reading
    return "read as: " + " then ".join(parts)


def _make_row(candidate, action, hours, target, reading=None):
    row = {
        "date": candidate["date"],
        "templateId": candidate["templateId"],
        "issueId": candidate["template"]["issueId"],
        "attributes": candidate["template"]["attributes"],
        "account": candidate["template"].get("account"),
        "descriptions": candidate["template"].get("descriptions") or [],
        "action": action,
        "hours": round(hours, 2),
        "seconds": int(round(hours * 3600)),
        "reading": reading,
        "existingCount": len(candidate["existing"]),
        "existingSeconds": sum(r.get("timeSpentSeconds") or 0 for r in candidate["existing"]),
        # what the date held for this template in total, which is what a verb acted on
        # and what the confirmation shows. beforeSeconds stays the single record's value,
        # because that is what apply re-checks before touching it.
        "dayBeforeSeconds": sum(r.get("timeSpentSeconds") or 0 for r in candidate["existing"]),
        "blocked": False,
        "blockedReason": None,
    }
    if target is not None:
        current = target.get("timeSpentSeconds") or 0
        billable = target.get("billableSeconds")
        row["worklogId"] = target.get("tempoWorklogId")
        row["beforeSeconds"] = current
        row["beforeBillableSeconds"] = billable
        row["startTime"] = target.get("startTime")
        row["afterBillableSeconds"], row["sendBillable"] = _billable_after(
            current, billable, row["seconds"])
    return row


def _billable_after(current_seconds, billable_seconds, new_seconds):
    """What billable becomes, and whether apply.py must send it.

    Tempo's behaviour is asymmetric and was verified: on a 1:1 record billable
    follows the hours upward but stays put when they go down, which would leave
    six hours logged and eight billed with no error.

    When the record is NOT 1:1 the field is left alone, because Tempo preserves the
    absolute value and that is exactly the deliberate reduced-billable the user set.
    Scaling it to "protect" it would destroy it — 3600 x 0.25 gives 900 where Tempo
    would have kept 1800.
    """
    if billable_seconds is None:
        return None, False
    if billable_seconds != current_seconds:
        return billable_seconds, False
    if new_seconds < current_seconds:
        return new_seconds, True
    return new_seconds, False


def apply_local_checks(rows, templates_by_id, days_into_future, today, required_keys):
    """Mark rows Tempo would itself refuse. Marked, never dropped.

    Every check here mirrors a refusal Tempo already makes, so this is not the skill
    inventing policy — it turns a request that is certain to fail into a message the
    user can read. A dropped row would instead look to them like their own typo.

    Period closure is deliberately absent: it cannot be read from any endpoint.
    """
    for row in rows:
        template = templates_by_id[row["templateId"]]
        date = tempo.parse_date(row["date"])

        if not template.get("usable"):
            _block(row, template.get("unusableReason") or "template is not usable")
            continue

        missing = [k for k in required_keys if row["attributes"].get(k) is None]
        if missing:
            _block(row, "no value for the required attribute(s) %s" % ", ".join(missing))
            continue

        if row["seconds"] < 1 and row["action"] == "UPDATE":
            # subtracting past zero is an instruction that cannot be carried out, and
            # Tempo's minimum of one second would turn it into an opaque 400
            # an update that overshot into zero or below. Tempo stores no worklog of
            # under a second, and an exact zero would already have become a removal, so
            # anything reaching here is a subtraction that took more than the date held.
            _block(row, "subtracting that much takes the date below nothing, which cannot "
                        "be expressed — subtract what is there, or remove the row")
            continue

        if row["seconds"] < 1 and row["action"] == "CREATE":
            # nothing to create: Tempo stores no worklog of zero hours
            _block(row, "a new row of 0 hours has nothing to create")
            continue

        ahead = (date - today).days
        if days_into_future is not None and ahead > days_into_future:
            _block(row, "the date is %d days ahead, past this tenant's limit of %d "
                        "(numberOfDaysAllowedIntoFuture), so Tempo is expected to refuse it"
                        % (ahead, days_into_future))

    _block_folds(rows)


def _block(row, reason):
    row["blocked"] = True
    row["blockedReason"] = reason


def _block_folds(rows):
    """Block every removal folded into a row that will not be written.

    A removal exists only to reconcile the records behind a result. If the result is
    refused, carrying out its removals destroys a worklog for a change that is not
    happening — and a folded removal has no cell, so the confirmation cannot show it.

    Every other block here is a property of the template or the date, so it catches a
    whole group. The overshoot guard is the first that can catch one row of a group.
    """
    refused = {row.get("worklogId") for row in rows
               if row["blocked"] and row.get("worklogId") is not None}
    for row in rows:
        if row["blocked"] or row.get("foldedInto") not in refused:
            continue
        _block(row, "the row this was folded into cannot be written, so removing this "
                    "one would lose hours for a change that is not happening")


# ---------------------------------------------------------------- rendering

def render(proposal):
    """One table per week, with the day totals and notes beneath each.

    A day's shape is what a reader must notice, so the day it belongs to is a column and
    the totals close the table rather than sitting somewhere to be cross-referenced.
    """
    lines = []
    header = proposal["header"]
    rows = proposal["rows"]
    live = [r for r in rows if not r["blocked"]]

    if header.get("userInput"):
        lines.append(header["userInput"])
        lines.append("")

    lines.append(header["summary"])
    for note in header["notes"]:
        lines.append(note)

    groups = _date_groups(proposal)
    body = _body(groups)

    lines.append("")
    lines.extend(body)

    if header["bigProposal"]:
        lines.append("")
        lines.extend(header["bigProposal"])

    if not live:
        lines.append("")
        lines.append("No row can be written, so there is nothing to confirm.")

    return "\n".join(lines)


def _date_groups(proposal):
    """Group proposed and existing rows by date, every date, in order."""
    rows = proposal["rows"]
    existing_by_date = {}
    for existing in proposal["header"]["existingRows"]:
        existing_by_date.setdefault(existing["date"], []).append(existing)

    dates = sorted({r["date"] for r in rows} | set(existing_by_date))
    groups = []
    for date_text in dates:
        groups.append((date_text, [r for r in rows if r["date"] == date_text],
                       existing_by_date.get(date_text, [])))
    return groups


def _disambiguate(labels):
    """Add what separates two work items whose labels came out identical.

    An internal id is noise on a table a person reads, so nothing extra is shown while
    the labels already differ. What can make them collide is not one predictable thing:
    a project account carries two issue ids, or two work items share an account and an
    issue and differ only in a second required attribute. Detecting the collision covers
    both, and anything else that produces one.

    `labels` maps identity -> text, and the identity is (issue id, attribute values), so
    it holds exactly what the reader needs to tell the pair apart.
    """
    seen = {}
    for item, text in labels.items():
        seen.setdefault(text, []).append(item)

    out = dict(labels)
    for text, items in seen.items():
        if len(items) < 2:
            continue
        issues = {issue for issue, _attributes in items}
        for issue, attributes in items:
            if len(issues) > 1:
                out[(issue, attributes)] = "%s · issue %s" % (text, issue)
            else:
                # only the attributes that actually differ across the colliding
                # set: the rest are already in the label, and repeating them
                # buries the one value the reader is comparing
                varying = {key for key, _value in attributes
                           if len({dict(other).get(key)
                                   for _i, other in items}) > 1}
                shown = ", ".join(str(value) for key, value in attributes
                                  if key in varying)
                out[(issue, attributes)] = "%s · %s" % (text, shown)
    return out


def _hours(seconds):
    return "%.2f" % ((seconds or 0) / 3600.0)


# Tempo generates a worklog's description from the issue, so the Jira key is in there
# and nowhere else the reader sees: the row label is a project account, which is a
# different vocabulary from the key someone names when they ask for work to be logged.
JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]*-[0-9]+\b")


def _jira_key(descriptions):
    for description in descriptions or []:
        found = JIRA_KEY.search(description or "")
        if found:
            return found.group(0)
    return None


def _cell_text(row):
    """What an item comes to on a day: `+N` from nothing, `before->after` from something."""
    before = row.get("dayBeforeSeconds")
    if before is None:
        before = row.get("existingSeconds") or 0
    if row["action"] == "CREATE":
        after = before + row["seconds"]
    else:
        after = row["seconds"]
    if before:
        return "%s->%s" % (_hours(before), _hours(after))
    return "+" + _hours(after)


def _stays(row):
    """What the date keeps for that item when a row is not written.

    A blocked row changes nothing, and the worklog it would have changed is still there.
    Contributing zero instead dropped those hours from the day total, because a blocked
    cell occupies the slot its already-logged row would otherwise have taken.
    """
    before = row.get("dayBeforeSeconds")
    return before if before is not None else (row.get("existingSeconds") or 0)


def _cell_after(row):
    """The seconds the day holds for that item once this row lands.

    A blocked row contributes nothing: it will not be written, and counting what the date
    holds today made the total run HIGHER than the cells suggested, which is the opposite
    of what the confirmation says to expect.
    """
    if row["blocked"]:
        return _stays(row)
    if row["action"] == "CREATE":
        before = row.get("dayBeforeSeconds")
        if before is None:
            before = row.get("existingSeconds") or 0
        return before + row["seconds"]
    return row["seconds"]


def _week_of(date):
    return date - datetime.timedelta(days=date.weekday())


def _grid(groups):
    """Per week: {(date, item): cell}, the item order, and the notes each cell carries.

    Cells key on the work item rather than on its rendered label. Keying on the label let
    two items whose labels happened to match overwrite each other, so one disappeared from
    the table and from the day total while still being written.
    """
    every_row = [r for _d, proposed, existing in groups
                 for r in list(proposed) + list(existing)]

    # one key per issue id, taken from whichever rows carry it. Reading each row's own
    # description let an edited one disagree with its siblings and split the work item
    # into two rows, and Tempo's own description is what the key is scraped from either
    # way — there is no endpoint that returns it.
    key_by_issue = {}
    for row in every_row:
        issue_id = row.get("issueId")
        if issue_id is None or issue_id in key_by_issue:
            continue
        jira = _jira_key(row.get("descriptions"))
        if jira:
            key_by_issue[issue_id] = jira

    def identity(row):
        """What identifies a work item: its issue and every required attribute value.

        The account alone is too coarse. It is the only required attribute on the tenant
        this was built against, so the two coincide there — but where a second one is
        required, two templates differing only in it share an account and an issue, and a
        cell keyed on those merged them: one lost its hours from the table and the day
        total while still being written.
        """
        attributes = row.get("attributes") or {}
        return (row.get("issueId"), tuple(sorted(attributes.items())))

    def label_for(row):
        text = tempo.format_account(row.get("account"))
        jira = key_by_issue.get(row.get("issueId"))
        if jira:
            text += " (%s)" % jira
        return text

    weeks = {}
    labels = {}
    order = []
    for date_text, proposed, existing in groups:
        date = tempo.parse_date(date_text)
        week = weeks.setdefault(_week_of(date), {})
        # a cell a proposed row owns already states what the day comes to for that item,
        # so an existing row must not land on top of it -- but existing rows have to add
        # up among themselves, which is a different question
        proposed_cells = set()
        for row in proposed:
            # a fold only removes a record the primary row has already accounted for
            if row.get("reading") == FOLDED:
                continue
            item = identity(row)
            if item not in labels:
                labels[item] = label_for(row)
                order.append(item)
            week[(date, item)] = (_cell_text(row), _cell_after(row),
                                  row["blockedReason"], list(row["annotations"]))
            proposed_cells.add((date, item))
        for row in existing:
            item = identity(row)
            if item not in labels:
                labels[item] = label_for(row)
                order.append(item)
            key = (date, item)
            if key in proposed_cells:
                continue
            # Tempo accepts several worklogs for one item on one date, and a cell is the
            # item's total for the day. Keeping only the first understated both the cell
            # and the day total, which is the direction that makes someone log it again.
            if key in week:
                _text, seconds, blocked, notes = week[key]
                seconds += row["seconds"]
                week[key] = (_hours(seconds), seconds, blocked, notes)
            else:
                week[key] = (_hours(row["seconds"]), row["seconds"], None, [])
    return weeks, _disambiguate(labels), order


def _body(groups):
    """One table per week: days across, work items down, day totals and notes beneath."""
    weeks, labels, order = _grid(groups)
    lines = []

    for monday in sorted(weeks):
        week = weeks[monday]
        days = [monday + datetime.timedelta(days=i) for i in range(7)]
        # weekend columns only when the weekend actually carries something
        days = [d for d in days
                if d.weekday() < 5 or any((d, item) in week for item in order)]
        present = [item for item in order if any((d, item) in week for d in days)]
        if not present:
            continue

        heading = "week of %s" % monday.isoformat()
        width = max([len(heading), len("day total")]
                    + [len(labels[item]) for item in present])
        # the columns are as wide as their widest cell: "8.00->16.00" is eleven
        # characters and a fixed width silently pushed the whole row out of line
        cell_width = len("Mon 31")
        for item in present:
            for date in days:
                text, _seconds, blocked, _notes = week.get((date, item), ("", 0, None, []))
                cell_width = max(cell_width, len(text) + (1 if blocked else 0))
        head = "  %-*s" % (width, heading)
        for date in days:
            head += "  %*s" % (cell_width, date.strftime("%a %d"))
        lines.append(head)
        lines.append("  " + "-" * (len(head) - 2))

        notes = []
        for item in present:
            line = "  %-*s" % (width, labels[item])
            for date in days:
                text, _seconds, blocked, cell_notes = week.get((date, item),
                                                               ("", 0, None, []))
                if blocked:
                    text += "!"
                    notes.append((date, labels[item], "NOT WRITEABLE: %s" % blocked))
                for note in cell_notes:
                    notes.append((date, labels[item], note))
                line += "  %*s" % (cell_width, text)
            lines.append(line.rstrip())

        totals = "  %-*s" % (width, "day total")
        for date in days:
            in_proposal = any((date, item) in week for item in present)
            seconds = sum(week[(date, item)][1] for item in present
                          if (date, item) in week)
            # an explicit 0.00 where the date is in the proposal and comes to nothing,
            # because a blank there reads exactly like a date nobody asked about
            totals += "  %*s" % (cell_width, _hours(seconds) if in_proposal else "")
        lines.append(totals.rstrip())

        # under the week they belong to. Printing every week's notes after the last
        # table put week one's reasons below week four.
        for date, label, note in notes:
            lines.append("  %s %s \u2014 %s" % (date.isoformat(), label, note))
        lines.append("")

    while lines and not lines[-1]:
        lines.pop()
    return lines


def annotate(rows, by_date, today, accounts_by_key, required_keys):
    """Attach each row's Notes cell as short tokens. Facts only, never comparisons.

    Nothing here says "you usually log 8 hours" or "this is higher than normal", and
    nothing treats any number as a full or maximum day. The user knows their own
    normal; the skill does not, and must not pretend to.

    Only what the table cannot say itself survives here. A note repeating the cell
    beside it -- that the day already holds hours of this item, what the verb was read
    as, what billable comes to -- is read past rather than read, and it pushed the one
    note that changes the outcome down a list of four.
    """
    current_month = (today.year, today.month)
    for row in rows:
        notes = []
        date = tempo.parse_date(row["date"])

        # neither `future` nor `weekend` is a per-row fact: every row on a date shares
        # it, the calendar puts Saturday and Sunday in their own columns, and the
        # header already counts the future rows against today's date. Repeating them
        # per row only split blocks that were otherwise identical.
        if (date.year, date.month) < current_month:
            notes.append("month may be closed")

        row["annotations"] = notes


def _account_of(row, accounts_by_key, required_keys):
    values = {
        value["key"]: value.get("value")
        for value in ((row.get("attributes") or {}).get("values") or [])
    }
    for key in required_keys:
        value = values.get(key)
        if value is None:
            continue
        return accounts_by_key.get(value) or {"key": value, "name": None, "status": None}
    return None


def build_header(intent, rows, dates, excluded, today, config, by_date, by_key,
                 accounts_by_key, required_keys):
    live = [r for r in rows if not r["blocked"]]
    future = [r for r in rows if tempo.parse_date(r["date"]) > today]

    span = "%s to %s" % (min(dates).isoformat(), max(dates).isoformat())
    summary = "%s — %d row%s across %d date%s" % (
        span, len(rows), "" if len(rows) == 1 else "s",
        len(dates), "" if len(dates) == 1 else "s")

    notes = []
    if excluded:
        grouped = {}
        for date, reason in excluded:
            grouped.setdefault(reason, []).append(date)
        for reason in sorted(grouped):
            days = sorted(grouped[reason])
            # every excluded date, uncapped: an exclusion the reader cannot see is a
            # silently dropped row, and a vague month excludes eight or ten weekend days
            listed = ", ".join("%s %s" % (d.strftime("%m-%d"), tempo.weekday_name(d))
                               for d in days)
            notes.append("Excluded (%s): %d day%s (%s)"
                         % (reason, len(days), "" if len(days) == 1 else "s", listed))
    # the future count is printed even for an explicit range, and especially there:
    # nothing filters an explicit range, so a mistyped year hides inside a date
    # string that looks perfectly normal.
    notes.append("%d row%s %s a future date (today is %s)"
                 % (len(future), "" if len(future) == 1 else "s",
                    "is" if len(future) == 1 else "are on",
                    today.isoformat()))
    blocked = [r for r in rows if r["blocked"]]
    if blocked:
        notes.append("%d row%s cannot be written and %s marked below"
                     % (len(blocked), "" if len(blocked) == 1 else "s",
                        "is" if len(blocked) == 1 else "are"))

    existing_rows = []
    # only the worklog a row is about to update is left out, because that one is
    # already in the table as the UPDATE row. Excluding every row that merely SHARES
    # its (date, template) hid a sibling worklog and, with it, the date's day total —
    # so a confirmed change to 6h could land 10h with nothing on the table to show it.
    # only a LIVE update's worklog is left out.
    # A blocked one is never written, so its row is still what the date holds,
    # and hiding it would drop those hours from the day total too —
    # understating instead of overstating, which reads as correct.
    updating = {r.get("worklogId") for r in rows
                if r.get("worklogId") and not r["blocked"]}
    for (date_text, _template_id), group in sorted(by_key.items()):
        if date_text not in {d.isoformat() for d in dates}:
            continue
        for existing in group:
            if existing.get("tempoWorklogId") in updating:
                continue
            existing_rows.append({
                "date": date_text,
                "weekday": tempo.weekday_name(tempo.parse_date(date_text)),
                "seconds": existing.get("timeSpentSeconds"),
                "worklogId": existing.get("tempoWorklogId"),
                "account": _account_of(existing, accounts_by_key, required_keys),
                # the issue and its description, so an already-logged row resolves to the
                # same Jira key as a proposed one and the two share a table row
                "issueId": (existing.get("issue") or {}).get("id"),
                # the required attribute values too, because those and the issue are what
                # identify a work item. Keying a cell on the account alone merged two
                # templates that differ only in a second required attribute.
                "attributes": {
                    key: value.get("value")
                    for value in ((existing.get("attributes") or {}).get("values") or [])
                    for key in [value.get("key")] if key in required_keys
                },
                "descriptions": [existing.get("description")],
            })

    big = []
    if len(live) > BULK_MAX_ITEMS:
        current_month = (today.year, today.month)
        older = [r for r in live if (tempo.parse_date(r["date"]).year,
                                     tempo.parse_date(r["date"]).month) < current_month]
        creating = len([r for r in live if r["action"] == "CREATE"])
        removing = len([r for r in live if r["action"] == "DELETE"])
        changing = len(live) - creating - removing
        # every live row is attempted: closure cannot be read from any endpoint, so
        # nothing suppresses the older ones. Asking about a smaller number would be
        # asking the user to approve less than the run will do.
        shape = []
        if creating:
            shape.append("%d new" % creating)
        if changing:
            shape.append("%d changed" % changing)
        if removing:
            shape.append("%d removed" % removing)
        big = [
            "%d rows will be attempted (%s), over %s." % (len(live), ", ".join(shape), span),
        ]
        if older:
            big.append(
                "  %d of them fall in months before the current one and may be refused "
                "as closed — that cannot be known until the write." % len(older))
        big += [
            "",
            "Confirm writing these %d rows?" % len(live),
            "This question needs its own answer; confirming the row table above is not "
            "an answer to it.",
        ]

    return {
        "userInput": intent.get("userInput"),
        "summary": summary,
        "notes": notes,
        "existingRows": existing_rows,
        "bigProposal": big,
    }


# ---------------------------------------------------------------- orchestration

def build_proposal(intent, config=None, token=None, today=None):
    config = config or tempo.load_config()
    token = token or tempo.load_token()
    today = today or datetime.date.today()

    author_account_id, _ = tempo.resolve_author_account_id(config, token)
    built = catalogue.build(int(intent.get("catalogueWeeks") or 8), config, token, today)
    templates_by_id = {t["id"]: t for t in built["templates"]}
    required_keys = [a["key"] for a in built["requiredAttributes"]]
    accounts_by_key = catalogue._accounts_by_key(config, token)

    dates, excluded, date_shape = resolve_dates(
        intent, author_account_id, built["weekendHistory"], config, token)
    if not dates:
        raise tempo.ConfigError(
            "every date in the range was excluded, so there is nothing to propose"
        )

    snapshot = read_snapshot(dates, author_account_id, config, token)
    by_date, by_key = _index_snapshot(snapshot, required_keys)

    rows = build_rows(intent, dates, templates_by_id, by_key, required_keys)

    global_config = tempo.read("/4/globalconfiguration", None, config, token)
    apply_local_checks(rows, templates_by_id,
                       global_config.get("numberOfDaysAllowedIntoFuture"),
                       today, required_keys)
    annotate(rows, by_date, today, accounts_by_key, required_keys)

    header = build_header(intent, rows, dates, excluded, today, config,
                          by_date, by_key, accounts_by_key, required_keys)

    proposal = {
        # to the microsecond, because apply compares ids as strings to decide which
        # proposal is newer: at one-second resolution two proposals made in the same
        # second ordered by their random suffix, and the newer one could lose
        "id": (datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
               + "-" + uuid.uuid4().hex[:6]),
        "createdAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "authorAccountId": author_account_id,
        "range": {"from": min(dates).isoformat(), "to": max(dates).isoformat()},
        "requiredAttributes": required_keys,
        "defaultStartTime": config.get("defaultStartTime") or "08:00:00",
        "intent": intent,
        "rows": rows,
        "header": header,
    }
    proposal["rendered"] = render(proposal)
    return proposal


def save(proposal):
    if not os.path.isdir(PROPOSAL_DIR):
        os.makedirs(PROPOSAL_DIR)
    # under the user's state directory rather than the repo: a proposal carries real
    # issue ids, project account keys and the author account id,
    # and a file in the working tree eventually gets swept up by git add -A.
    path = os.path.join(PROPOSAL_DIR, proposal["id"] + ".json")
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n")
    return path


# ---------------------------------------------------------------- cli entry

def _main(argv):
    parser = argparse.ArgumentParser(description="Build a Tempo worklog proposal.")
    parser.add_argument("--intent", required=True, help="path to the intent JSON")
    arguments = parser.parse_args(argv[1:])

    try:
        with io.open(arguments.intent, encoding="utf-8") as handle:
            intent = json.load(handle)
    except (IOError, OSError) as error:
        sys.stderr.write("cannot read intent: %s\n" % error)
        return EXIT_USAGE
    except ValueError as error:
        sys.stderr.write("intent is not valid JSON: %s\n" % error)
        return EXIT_USAGE

    try:
        proposal = build_proposal(intent)
    except Clarify as clarification:
        sys.stdout.write("status: needs-clarification\n\n")
        sys.stdout.write("\n".join(clarification.lines) + "\n")
        return EXIT_NEEDS_CLARIFICATION
    except (tempo.ConfigError, tempo.TempoError) as error:
        sys.stderr.write(str(error) + "\n")
        return EXIT_ERROR

    path = save(proposal)
    sys.stdout.write("status: proposal\nproposal: %s\nfile: %s\n\n"
                     % (proposal["id"], path))
    sys.stdout.write(proposal["rendered"] + "\n")
    return EXIT_PROPOSAL


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
