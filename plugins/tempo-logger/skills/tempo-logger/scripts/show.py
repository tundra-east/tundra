"""Render the caller's existing worklogs for a date range.

Answers "what did I log?" and nothing else — it proposes nothing and writes nothing.

The one rule that matters most here is that no row is ever omitted. A row whose
project account no longer resolves still appears, with its bare key and a note,
because a missing row reads to the user as a day with no hours and they log it
a second time.

Read-only.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalogue  # noqa: E402
import plan  # noqa: E402
import tempo  # noqa: E402


# the contract this script publishes; see setup.py's note.
EXIT_MEANINGS = (
    (0, "worklogs printed on stdout"),
    (1, "the read failed; stdout is empty and stderr says why"),
    (2, "bad arguments: --from and --to go together, and --to must not precede --from"),
)


def current_period(today, config, token):
    """The approval period containing today, taken from Tempo rather than computed.

    /4/periods returns its list under `periods`, not `results`, so read_all would
    silently return nothing here — this must stay a plain read.

    The approval period is not the same span as a working week, and the two are not
    guaranteed to align, so it is read rather than derived from a weekday rule.
    """
    span = datetime.timedelta(days=15)
    response = tempo.read(
        "/4/periods",
        {"from": (today - span).isoformat(), "to": (today + span).isoformat()},
        config, token,
    )
    for period in response.get("periods", []):
        if period["from"] <= today.isoformat() <= period["to"]:
            return tempo.parse_date(period["from"]), tempo.parse_date(period["to"])
    raise tempo.ConfigError(
        "Tempo returned no approval period containing %s, so the default range "
        "cannot be derived — pass --from and --to" % today.isoformat()
    )


def fetch(date_from, date_to, config=None, token=None):
    config = config or tempo.load_config()
    token = token or tempo.load_token()
    author_account_id, _ = tempo.resolve_author_account_id(config, token)
    rows = tempo.read_all(
        "/4/worklogs/user/" + author_account_id,
        {"from": date_from.isoformat(), "to": date_to.isoformat(), "limit": 100},
        config, token,
    )
    return sorted(rows, key=lambda r: (r.get("startDate") or "", r.get("startTime") or ""))


def render(rows, date_from, date_to, accounts_by_key, required_keys):
    """The same week grid plan.py renders, with no proposed rows in it.

    Shared rather than reimplemented: a reader who has seen one confirmation already
    knows how to read this, and a change to the grid cannot now reach only one of them.
    """
    lines = []
    total = sum(row.get("timeSpentSeconds") or 0 for row in rows)
    lines.append("%s to %s \u2014 %d worklog%s, %.2f hours total"
                 % (date_from.isoformat(), date_to.isoformat(), len(rows),
                    "" if len(rows) == 1 else "s", total / 3600.0))

    if not rows:
        # stated in words, because silence here is indistinguishable from a failure
        lines.append("")
        lines.append("No worklogs in this range.")
        return "\n".join(lines)

    by_date = {}
    for row in rows:
        by_date.setdefault(row.get("startDate"), []).append(row)

    groups = [(date_text, [], [_existing(row, accounts_by_key, required_keys)
                              for row in by_date[date_text]])
              for date_text in sorted(by_date)]

    lines.append("")
    lines.extend(plan._body(groups))
    return "\n".join(lines)


def _existing(row, accounts_by_key, required_keys):
    """A worklog in the shape the grid reads, which is the shape plan.py builds too."""
    return {
        "date": row.get("startDate"),
        "seconds": row.get("timeSpentSeconds") or 0,
        "account": _account_of(row, accounts_by_key, required_keys),
        "issueId": (row.get("issue") or {}).get("id"),
        "attributes": {
            key: value.get("value")
            for value in ((row.get("attributes") or {}).get("values") or [])
            for key in [value.get("key")] if key in required_keys
        },
        "descriptions": [row.get("description")],
    }


def _account_of(row, accounts_by_key, required_keys):
    """Resolve a row's project account for display, never dropping an unresolved key."""
    values = {
        value["key"]: value.get("value")
        for value in ((row.get("attributes") or {}).get("values") or [])
    }
    for key in required_keys:
        value = values.get(key)
        if value is None:
            continue
        known = accounts_by_key.get(value)
        if known:
            return known
        return {"key": value, "name": None, "status": None}
    return None


# ---------------------------------------------------------------- cli entry

def _main(argv):
    parser = argparse.ArgumentParser(description="Show existing Tempo worklogs.")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (default: this approval period)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (default: this approval period)")
    parser.add_argument("--json", action="store_true", help="emit the raw rows instead of text")
    arguments = parser.parse_args(argv[1:])

    if bool(arguments.date_from) != bool(arguments.date_to):
        sys.stderr.write("--from and --to go together, or give neither\n")
        return 2

    try:
        config = tempo.load_config()
        token = tempo.load_token()
        today = datetime.date.today()

        if arguments.date_from:
            date_from = tempo.parse_date(arguments.date_from)
            date_to = tempo.parse_date(arguments.date_to)
            if date_to < date_from:
                sys.stderr.write("--to is before --from\n")
                return 2
        else:
            date_from, date_to = current_period(today, config, token)

        rows = fetch(date_from, date_to, config, token)
        if arguments.json:
            sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
            return 0

        required_keys, _, _ = catalogue._required_attribute_keys(config, token)
        accounts_by_key = catalogue._accounts_by_key(config, token)
        sys.stdout.write(render(rows, date_from, date_to, accounts_by_key, required_keys) + "\n")
        return 0
    except (tempo.ConfigError, tempo.TempoError) as error:
        sys.stderr.write(str(error) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
