"""Execute a proposal: the only file that calls a data-changing endpoint.

Everything else in this skill reads. That is enforced rather than agreed —
tempo.py splits read and write into two interfaces that each refuse the other's
endpoints, and the data-changing endpoints are whitelisted there by name.

Three things drive the shape of this file.

Preconditions are checked against one snapshot taken when apply starts, not with a
fetch per row. A 22-day month is then one read plus one bulk write instead of 44
sequential calls.

Failures are handled by how many rows they affect, not by status code. A closed
period and a closed project account are both 403 and their correct handling is
opposite, so the branch is on Tempo's own message.

Every row's outcome is appended and flushed as it happens. This script can be cut
off by a tool timeout, and the rows already written must survive that.

bypassPeriodClosuresAndApprovals is not implemented here at all, which is what makes
"never bypass a closed period automatically" a fact rather than an instruction.
"""

import argparse
import datetime
import io
import json
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalogue  # noqa: E402
import tempo  # noqa: E402

RESULT_DIR = os.path.join(tempo.STATE_DIR, "results")
BULK_MAX_ITEMS = 50

WRITE_RETRY_ATTEMPTS = 4
WRITE_RETRY_BASE_SECONDS = 1.0

WRITTEN = "written"
SKIPPED_PRECONDITION = "skipped-precondition"
SKIPPED_PROPAGATED = "skipped-propagated"
FAILED = "failed"
UNTRIED = "untried"

EXIT_ALL_WRITTEN = 0
EXIT_PARTIAL = 1
EXIT_GLOBAL_STOP = 2
EXIT_USAGE = 3
# its own code, because "the id is superseded" and "the id does not exist" need
# opposite responses and sharing exit 3 made them indistinguishable
EXIT_SUPERSEDED = 4

# the user-settable fields an UPDATE's precondition covers.
# description is excluded because Tempo derives it from issueId, and
# startDateTimeUtc because it is derived from startDate plus startTime.
# startTime IS included: it is a field the user can see and set in the UI,
# so a value they chose there must not be silently overwritten.
UPDATE_GUARDED_FIELDS = ("startDate", "timeSpentSeconds", "billableSeconds", "startTime")

# marks a row the proposal already showed as unwriteable,
# so the result text can tell that apart from a precondition that went stale afterwards
BLOCKED_AT_PLAN = "blocked when the proposal was made: "

# the contract this script publishes; see setup.py's note.
EXIT_MEANINGS = (
    (EXIT_ALL_WRITTEN, "every row written"),
    (EXIT_PARTIAL, "at least one row was not written, or the run failed before starting"),
    (EXIT_GLOBAL_STOP, "stopped part-way, or nothing was sent; untried rows exist"),
    (EXIT_USAGE, "refused to run: the id was not found, or its file is not valid JSON"),
    (EXIT_SUPERSEDED, "refused to run: a newer proposal shares a date with this one"),
)

ROW_STATUS_MEANINGS = (
    (WRITTEN, "written to Tempo"),
    (SKIPPED_PRECONDITION, "not sent: the row changed under the proposal, or was already unwriteable"),
    (SKIPPED_PROPAGATED, "not sent, and NOT recorded anywhere: an earlier failure covered it"),
    (FAILED, "sent and refused; the reason carries Tempo's own message"),
    (UNTRIED, "never sent"),
)


class Superseded(tempo.ConfigError):
    """A newer proposal over overlapping dates has replaced this one."""


class GlobalStop(Exception):
    """A failure that makes every remaining row unsafe or pointless to attempt."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- result recording

class Results(object):
    """Append-and-flush per row, because a tool timeout can cut this process off."""

    def __init__(self, proposal_id):
        if not os.path.isdir(RESULT_DIR):
            os.makedirs(RESULT_DIR)
        self.path = os.path.join(RESULT_DIR, proposal_id + ".jsonl")
        self.rows = []
        # the file is appended across runs so a run cut short still leaves a record,
        # which means one row can appear several times. A run id makes the entries
        # attributable, so a reader can take the latest run rather than counting them all.
        self.run = datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.handle = io.open(self.path, "a", encoding="utf-8")

    @staticmethod
    def previous(proposal_id):
        """Rows recorded by earlier runs of this proposal, latest run per row.

        Used so a re-run can tell "someone else changed this" from "we wrote it
        ourselves last time and were cut off before reporting it".
        """
        path = os.path.join(RESULT_DIR, proposal_id + ".jsonl")
        if not os.path.exists(path):
            return {}
        latest = {}
        with io.open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                key = _row_key(entry)
                if key not in latest or entry.get("at", "") >= latest[key].get("at", ""):
                    latest[key] = entry
        return latest

    def record(self, row, status, reason=None, worklog_id=None):
        entry = {
            "date": row["date"],
            "templateId": row["templateId"],
            "issueId": row["issueId"],
            "accountKey": (row.get("account") or {}).get("key"),
            "hours": row["hours"],
            "action": row["action"],
            "status": status,
            "reason": reason,
            "worklogId": worklog_id or row.get("worklogId"),
            "run": self.run,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self.rows.append(entry)
        self.handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.handle.flush()
        return entry

    def close(self):
        self.handle.close()


# ---------------------------------------------------------------- preconditions

def _index(rows, required_keys):
    by_key = {}
    by_id = {}
    for row in rows:
        issue_id = (row.get("issue") or {}).get("id")
        values = {
            value["key"]: value.get("value")
            for value in ((row.get("attributes") or {}).get("values") or [])
        }
        attributes = {key: values.get(key) for key in required_keys}
        template = catalogue.template_id(issue_id, attributes)
        by_key.setdefault((row.get("startDate"), template), []).append(row)
        by_id[row.get("tempoWorklogId")] = row
    return by_key, by_id


def _row_key(row):
    """What identifies a row in the result record.

    The worklog id joins the key for an update or a removal, because a verb acting on the
    day's total emits one removal per surplus record: two of them share a date, a template
    and an action, and without the id the second was never recorded as untried when a run
    stopped between them.

    It is deliberately left out for a create. A proposed create names no worklog and the
    recorded one names the id Tempo assigned, so including it would put the two under
    different keys and defeat the re-run recognition that reads them back.
    """
    worklog_id = None if row["action"] == "CREATE" else row.get("worklogId")
    return (row["date"], row["templateId"], row["action"], worklog_id)


def _is_our_earlier_write(row, mine, by_id):
    """Whether the precondition failed because an earlier run of THIS proposal wrote it.

    The record file is appended across runs precisely so a run cut short leaves one,
    and the documented recovery is to re-run.
    """
    if not mine or mine.get("status") != WRITTEN:
        return False
    if row["action"] == "CREATE":
        if mine.get("worklogId") is not None:
            return mine.get("worklogId") in by_id
        # written, but the bulk response held no row matching this one, so no id was
        # captured and there is nothing to look up. The record is this tool's own
        # writing: if it says the row landed, reporting it as not landed is the one
        # direction that makes a user log the same hours twice.
        return True
    if row["action"] == "DELETE":
        # the worklog is no longer there, which is exactly what the row asked for
        return row.get("worklogId") not in by_id

    # an update that landed leaves the worklog holding exactly what we meant to write,
    # which is what the guarded-field check then reports as a change under us.
    target = by_id.get(row.get("worklogId"))
    if target is None:
        return False
    if (target.get("timeSpentSeconds") or 0) != row["seconds"]:
        return False
    billable = row.get("afterBillableSeconds")
    return billable is None or (target.get("billableSeconds") or 0) == billable


def check_precondition(row, by_key, by_id, required_keys):
    """Return None when the row may proceed, or the reason it may not.

    The rule is that everything the user can set must still match — not merely
    everything the confirmation table displayed. A tenant with several required
    attributes may not show them all, and they still have to be verified.
    """
    if row["action"] == "CREATE":
        # a create is deliberately proposed onto an occupied date — Tempo accepts several
        # worklogs for one issue on one date, and the proposal showed the reader what the
        # date already held. So the question is whether the date still looks the way it
        # did, not whether it is empty: refusing on mere existence refused every row the
        # design was changed to produce.
        existing = by_key.get((row["date"], row["templateId"]), [])
        seen = row.get("existingCount")
        if seen is None:
            seen = 0
        if len(existing) != seen:
            return ("%s now holds %d worklog(s) for this template, but the proposal was "
                    "made against %d — the date changed after it was confirmed"
                    % (row["date"], len(existing), seen))
        return None

    target = by_id.get(row.get("worklogId"))
    if target is None:
        # deleted or moved out of range are not distinguished, because both mean
        # the same thing here: do not touch it.
        return ("%s is no longer in the range — it was deleted or moved"
                % tempo.format_worklog_ref(row.get("worklogId")))

    proposed = {
        "startDate": row["date"],
        "timeSpentSeconds": row.get("beforeSeconds"),
        "billableSeconds": row.get("beforeBillableSeconds"),
        "startTime": row.get("startTime"),
    }
    for field in UPDATE_GUARDED_FIELDS:
        current = target.get(field)
        if proposed[field] != current:
            return ("%s changed since the proposal: was %r, now %r"
                    % (field, proposed[field], current))

    current_issue = (target.get("issue") or {}).get("id")
    if current_issue != row["issueId"]:
        return "issue changed since the proposal: was %r, now %r" % (row["issueId"], current_issue)

    values = {
        value["key"]: value.get("value")
        for value in ((target.get("attributes") or {}).get("values") or [])
    }
    for key in required_keys:
        if row["attributes"].get(key) != values.get(key):
            return ("attribute %s changed since the proposal: was %r, now %r"
                    % (key, row["attributes"].get(key), values.get(key)))
    return None


# ---------------------------------------------------------------- start time

def assign_start_times(rows, snapshot_by_date, default_start_time):
    """Give every CREATE row a startTime, chaining within each day.

    Sending it is not optional. Omitted, Tempo stores 00:00:00, and local midnight
    converted to UTC lands on the PREVIOUS day (verified: a row for 2026-09-09
    came back stamped on the previous day in UTC), so anything downstream that
    buckets by startDateTimeUtc counts the whole day against the day before.

    The first row of a day starts after whatever the day already holds, and each
    later row starts where the previous one ended, which is what the UI does and
    what keeps two rows on one day from overlapping.
    """
    base = _parse_time(default_start_time)
    for date_text in sorted({r["date"] for r in rows if r["action"] == "CREATE"}):
        cursor = base
        for existing in snapshot_by_date.get(date_text, []):
            start = _parse_time(existing.get("startTime") or "00:00:00")
            end = start + (existing.get("timeSpentSeconds") or 0)
            cursor = max(cursor, end)
        for row in [r for r in rows if r["date"] == date_text and r["action"] == "CREATE"]:
            row["startTime"] = _format_time(cursor)
            cursor += row["seconds"]


def _parse_time(text):
    parts = [int(p) for p in (text or "00:00:00").split(":")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _format_time(seconds):
    # a day that overruns is clamped rather than wrapped, because wrapping past
    # midnight would move the row onto another date without saying so.
    seconds = min(seconds, 23 * 3600 + 59 * 60)
    return "%02d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


# ---------------------------------------------------------------- payloads

def create_payload(row, author_account_id):
    """Only the fields the design says to send.

    billableSeconds is omitted: Tempo fills it to match the hours on a create,
    which is verified, so sending it adds a second source of truth for nothing.
    description is omitted because Tempo generates it from the issue.
    remainingEstimateSeconds is omitted, which is how "do not touch the estimate"
    is expressed.
    """
    return {
        "authorAccountId": author_account_id,
        "startDate": row["date"],
        "startTime": row["startTime"],
        "timeSpentSeconds": row["seconds"],
        "attributes": [{"key": key, "value": value}
                       for key, value in sorted(row["attributes"].items())
                       if value is not None],
    }


def update_payload(row, author_account_id):
    """An update sends the hours, and billable ONLY when Tempo would leave it wrong.

    On a 1:1 record billable follows the hours up but not down, so a decrease
    without this field would record six hours logged and eight billed, silently.
    On a record that is not 1:1 the field is left out, because Tempo preserves the
    absolute value and that is the deliberate reduced-billable the user set.

    startTime is omitted so the value the user may have chosen is preserved, and
    because omitting it is also what keeps the UTC shift from happening on updates.
    """
    payload = {
        "authorAccountId": author_account_id,
        "startDate": row["date"],
        "timeSpentSeconds": row["seconds"],
    }
    if row.get("sendBillable"):
        payload["billableSeconds"] = row["afterBillableSeconds"]
    return payload


# ---------------------------------------------------------------- execution

def run(proposal, config=None, token=None):
    config = config or tempo.load_config()
    token = token or tempo.load_token()
    author_account_id = proposal["authorAccountId"]
    required_keys = proposal["requiredAttributes"]

    live = [r for r in proposal["rows"] if not r["blocked"]]
    blocked = [r for r in proposal["rows"] if r["blocked"]]
    results = Results(proposal["id"])
    for row in blocked:
        results.record(row, SKIPPED_PRECONDITION,
                       BLOCKED_AT_PLAN + (row["blockedReason"] or ""))

    stop_reason = None
    try:
        snapshot = tempo.read_all(
            "/4/worklogs/user/" + author_account_id,
            {"from": proposal["range"]["from"], "to": proposal["range"]["to"], "limit": 100},
            config, token,
        )
    except tempo.TempoError as error:
        # a truncated snapshot is a global failure and nothing is written: the missing
        # days would look empty, every create precondition would pass,
        # and the write would duplicate hours on days that already have them.
        for row in live:
            results.record(row, UNTRIED, "snapshot could not be read: %s" % error.message)
        results.close()
        return results, "snapshot could not be read: %s" % error.message

    by_key, by_id = _index(snapshot, required_keys)
    snapshot_by_date = {}
    for existing in snapshot:
        snapshot_by_date.setdefault(existing.get("startDate"), []).append(existing)

    earlier = Results.previous(proposal["id"])
    ready = []
    for row in live:
        reason = check_precondition(row, by_key, by_id, required_keys)
        if reason:
            # a create whose slot is now full may be OUR OWN earlier write: this file is
            # appended across runs precisely so a run cut short leaves a record, and the
            # documented recovery is to re-run. Reporting that as a precondition failure
            # tells the user hours were not written when they were — the one direction
            # that makes them log the same hours twice.
            mine = earlier.get(_row_key(row))
            if _is_our_earlier_write(row, mine, by_id):
                results.record(row, WRITTEN,
                               "already written by run %s of this proposal; left as it is"
                               % mine.get("run", "(unknown)"),
                               worklog_id=mine.get("worklogId"))
                continue
            results.record(row, SKIPPED_PRECONDITION, reason)
        else:
            ready.append(row)

    assign_start_times(ready, snapshot_by_date, proposal["defaultStartTime"])

    state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
    try:
        _write_updates([r for r in ready if r["action"] == "UPDATE"],
                       author_account_id, results, state, config, token)
        _write_deletes([r for r in ready if r["action"] == "DELETE"],
                       results, state, config, token)
        _write_creates([r for r in ready if r["action"] == "CREATE"],
                       author_account_id, results, state, config, token, required_keys)
    except GlobalStop as stop:
        stop_reason = stop.reason
        done = {_row_key(e) for e in results.rows}
        for row in ready:
            if _row_key(row) not in done:
                results.record(row, UNTRIED, stop_reason)

    results.close()
    return results, stop_reason


def _group_reason(row, state):
    """Whether an earlier failure already covers this row, and the wording for it."""
    month = row["date"][:7]
    if month in state["closedMonths"]:
        # phrased as an inference, because the closure unit being the calendar month
        # rests on three dates in one tenant and one month, not on documentation.
        return ("a row in %s was refused because the period is closed, so the "
                "remaining rows in that month were not attempted" % month)
    account_key = (row.get("account") or {}).get("key")
    if account_key and account_key in state["closedAccounts"]:
        return ("project account %s was refused as closed or archived, so the remaining "
                "rows using it were not attempted — the catalogue needs rebuilding"
                % account_key)
    return None


def _handle_failure(error, row, state):
    """Record the failure's reach in `state`, or raise for a global one."""
    if error.scope == tempo.Scope.GROUP_MONTH:
        state["closedMonths"].add(row["date"][:7])
        return ("period is closed: %s" % error.message)
    if error.scope == tempo.Scope.GROUP_ACCOUNT:
        account_key = (row.get("account") or {}).get("key")
        if account_key:
            # keyed on the project account, not the template: one account can be paired
            # with several issue ids, and keying on the template would let every other
            # template's rows each hit the same 403 again.
            state["closedAccounts"].add(account_key)
        return ("project account is closed or archived: %s" % error.message)
    if error.scope == tempo.Scope.GLOBAL:
        raise GlobalStop("%s %s: %s" % (error.status, row["date"], error.message))
    return "%s: %s" % (error.status, error.message)


def _write_updates(rows, author_account_id, results, state, config, token):
    for row in rows:
        covered = _group_reason(row, state)
        if covered:
            results.record(row, SKIPPED_PROPAGATED, covered)
            continue
        try:
            _put_with_retry(row, author_account_id, config, token)
            results.record(row, WRITTEN, None)
        except tempo.TempoError as error:
            # this row was sent and refused, so it is `failed` whatever the scope.
            # _handle_failure still records the scope, so LATER rows are propagated.
            _record_before_global_stop(error, row, results)
            reason = _handle_failure(error, row, state)
            results.record(row, FAILED, reason)


def _write_deletes(rows, results, state, config, token):
    """Remove a worklog outright, which is what reducing it to zero hours means.

    Retried on 429 without a guard, like an update: deleting a worklog that is already
    gone is the state the caller asked for, so a repeat cannot do harm.
    """
    for row in rows:
        covered = _group_reason(row, state)
        if covered:
            results.record(row, SKIPPED_PROPAGATED, covered)
            continue
        try:
            _delete_with_retry(row, config, token)
            results.record(row, WRITTEN, "removed", worklog_id=row.get("worklogId"))
        except tempo.TempoError as error:
            _record_before_global_stop(error, row, results)
            reason = _handle_failure(error, row, state)
            results.record(row, FAILED, reason)


def _record_before_global_stop(error, row, results):
    """Record a sent-and-refused row as `failed` before a global refusal stops the run.

    _handle_failure raises GlobalStop for a global scope, and run()'s sweep marks every
    row without a record `untried` — which would report a row that was sent as never
    attempted, the same defect _send_bulk already guards against for creates.
    """
    if error.scope == tempo.Scope.GLOBAL:
        results.record(row, FAILED, "%s: %s" % (error.status, error.message))


def _delete_with_retry(row, config, token):
    delay = WRITE_RETRY_BASE_SECONDS
    for attempt in range(WRITE_RETRY_ATTEMPTS):
        try:
            return tempo.write("DELETE", "/4/worklogs/%s" % row["worklogId"],
                               None, config, token)
        except tempo.TempoError as error:
            if error.scope != tempo.Scope.RETRYABLE or attempt == WRITE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(delay + random.uniform(0, delay / 2))
            delay *= 2
    raise AssertionError("unreachable")


def _put_with_retry(row, author_account_id, config, token):
    """An update may be retried on 429 without a guard: it is value-idempotent.

    Setting the hours to eight twice still leaves eight. A create cannot do this,
    which is why the two paths differ.
    """
    delay = WRITE_RETRY_BASE_SECONDS
    for attempt in range(WRITE_RETRY_ATTEMPTS):
        try:
            return tempo.write("PUT", "/4/worklogs/%s" % row["worklogId"],
                               update_payload(row, author_account_id), config, token)
        except tempo.TempoError as error:
            if error.scope != tempo.Scope.RETRYABLE or attempt == WRITE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(delay + random.uniform(0, delay / 2))
            delay *= 2
    raise AssertionError("unreachable")


def _write_creates(rows, author_account_id, results, state, config, token, required_keys):
    # grouped by issue AND by calendar month. The bulk endpoint is per issue, so the
    # month is not its requirement — it is ours: a closure is recorded from one row of
    # the failing batch, so a batch spanning two months could mark the wrong month
    # closed, skipping the open one and retrying the closed one.
    by_issue = {}
    for row in rows:
        by_issue.setdefault((row["issueId"], row["date"][:7]), []).append(row)

    for (issue_id, _month), issue_rows in sorted(by_issue.items()):
        for start in range(0, len(issue_rows), BULK_MAX_ITEMS):
            batch = issue_rows[start:start + BULK_MAX_ITEMS]
            pending = []
            for row in batch:
                covered = _group_reason(row, state)
                if covered:
                    results.record(row, SKIPPED_PROPAGATED, covered)
                else:
                    pending.append(row)
            if pending:
                _send_bulk(issue_id, pending, author_account_id, results, state,
                           config, token, required_keys)


def _send_bulk(issue_id, rows, author_account_id, results, state, config, token, required_keys):
    """Send one batch, and resolve every outcome from Tempo's actual state when unsure."""
    path = "/4/worklogs/issue/%s/bulk" % issue_id
    pending = list(rows)
    delay = WRITE_RETRY_BASE_SECONDS

    for attempt in range(WRITE_RETRY_ATTEMPTS):
        payload = [create_payload(row, author_account_id) for row in pending]
        try:
            created = tempo.write("POST", path, payload, config, token)
            written = created if isinstance(created, list) else created.get("results", [])
            _match_created(pending, written, results)
            return
        except tempo.TempoError as error:
            if error.scope == tempo.Scope.RETRYABLE:
                # a throttled create is never blindly resent: the first attempt may have
                # landed. The range is re-read, whatever landed is recorded as written,
                # and only the rows that did not land are sent again.
                time.sleep(delay + random.uniform(0, delay / 2))
                delay *= 2
                landed, missing = _split_by_actual_state(
                    pending, author_account_id, results, config, token, required_keys,
                    "the create was throttled (429), so the range was re-read")
                if landed is None:
                    return
                if not missing:
                    return
                pending = missing
                continue

            if error.scope == tempo.Scope.BULK_IN_PROGRESS:
                # 409 is the most reliable signal that the previous attempt actually
                # landed, so the range decides and nothing is resent.
                _reconcile(pending, author_account_id, results, config, token, required_keys,
                           "a bulk action was already in progress for this issue")
                return

            if error.status == 400:
                # the message says one worklog could not be created but not whether any
                # of the others were, so the actual state decides — never the response.
                _reconcile(pending, author_account_id, results, config, token, required_keys,
                           "the bulk create was rejected: %s" % error.message)
                return

            if error.scope == tempo.Scope.GLOBAL:
                # the batch was sent, so its rows are not "never sent". Every other
                # uncertain branch re-reads; this one used to raise straight past it and
                # let run() mark them untried, which reports possibly-written hours as
                # never attempted.
                _reconcile(pending, author_account_id, results, config, token,
                           required_keys,
                           "the request failed with %s after the batch was sent"
                           % error.status)
                raise GlobalStop("%s %s: %s" % (error.status, pending[0]["date"],
                                                error.message))
            reason = _handle_failure(error, pending[0], state)
            # every row in this batch WAS sent and refused, so all of them are `failed`.
            # `skipped-propagated` means "an earlier failure covered this and we never
            # tried it", which only _group_reason can establish — recording the batch
            # that triggered the 403 as propagated pointed the user at an earlier
            # failure that did not exist, and suppressed the instruction to relay
            # Tempo's own message.
            for row in pending:
                results.record(row, FAILED, reason)
            return

    for row in pending:
        results.record(row, FAILED,
                       "still throttled after %d attempts" % WRITE_RETRY_ATTEMPTS)


def _split_by_actual_state(rows, author_account_id, results, config, token, required_keys,
                           preamble):
    """Record the rows that landed, and return the ones that did not.

    The comparison is the row COUNT for each (date, template) against what the
    proposal expected, not "does a row exist" — on a day that already held rows the
    existence question answers yes either way, which is why the count is the guard.
    """
    dates = [row["date"] for row in rows]
    try:
        fresh = tempo.read_all(
            "/4/worklogs/user/" + author_account_id,
            {"from": min(dates), "to": max(dates), "limit": 100},
            config, token,
        )
    except tempo.TempoError as error:
        for row in rows:
            results.record(row, FAILED,
                           "%s, and the re-read to establish what landed also failed (%s)"
                           % (preamble, error.message))
        return None, None

    by_key, _ = _index(fresh, required_keys)
    missing = []
    for row in rows:
        found = by_key.get((row["date"], row["templateId"]), [])
        if len(found) > row["existingCount"]:
            match = next((r for r in found
                          if (r.get("timeSpentSeconds") or 0) == row["seconds"]), found[-1])
            results.record(row, WRITTEN,
                           "%s; the row count for this date and template rose from %d to %d, "
                           "so this row landed" % (preamble, row["existingCount"], len(found)),
                           worklog_id=match.get("tempoWorklogId"))
        else:
            missing.append(row)
    return rows, missing


def _reconcile(rows, author_account_id, results, config, token, required_keys, preamble):
    """Resolve every row from a fresh read, recording the ones that did not land as failed."""
    landed, missing = _split_by_actual_state(
        rows, author_account_id, results, config, token, required_keys, preamble)
    if landed is None:
        return
    for row in missing:
        results.record(row, FAILED,
                       "%s; the row count for this date and template did not rise, "
                       "so this row did not land" % preamble)


def _match_created(rows, written, results):
    """Attach the returned worklog ids, never guessing when the match is ambiguous.

    Each returned worklog is claimed by at most one row. An unmatched row is still
    recorded as written — the call succeeded — but with no id rather than another
    row's id, because a wrong id here would point a later correction at the wrong
    worklog.
    """
    unclaimed = list(written)
    for row in rows:
        match = next((c for c in unclaimed
                      if c.get("startDate") == row["date"]
                      and (c.get("timeSpentSeconds") or 0) == row["seconds"]), None)
        if match is not None:
            unclaimed.remove(match)
            results.record(row, WRITTEN, None, worklog_id=match.get("tempoWorklogId"))
        else:
            results.record(row, WRITTEN,
                           "created, but the response held no row matching this date and "
                           "duration, so its worklog id is not recorded")


# ---------------------------------------------------------------- rendering

def render(proposal, results, stop_reason):
    counts = {}
    for entry in results.rows:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    lines = ["Applied proposal %s" % proposal["id"], ""]
    order = (WRITTEN, SKIPPED_PRECONDITION, SKIPPED_PROPAGATED, FAILED, UNTRIED)
    lines.append("  ".join("%s: %d" % (status, counts.get(status, 0)) for status in order))

    if stop_reason:
        lines.append("")
        lines.append("STOPPED: %s" % stop_reason)
        lines.append("The rows marked untried were never sent. Re-run to attempt them.")

    for status in order:
        entries = [e for e in results.rows if e["status"] == status]
        if not entries:
            continue
        lines.append("")
        lines.append("%s (%d)" % (_label(status), len(entries)))
        for entry in entries:
            head = "   %s  %-7s %7s  %s" % (
                entry["date"], entry["action"], "%.2fh" % entry["hours"],
                entry["accountKey"] or "(no account)")
            if entry["status"] == WRITTEN and entry["worklogId"]:
                head += "  -> %s" % tempo.format_worklog_ref(entry["worklogId"])
            lines.append(head)
            if entry["reason"]:
                lines.append("      %s" % entry["reason"])

    if counts.get(SKIPPED_PROPAGATED):
        lines.append("")
        # stated in full because "skipped" reads as "handled", and these hours were not
        # recorded anywhere — a different situation from a precondition skip.
        lines.append("The %d propagated row(s) were NOT recorded anywhere. Those hours are "
                     "still missing from Tempo." % counts[SKIPPED_PROPAGATED])

    lines.append("")
    lines.append("Row-by-row record: %s" % results.path)
    return "\n".join(lines)


def _label(status):
    return {
        WRITTEN: "Written",
        SKIPPED_PRECONDITION: "Skipped — a precondition no longer held",
        SKIPPED_PROPAGATED: "Skipped — covered by an earlier failure (hours NOT recorded)",
        FAILED: "Failed — attempted and refused",
        UNTRIED: "Untried — never sent",
    }[status]


# ---------------------------------------------------------------- cli entry

def load_proposal(reference):
    path = reference
    if not os.path.exists(path):
        candidate = os.path.join(tempo.STATE_DIR, "proposals", reference + ".json")
        if os.path.exists(candidate):
            path = candidate
        else:
            raise tempo.ConfigError("no proposal found at %r or %r" % (reference, candidate))
    with io.open(path, encoding="utf-8") as handle:
        proposal = json.load(handle)
    _refuse_if_superseded(proposal)
    return proposal


def _dates_of(proposal):
    """Every date a proposal has a row on."""
    return {row["date"] for row in proposal.get("rows", [])}


def _refuse_if_superseded(proposal):
    """Refuse a proposal that a later one over the same dates has replaced.

    Revising a proposal leaves the earlier file on disk and still applicable, so
    applying the stale id writes the hours the user rejected — successfully, and with
    exit 0, because the row preconditions cannot tell one reading of an instruction
    from another.

    Ids begin with a timestamp, so they sort chronologically. The remedy is positive
    and cheap: re-run plan.py. There is no override, because an override here would
    be a way to write the rejected version on purpose.
    """
    directory = os.path.join(tempo.STATE_DIR, "proposals")
    if not os.path.isdir(directory):
        return
    mine = proposal["id"]
    span = proposal["range"]

    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        other_id = name[:-len(".json")]
        if other_id <= mine:
            continue
        try:
            with io.open(os.path.join(directory, name), encoding="utf-8") as handle:
                other = json.load(handle)
        except (ValueError, OSError):
            # an unreadable neighbour must not block a legitimate apply
            continue
        other_span = other.get("range") or {}
        if not other_span:
            continue
        # the dates each proposal actually covers, not their spans. A proposal for
        # 09-01 and 09-30 spans the month but touches two days, so comparing spans
        # made it collide with every September proposal it shared no date with.
        if _dates_of(other) & _dates_of(proposal):
            raise Superseded(
                "proposal %s has been superseded by %s, which covers overlapping dates "
                "(%s to %s). The newer one is what the user was shown last, so this one "
                "is not applied. Find out which proposal the user actually confirmed "
                "rather than simply re-running plan.py."
                % (mine, other_id, other_span.get("from"), other_span.get("to"))
            )


def _main(argv):
    parser = argparse.ArgumentParser(description="Apply a Tempo worklog proposal.")
    parser.add_argument("--proposal", required=True, help="proposal id or path")
    arguments = parser.parse_args(argv[1:])

    try:
        proposal = load_proposal(arguments.proposal)
    except Superseded as error:
        sys.stderr.write(str(error) + "\n")
        return EXIT_SUPERSEDED
    except (tempo.ConfigError, ValueError) as error:
        sys.stderr.write(str(error) + "\n")
        return EXIT_USAGE

    try:
        results, stop_reason = run(proposal)
    except (tempo.ConfigError, tempo.TempoError) as error:
        sys.stderr.write(str(error) + "\n")
        return EXIT_PARTIAL

    sys.stdout.write(render(proposal, results, stop_reason) + "\n")

    statuses = {entry["status"] for entry in results.rows}
    if stop_reason or UNTRIED in statuses:
        return EXIT_GLOBAL_STOP
    # a partial run must not exit 0: the model reads 0 as "all done"
    # and would report hours as logged that were never recorded.
    if statuses - {WRITTEN}:
        return EXIT_PARTIAL
    return EXIT_ALL_WRITTEN


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
