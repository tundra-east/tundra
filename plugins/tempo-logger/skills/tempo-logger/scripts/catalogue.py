"""Build the worklog template catalogue from the caller's own Tempo history.

A template is the unit the skill actually learns:

    ( issueId, {every required attribute: value}, count, last used )

It is not "a work item". Nothing in the Tempo API turns a Jira key into the
numeric issueId a write needs, and nothing derives the project account from the
issue, so the only place both arrive together is a worklog that already exists.
That is why history is the sole source and why an account with no history is
never selectable.

Output is JSON, because this is the one script whose result the model interprets
rather than relays: semantic matching of the user's words onto these templates is
the model's job, and plan.py revalidates whatever it picks.

Read-only.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tempo  # noqa: E402

DEFAULT_WEEKS = 8
WORKLOG_PAGE_SIZE = 100

# the contract this script publishes; see setup.py's note.
EXIT_MEANINGS = (
    (0, "a catalogue on stdout; templates may still be empty"),
    (1, "the read failed; stdout is empty and stderr says why"),
    (2, "bad arguments"),
)

# every key build() puts on a template. A live check asserts this matches what a real
# build emits, so the list cannot fall behind the code that produces it.
TEMPLATE_FIELDS = (
    ("id", "hand this to plan.py; derived from the identity"),
    ("issueId", "the numeric Jira issue id"),
    ("attributes", "every required attribute value — the rest of the identity"),
    ("count", "how many worklogs this template came from"),
    ("lastUsed", "the most recent date it was used"),
    ("descriptions", "Tempo's own generated text, where a Jira key appears"),
    ("dailyTotalHours", "per-day totals; the only field that governs hours"),
    ("daysUsed", "how many distinct days those totals came from"),
    ("account", "key, name, raw status, resolved, attributeKey, collidesWith"),
    ("usable", "whether a write could use it"),
    ("unusableReason", "why not, when it could not"),
)


# ---------------------------------------------------------------- name folding

def fold_name(name):
    """Fold a display name for collision detection only — never for matching user input.

    Folds by Unicode category rather than by listing code points, because an
    enumeration always misses the one that matters: account names can
    carry an EN DASH (U+2013), and a copy-paste can introduce U+00A0 or U+2011
    that look identical to what the user typed.

    Bracket prefixes such as [SAL] are folded away here as well, which is what
    makes two "Partner integration" accounts collide when only a prefix tells them apart.
    They stay in the displayed name, so a user who typed [SAL] and got [Sales & Services]
    can see it.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    characters = []
    for character in decomposed:
        if unicodedata.combining(character):
            continue
        category = unicodedata.category(character)
        if category == "Pd":
            characters.append("-")
        elif category in ("Zs", "Zl", "Zp", "Cc") or character.isspace():
            characters.append(" ")
        else:
            characters.append(character)
    text = "".join(characters).lower()
    text = re.sub(r"\[[^\]]*\]", " ", text)
    return " ".join(text.split())


# ---------------------------------------------------------------- template identity

def template_id(issue_id, attributes):
    """A short id derived from the identity itself, not from the sort position.

    Derived rather than sequential so that a reference made against an older
    catalogue fails to resolve instead of resolving to whichever template now
    sits at that position — the wrong direction to fail in, since the model
    hands this id to plan.py and a silent shift would write to another issue.
    """
    payload = json.dumps(
        {"issueId": issue_id, "attributes": attributes},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    return "t" + hashlib.sha1(payload).hexdigest()[:8]


# ---------------------------------------------------------------- collection

def _required_attribute_keys(config, token):
    """Which attributes this tenant requires, read rather than assumed.

    The response order is not the order the notes recorded, so nothing may depend
    on position — only on the `required` flag.
    """
    attributes = tempo.read_all("/4/work-attributes", {"limit": 50}, config, token)
    required = [a for a in attributes if a.get("required")]
    return (
        [a["key"] for a in required],
        [a["key"] for a in required if a.get("type") == "ACCOUNT"],
        [{"key": a["key"], "name": a.get("name"), "type": a.get("type")} for a in required],
    )


def _accounts_by_key(config, token):
    """Every project account, keyed by `key`, keeping the raw status string.

    The raw status is kept rather than an is-open boolean, because show.py has to
    tell an ARCHIVED existing record apart from one it simply could not look up,
    and a boolean collapses those two into the same answer.
    """
    accounts = tempo.read_all("/4/accounts", {"limit": 50}, config, token)
    return {
        account["key"]: {
            "key": account["key"],
            "name": account.get("name"),
            "status": account.get("status"),
        }
        for account in accounts if account.get("key")
    }


def _collision_groups(accounts_by_key):
    """Group accounts whose folded names are equal.

    Deliberately does NOT deduplicate. Both members must survive into the
    catalogue: collapsing them would leave one candidate when the user says
    "partner integration", and the hours would land on the wrong business unit
    with no sign that a second option ever existed.
    """
    groups = {}
    for account in accounts_by_key.values():
        if not account["name"]:
            continue
        groups.setdefault(fold_name(account["name"]), []).append(account["key"])
    return {folded: sorted(keys) for folded, keys in groups.items() if len(keys) > 1}


def _worklogs(author_account_id, date_from, date_to, config, token):
    return tempo.read_all(
        "/4/worklogs/user/" + author_account_id,
        {"from": date_from, "to": date_to, "limit": WORKLOG_PAGE_SIZE},
        config, token,
    )


# ---------------------------------------------------------------- assembly

def build(weeks, config=None, token=None, today=None):
    config = config or tempo.load_config()
    token = token or tempo.load_token()
    today = today or datetime.date.today()

    author_account_id, author_source = tempo.resolve_author_account_id(config, token)

    # the window reaches the same distance either side of today,
    # so the one knob governs both directions and no second span is invented.
    # reaching forward matters because leave is routinely booked ahead,
    # and a template that exists only on a future-dated worklog would otherwise be invisible.
    span = datetime.timedelta(days=weeks * 7)
    date_from = (today - span).isoformat()
    date_to = (today + span).isoformat()

    required_keys, account_typed_keys, required_described = _required_attribute_keys(config, token)
    accounts_by_key = _accounts_by_key(config, token)
    collisions = _collision_groups(accounts_by_key)
    rows = _worklogs(author_account_id, date_from, date_to, config, token)

    templates = {}
    weekend_dates = []
    for row in rows:
        issue_id = (row.get("issue") or {}).get("id")
        if issue_id is None:
            continue
        values = {
            value["key"]: value.get("value")
            for value in ((row.get("attributes") or {}).get("values") or [])
        }
        attributes = {key: values.get(key) for key in required_keys}
        key = template_id(issue_id, attributes)

        entry = templates.get(key)
        if entry is None:
            entry = templates[key] = {
                "id": key,
                "issueId": issue_id,
                "attributes": attributes,
                "count": 0,
                "lastUsed": None,
                "descriptions": [],
                "_secondsByDate": {},
            }
        entry["count"] += 1
        start_date = row.get("startDate")
        if start_date and (entry["lastUsed"] is None or start_date > entry["lastUsed"]):
            entry["lastUsed"] = start_date
        description = row.get("description")
        if description and description not in entry["descriptions"]:
            entry["descriptions"].append(description)
        hours = round((row.get("timeSpentSeconds") or 0) / 3600.0, 2)
        if start_date:
            entry["_secondsByDate"][start_date] = (
                entry["_secondsByDate"].get(start_date, 0) + (row.get("timeSpentSeconds") or 0))

        # weekend evidence counts only what has already happened.
        # a future-dated weekend row this skill proposed
        # would otherwise become the justification for proposing weekends again.
        if start_date:
            parsed = datetime.date(*[int(part) for part in start_date.split("-")])
            if parsed.weekday() >= 5 and parsed <= today:
                weekend_dates.append(start_date)

    warnings = []
    for entry in templates.values():
        # per-day totals, not per-row hours: hours may only be derived for a proposal
        # when every day this template was used carried the same total,
        # and a day split into two rows of 4h is a day of 8h for that purpose.
        by_date = entry.pop("_secondsByDate")
        entry["dailyTotalHours"] = sorted({round(v / 3600.0, 2) for v in by_date.values()})
        entry["daysUsed"] = len(by_date)
        entry["account"] = _describe_account(
            entry, required_keys, account_typed_keys, accounts_by_key, collisions)
        _mark_usability(entry, required_keys, warnings)

    ranked = sorted(
        templates.values(),
        key=lambda t: (t["count"], t["lastUsed"] or ""),
        reverse=True,
    )

    if not ranked:
        warnings.append(
            "no worklogs in the window, so there are no templates — either this is "
            "a cold start, or the window is too narrow: try a larger --weeks"
        )
    if collisions:
        warnings.append(
            "%d account name(s) are shared by more than one key, so a name alone "
            "cannot identify an account here" % len(collisions)
        )

    return {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "window": {"from": date_from, "to": date_to, "weeks": weeks},
        "author": {"accountId": author_account_id, "source": author_source},
        "requiredAttributes": required_described,
        "templates": ranked,
        "weekendHistory": {
            "count": len(weekend_dates),
            "lastUsed": max(weekend_dates) if weekend_dates else None,
            "dates": sorted(set(weekend_dates)),
        },
        "accountNameCollisions": [
            {"foldedName": folded, "keys": keys} for folded, keys in sorted(collisions.items())
        ],
        "warnings": warnings,
    }


def _describe_account(entry, required_keys, account_typed_keys, accounts_by_key, collisions):
    """Resolve the project account attribute to key, name and raw status.

    Which attribute holds the account is never hardcoded — `_Account_` is
    one tenant's name for it, not the API's.

    A value that matches a real account key settles it, because that is direct
    evidence. Only when nothing matches does the tenant's declared attribute type
    decide, and that second pass exists so an unresolved key still gets reported:
    an account that was renamed away would otherwise vanish from the catalogue,
    which reads to the user as a day with no hours, and they log it again.
    """
    for key in required_keys:
        value = entry["attributes"].get(key)
        known = accounts_by_key.get(value) if value is not None else None
        if known:
            folded = fold_name(known["name"]) if known["name"] else None
            return dict(
                known,
                attributeKey=key,
                resolved=True,
                collidesWith=[k for k in collisions.get(folded, []) if k != value],
            )

    for key in account_typed_keys:
        value = entry["attributes"].get(key)
        if value is not None:
            return {
                "key": value,
                "name": None,
                "status": None,
                "attributeKey": key,
                "resolved": False,
                "collidesWith": [],
            }
    return None


def _mark_usability(entry, required_keys, warnings):
    """Say whether a write could use this template, and why not when it could not."""
    missing = [key for key in required_keys if entry["attributes"].get(key) is None]
    if missing:
        entry["usable"] = False
        entry["unusableReason"] = (
            "the worklogs behind this template have no value for %s, which this tenant "
            "now requires — it was probably made required after they were logged"
            % ", ".join(missing)
        )
        warnings.append(
            "template %s (issue %s) is missing the required attribute(s) %s"
            % (entry["id"], entry["issueId"], ", ".join(missing))
        )
        return

    account = entry.get("account")
    if account and not account["resolved"]:
        entry["usable"] = False
        entry["unusableReason"] = (
            "project account %s is not in /4/accounts — it may have been renamed or "
            "removed, and a write naming it would be rejected" % account["key"]
        )
        return
    if account and account["status"] != "OPEN":
        # verified: a write to one of these is refused with 403 "Account is closed or archived",
        # and Tempo's own message groups CLOSED and ARCHIVED together.
        entry["usable"] = False
        entry["unusableReason"] = (
            "project account %s is %s, and Tempo refuses worklogs on it"
            % (account["key"], account["status"])
        )
        return

    entry["usable"] = True
    entry["unusableReason"] = None


# ---------------------------------------------------------------- cli entry

def _main(argv):
    parser = argparse.ArgumentParser(
        description="Build the worklog template catalogue from Tempo history.")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS,
                        help="how far either side of today to read (default %d)" % DEFAULT_WEEKS)
    arguments = parser.parse_args(argv[1:])

    if arguments.weeks < 1:
        sys.stderr.write("--weeks must be at least 1\n")
        return 2

    try:
        catalogue = build(arguments.weeks)
    except (tempo.ConfigError, tempo.TempoError) as error:
        sys.stderr.write(str(error) + "\n")
        return 1

    sys.stdout.write(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
