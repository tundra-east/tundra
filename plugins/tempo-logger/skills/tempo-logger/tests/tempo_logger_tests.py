"""Deterministic checks on the properties the tempo-logger scripts rely on.

These are not tests of Tempo; they are tests of the guarantees the scripts claim,
each one a rule that would otherwise be a convention.
Nothing here reads a token or reaches the network:
the user's config and state directories point at an empty temporary directory before the scripts are imported,
and the checks that need Tempo's answers are handed canned ones.
The checks that need a live tenant are in tests/live_check.py, which is run by hand and never discovered.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/tempo_logger_tests.py
"""

import ast
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# the scripts read the token, config and state paths once, at import,
# so every variable that could point them at the real user's files is overridden first
_USER_STATE = tempfile.mkdtemp()
for _var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
    os.environ[_var] = _USER_STATE

# import the modules under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS_DIR)

import tempo  # noqa: E402
import setup as setup_script  # noqa: E402
import catalogue  # noqa: E402
import plan  # noqa: E402
import show  # noqa: E402
import apply as apply_script  # noqa: E402


# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(label, condition, detail=""):
    ok = bool(condition)
    _results.append((_group, label, ok, "" if ok else detail))


# every prose file, not SKILL.md alone: "!! NOT WRITEABLE" outlived its removal from
# SKILL.md by living on in the README, which this check was not looking at.
_PROSE = {
    "SKILL.md": os.path.join(_HERE, "..", "SKILL.md"),
    # tests -> skill -> skills -> plugin root, where the README lives
    "README.md": os.path.join(_HERE, *([".."] * 3 + ["README.md"])),
}

# a config that never reaches the token file,
# and a host on this machine, in case a stub is missed and something is sent after all
_OFFLINE = {"baseUrl": "http://127.0.0.1:9/4"}


class _Stubs(object):
    """Replace module attributes for one block and put every one back, even when the block raises."""

    def __init__(self):
        self._saved = []

    def set(self, owner, name, value):
        self._saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self):
        # no request may leave a stubbed block, whichever collaborator was missed
        def no_network(method, url, token, body=None):
            raise AssertionError("a request was sent: %s %s" % (method, url))
        self.set(tempo, "_send", no_network)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self._saved):
            setattr(owner, name, value)
        return False


def _canned_read_all(path, params=None, config=None, token=None):
    """What a small tenant answers, keyed by path, so catalogue.build() runs with no tenant."""
    if path == "/4/work-attributes":
        return [{"key": "_Account_", "name": "Account", "type": "ACCOUNT", "required": True},
                {"key": "_Note_", "name": "Note", "type": "STATIC_LIST", "required": False}]
    if path == "/4/accounts":
        return [{"key": "200-17", "name": "Widget Platform \u2013 Development", "status": "OPEN"},
                {"key": "200-16", "name": "Widget Platform - Governance", "status": "OPEN"}]
    if path.startswith("/4/worklogs/user/"):
        return [
            {"tempoWorklogId": 90001, "startDate": "2026-09-08", "timeSpentSeconds": 8 * 3600,
             "issue": {"id": 10017}, "description": "Working on work item ACME-182",
             "attributes": {"values": [{"key": "_Account_", "value": "200-17"}]}},
            {"tempoWorklogId": 90002, "startDate": "2026-09-09", "timeSpentSeconds": 3600,
             "issue": {"id": 10016}, "description": "Working on work item ACME-177",
             "attributes": {"values": [{"key": "_Account_", "value": "200-16"}]}},
        ]
    raise AssertionError("no canned response for %s" % path)


def _proposed(action, seconds, before=None, blocked=None, reading=None):
    return {"date": "2026-09-08", "action": action, "seconds": seconds,
            "issueId": 1, "account": {"key": "200-17", "name": "Dev", "status": "OPEN"},
            "dayBeforeSeconds": before, "existingSeconds": before or 0,
            "blocked": bool(blocked), "blockedReason": blocked, "reading": reading,
            "annotations": []}


def test_endpoint_policy():
    """read and write reject each other"""
    # the whitelist must recognise a write however its ids are spelled
    check("POST /4/worklogs is mutating", tempo.is_mutating("POST", "/4/worklogs"))
    check("POST bulk is mutating", tempo.is_mutating("POST", "/4/worklogs/issue/10017/bulk"))
    check("PUT by id is mutating", tempo.is_mutating("PUT", "/4/worklogs/90003"))
    check("DELETE by id is mutating", tempo.is_mutating("DELETE", "/4/worklogs/90003"))

    # the read POST must survive: a "POST means write" rule would ban it
    check("POST /4/worklogs/search is NOT mutating",
          not tempo.is_mutating("POST", "/4/worklogs/search"))
    check("GET worklogs is NOT mutating", not tempo.is_mutating("GET", "/4/worklogs"))
    check("GET accounts is NOT mutating", not tempo.is_mutating("GET", "/4/accounts"))

    # an author-account path segment must not be mistaken for a write target
    check("GET worklogs/user/{acct} is NOT mutating",
          not tempo.is_mutating("GET", "/4/worklogs/user/712020:00000000-0000-4000-8000-000000000000"))


def test_interfaces_refuse():
    """the two interfaces actually refuse"""
    # both interfaces read the token before the policy check,
    # so with no token the missing file would answer first and the refusal would never be reached.
    # an explicit config and token take the token file out of the way.
    sent = []
    with _Stubs() as stubs:
        stubs.set(tempo, "_send", lambda method, url, token, body=None: sent.append(url) or {})
        try:
            tempo.write("GET", "/4/globalconfiguration", None, _OFFLINE, "t")
            check("write() refuses a read endpoint", False, "no exception raised")
        except tempo.ConfigError as error:
            check("write() refuses a read endpoint", "refused" in str(error), str(error))
        except Exception as error:
            check("write() refuses a read endpoint", False, type(error).__name__)

        try:
            # read() can only refuse a mutating GET and no such endpoint exists,
            # so the meaningful direction is read_post against a write endpoint
            tempo.read_post("/4/worklogs", {"x": 1}, _OFFLINE, "t")
            check("read_post() refuses a write endpoint", False, "no exception raised")
        except tempo.ConfigError as error:
            check("read_post() refuses a write endpoint", "refused" in str(error), str(error))
        except Exception as error:
            check("read_post() refuses a write endpoint", False, type(error).__name__)
    check("and neither refusal sent a request", sent == [], str(sent))


def test_url_join():
    """url join tolerates the version prefix on both sides"""
    joined = tempo._build_url("https://api.tempo.io/4", "/4/globalconfiguration")
    check("no doubled version segment", joined == "https://api.tempo.io/4/globalconfiguration", joined)
    joined2 = tempo._build_url("https://api.tempo.io", "/4/globalconfiguration")
    check("base without version still works",
          joined2 == "https://api.tempo.io/4/globalconfiguration", joined2)


def test_failure_classification():
    """failure classification is keyed on the message, not the status"""
    cases = [
        (403, '{"errors":[{"message":"Period is closed"}]}', tempo.Scope.GROUP_MONTH),
        (403, '{"errors":[{"message":"period is closed."}]}', tempo.Scope.GROUP_MONTH),
        (403, '{"errors":[{"message":"Account is closed or archived"}]}', tempo.Scope.GROUP_ACCOUNT),
        (403, '{"errors":[{"message":"Something else entirely"}]}', tempo.Scope.GLOBAL),
        (401, '{"errors":[{"message":"Unauthorized"}]}', tempo.Scope.GLOBAL),
        (409, '{"errors":[{"message":"bulk action in progress"}]}', tempo.Scope.BULK_IN_PROGRESS),
        (429, "", tempo.Scope.RETRYABLE),
        (400, '{"errors":[{"message":"bad attribute"}]}', tempo.Scope.ROW),
        (404, "", tempo.Scope.ROW),
        (503, "", tempo.Scope.GLOBAL),
    ]
    for status, body, expected in cases:
        scope, _ = tempo._classify(status, body, "/x")
        check("%s -> %s" % (status, expected), scope == expected, "got " + scope)

    # both observed casings must land in the same bucket, because an exact match
    # that missed would escalate one closed month into a global stop
    lower, _ = tempo._classify(403, '{"errors":[{"message":"period is closed"}]}', "/x")
    upper, _ = tempo._classify(403, '{"errors":[{"message":"Period is closed"}]}', "/x")
    check("casing does not change the scope", lower == upper)


def test_setup_config_values():
    """setup: config values are rejected before they are written"""
    for key, value in [
        ("baseUrl", "http://api.tempo.io/4"),
        ("defaultStartTime", "8:00"),
        ("defaultStartTime", "24:00:00"),
        ("authorAccountId", "   "),
    ]:
        try:
            setup_script._validate(key, value)
            check("%s=%r rejected" % (key, value), False, "accepted")
        except tempo.ConfigError:
            check("%s=%r rejected" % (key, value), True)

    check("baseUrl trailing slash dropped",
          setup_script._validate("baseUrl", "https://api.tempo.io/4/") == "https://api.tempo.io/4")
    check("defaultStartTime 08:00:00 accepted",
          setup_script._validate("defaultStartTime", "08:00:00") == "08:00:00")

    # an unknown key must not be writable at all: load_config() never reads it,
    # so a typo would look like it took effect and change nothing.
    check("defaultStartTime is a known key", "defaultStartTime" in setup_script.CONFIG_KEYS)
    check("the removed renderLimit is not a key", "renderLimit" not in setup_script.CONFIG_KEYS)


def test_setup_token_shape():
    """setup: token shape anomalies are named, and the token is never echoed"""
    original_token_path = tempo.TOKEN_PATH
    fixtures = [
        ("wrapped paste", "abc\ndef\n", "non-empty lines"),
        ("quoted", '"abcdef"\n', "wrapped in quotes"),
        ("bearer prefix", "Bearer abcdef\n", "starts with 'Bearer '"),
        ("internal space", "abc def\n", "whitespace inside"),
        ("byte-order mark", "\ufeffabcdef\n", "byte-order mark"),
    ]
    scratch = os.path.join(tempfile.mkdtemp(), "token.txt")
    try:
        for label, content, expected in fixtures:
            with io.open(scratch, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            tempo.TOKEN_PATH = scratch
            notes = " | ".join(setup_script._token_shape_notes())
            check("%s is named" % label, expected in notes, notes)
            secret = content.strip().strip('"')
            check("%s does not echo the token" % label,
                  secret not in notes or len(secret) < 4, notes)
    finally:
        tempo.TOKEN_PATH = original_token_path
        if os.path.exists(scratch):
            os.remove(scratch)


def test_catalogue_name_folding():
    """catalogue: name folding is by Unicode category, not by a list of code points"""
    fold = catalogue.fold_name
    # the dash the notes singled out, plus the two an enumeration would miss
    check("EN DASH folds to hyphen", fold("Widget Platform \u2013 Development")
          == fold("Widget Platform - Development"))
    check("NON-BREAKING HYPHEN folds too", fold("A\u2011B") == fold("A-B"))
    check("NBSP folds to a space", fold("A\u00a0B") == fold("A B"))
    check("case is folded", fold("Partner Integration") == fold("partner integration"))
    check("trailing whitespace is folded", fold("Admin  ") == fold("Admin"))

    # the two groups the notes recorded must fold together, and this is the pair
    # that only collides once the bracket prefix is folded away
    check("bracket prefixes fold away",
          fold("[Shared] Partner integration") == fold("[Sales & Services] Partner Integration"))

    # and folding must not erase a real difference
    check("different names stay different", fold("200-16 dev") != fold("200-17 dev"))


def test_catalogue_collisions():
    """catalogue: collision detection keeps both members"""
    accounts = {
        "PARTNER_A": {"key": "PARTNER_A", "name": "[Shared] Partner integration",
                      "status": "OPEN"},
        "PARTNER_B": {"key": "PARTNER_B",
                      "name": "[Sales & Services] Partner Integration",
                      "status": "OPEN"},
        "ADMIN_OPS": {"key": "ADMIN_OPS", "name": "Admin", "status": "OPEN"},
    }
    groups = catalogue._collision_groups(accounts)
    check("the colliding pair is found", len(groups) == 1, str(groups))
    # deduplicating here would leave one candidate for "partner integration"
    # and send the hours to the wrong business unit with no sign of the other
    check("both members survive", sorted(list(groups.values())[0])
          == ["PARTNER_A", "PARTNER_B"], str(groups))
    check("a unique name forms no group", "admin" not in groups)


def test_catalogue_template_id():
    """catalogue: the template id follows identity, not position"""
    base = catalogue.template_id(10017, {"_Account_": "200-17"})
    check("id is stable across calls",
          base == catalogue.template_id(10017, {"_Account_": "200-17"}))
    check("a different issue gives a different id",
          base != catalogue.template_id(10018, {"_Account_": "200-17"}))
    check("a different account gives a different id",
          base != catalogue.template_id(10017, {"_Account_": "200-16"}))
    check("a missing attribute gives a different id",
          base != catalogue.template_id(10017, {"_Account_": None}))


def test_catalogue_unusable_templates():
    """catalogue: unusable templates are marked, never dropped"""
    cases = [
        ("closed account", {"_Account_": "999"},
         {"999": {"key": "999", "name": "Old", "status": "CLOSED"}}, "CLOSED"),
        ("archived account", {"_Account_": "998"},
         {"998": {"key": "998", "name": "Older", "status": "ARCHIVED"}}, "ARCHIVED"),
        ("account not in /4/accounts", {"_Account_": "gone"}, {}, "not in /4/accounts"),
        ("missing required attribute", {"_Account_": None}, {}, "now requires"),
    ]
    for label, attributes, known, expected in cases:
        entry = {"id": "tX", "issueId": 1, "attributes": attributes}
        entry["account"] = catalogue._describe_account(
            entry, ["_Account_"], ["_Account_"], known, {})
        warnings = []
        catalogue._mark_usability(entry, ["_Account_"], warnings)
        check("%s is unusable" % label, entry["usable"] is False)
        check("%s says why" % label, expected in (entry["unusableReason"] or ""),
              str(entry["unusableReason"]))

    # the raw status must reach the reason, because CLOSED and ARCHIVED are
    # different situations even though Tempo's 403 groups them
    entry = {"id": "tX", "issueId": 1, "attributes": {"_Account_": "998"}}
    entry["account"] = catalogue._describe_account(
        entry, ["_Account_"], ["_Account_"],
        {"998": {"key": "998", "name": "Older", "status": "ARCHIVED"}}, {})
    check("raw status is preserved, not a boolean", entry["account"]["status"] == "ARCHIVED")

    # an attribute the tenant does not declare as an ACCOUNT must not be
    # reported as an unresolved account
    entry = {"id": "tX", "issueId": 1, "attributes": {"_SomeText_": "free text"}}
    check("a non-account attribute is not mistaken for one",
          catalogue._describe_account(entry, ["_SomeText_"], [], {}, {}) is None)


def test_tempo_nouns():
    """tempo: an id always arrives with its noun, and an account always with its key"""
    check("worklog and issue read differently",
          tempo.format_worklog_ref(90001) != tempo.format_issue(90001, None))
    check("issue carries its noun", tempo.format_issue(10017, None) == "issue 10017")
    check("worklog carries its noun", tempo.format_worklog_ref(90001) == "worklog 90001")
    # the key is the only thing that separates two accounts whose names collide
    rendered = tempo.format_account({"key": "200-17", "name": "Widget Platform \u2013 Development",
                                     "status": "OPEN"})
    check("the key is always shown", rendered.startswith("200-17"), rendered)
    check("an OPEN account shows no status noise", "[OPEN]" not in rendered)
    closed = tempo.format_account({"key": "999", "name": "Old", "status": "CLOSED"})
    check("a non-OPEN status is surfaced", "[CLOSED]" in closed, closed)
    unresolved = tempo.format_account({"key": "gone", "name": None, "status": None})
    check("an unresolved name says so rather than going blank",
          "gone" in unresolved and "not found" in unresolved, unresolved)
    check("hours always carry two decimals", tempo.format_hours(28800) == "8.00h")


def test_plan_billable():
    """plan: the billable asymmetry, which Tempo gets silently wrong downward"""
    billable_cases = [
        ("1:1 decrease", 8 * 3600, 8 * 3600, 6 * 3600, 6 * 3600, True),
        ("1:1 increase", 8 * 3600, 8 * 3600, 10 * 3600, 10 * 3600, False),
        ("not 1:1 decrease", 8 * 3600, 4 * 3600, 6 * 3600, 4 * 3600, False),
        ("not 1:1 increase", 8 * 3600, 4 * 3600, 10 * 3600, 4 * 3600, False),
    ]
    for label, current, billable, new, expected_after, expected_send in billable_cases:
        after, send = plan._billable_after(current, billable, new)
        check("%s -> billable %s" % (label, tempo.format_hours(expected_after)),
              after == expected_after, tempo.format_hours(after))
        check("%s -> send=%s" % (label, expected_send), send is expected_send)

    # the rule that must never come back: scaling a reduced billable by the hour ratio
    # would turn a deliberate 1800 into 900, destroying the value it meant to protect
    after, _ = plan._billable_after(3600, 1800, 900)
    check("a reduced billable is preserved, never scaled", after == 1800, "got %s" % after)


def test_plan_arithmetic():
    """plan: the model's arithmetic is recomputed, not trusted"""
    try:
        plan._verify_arithmetic([{"finalHours": 8.0}], 9.0, 7.0, "2026-09-08")
        check("a disagreeing final value is refused", False, "accepted")
    except tempo.ConfigError as error:
        check("a disagreeing final value is refused", True)
        check("the refusal shows both numbers",
              "8.00" in str(error) and "9.00" in str(error), str(error))
    try:
        plan._verify_arithmetic([{"finalHours": 9.0}], 9.0, 7.0, "2026-09-08")
        check("an agreeing final value passes", True)
    except tempo.ConfigError as error:
        check("an agreeing final value passes", False, str(error))
    try:
        plan._verify_arithmetic([{}], 9.0, 7.0, "2026-09-08")
        check("an absent final value is not invented", True)
    except tempo.ConfigError as error:
        check("an absent final value is not invented", False, str(error))

    # reducing a row to nothing is a delete now, so the guard that refused small
    # updates as disguised deletes is gone along with the refusal that caused it
    candidate = {
        "date": "2026-09-15",
        "templateId": "tf10a378a",
        "template": {"issueId": 1, "attributes": {}, "account": {"key": "200-17"},
                     "count": 4, "lastUsed": "2026-09-08", "dailyTotalHours": [8.0],
                     "daysUsed": 1, "descriptions": []},
        "steps": [{"verb": "SET", "hours": 0.0, "finalHours": 0.0}],
        "existing": [{"tempoWorklogId": 5, "timeSpentSeconds": 8 * 3600,
                      "billableSeconds": 8 * 3600, "startTime": "08:00:00",
                      "startDate": "2026-09-15"}],
    }
    rows = plan._resolve_action(candidate, [])
    check("setting an existing row to zero is a delete",
          [r["action"] for r in rows] == ["DELETE"])
    check("and it carries the worklog to remove", rows[0]["worklogId"] == 5)

    candidate["steps"] = [{"verb": "SET", "hours": 1.0, "finalHours": 1.0}]
    small = plan._resolve_action(candidate, [])
    check("a small update is no longer refused as a disguised delete",
          small[0]["action"] == "UPDATE" and small[0]["hours"] == 1.0)

    # the defect that drove total-based verbs: a day holding 1h and 1h, set to 1.5,
    # used to change one record and leave the day at 2.5 with the check still passing
    candidate["steps"] = [{"verb": "SET", "hours": 1.5, "finalHours": 1.5}]
    candidate["existing"] = [
        {"tempoWorklogId": 5, "timeSpentSeconds": 3600, "billableSeconds": 3600,
         "startTime": "09:00:00", "startDate": "2026-09-15"},
        {"tempoWorklogId": 6, "timeSpentSeconds": 3600, "billableSeconds": 3600,
         "startTime": "10:00:00", "startDate": "2026-09-15"},
    ]
    rows = plan._resolve_action(candidate, [])
    check("a verb reads the day's total for the item, not one record",
          rows[0]["dayBeforeSeconds"] == 2 * 3600)
    check("one record carries the result", rows[0]["action"] == "UPDATE"
          and rows[0]["hours"] == 1.5)
    check("and the rest are removed",
          [r["action"] for r in rows[1:]] == ["DELETE"]
          and rows[1]["worklogId"] == 6)
    check("so the date comes to exactly what was asked",
          sum(r["seconds"] for r in rows if r["action"] != "DELETE") == int(1.5 * 3600))

    # and reducing that same day to nothing removes both records
    candidate["steps"] = [{"verb": "SET", "hours": 0.0, "finalHours": 0.0}]
    rows = plan._resolve_action(candidate, [])
    check("zeroing a day with two records removes both",
          [r["action"] for r in rows] == ["DELETE", "DELETE"])

    # a create of nothing still has nothing to create
    rows = [{"date": "2026-09-15", "templateId": "tf10a378a", "action": "CREATE",
             "seconds": 0, "attributes": {}, "blocked": False, "blockedReason": None}]
    plan.apply_local_checks(rows, {"tf10a378a": {"usable": True, "attributes": {},
                                                 "dailyTotalHours": [8.0]}},
                            None, datetime.date(2026, 9, 11), [])
    check("a zero-hour create is still blocked", rows[0]["blocked"] is True)
    check("and says there is nothing to create",
          "nothing to create" in (rows[0]["blockedReason"] or ""))


def test_apply_start_times():
    """apply: start times chain within a day and never wrap onto another date"""
    rows = [{"date": "2026-09-15", "action": "CREATE", "seconds": 6 * 3600},
            {"date": "2026-09-15", "action": "CREATE", "seconds": 2 * 3600}]
    apply_script.assign_start_times(rows, {}, "08:00:00")
    check("first row starts at the configured default", rows[0]["startTime"] == "08:00:00")
    check("second row starts where the first ended", rows[1]["startTime"] == "14:00:00",
          rows[1]["startTime"])

    existing = {"2026-09-08": [{"startTime": "08:00:00", "timeSpentSeconds": 7 * 3600},
                               {"startTime": "16:00:00", "timeSpentSeconds": 3600}]}
    rows = [{"date": "2026-09-08", "action": "CREATE", "seconds": 2 * 3600}]
    apply_script.assign_start_times(rows, existing, "08:00:00")
    check("a new row starts after everything the day already holds",
          rows[0]["startTime"] == "17:00:00", rows[0]["startTime"])

    rows = [{"date": "2026-09-15", "action": "CREATE", "seconds": 20 * 3600},
            {"date": "2026-09-15", "action": "CREATE", "seconds": 4 * 3600}]
    apply_script.assign_start_times(rows, {}, "08:00:00")
    # wrapping past midnight would move the row to another date without saying so
    check("an overrunning day is clamped, not wrapped", rows[1]["startTime"] == "23:59:00",
          rows[1]["startTime"])


def test_apply_payloads():
    """apply: what each payload sends, and what it deliberately leaves out"""
    create = apply_script.create_payload(
        {"date": "2026-09-15", "startTime": "08:00:00", "seconds": 28800,
         "attributes": {"_Account_": "200-17"}}, "acct")
    check("create sends startTime", create.get("startTime") == "08:00:00")
    # omitted because Tempo fills it to match the hours, which is verified
    check("create omits billableSeconds", "billableSeconds" not in create)
    # omitted because Tempo generates it from the issue
    check("create omits description", "description" not in create)
    # omitting it is how "do not touch the estimate" is expressed
    check("create omits remainingEstimateSeconds", "remainingEstimateSeconds" not in create)
    check("create never carries a bypass flag",
          "bypassPeriodClosuresAndApprovals" not in create)
    check("create sends attributes as a key/value list",
          create["attributes"] == [{"key": "_Account_", "value": "200-17"}])

    base = {"date": "2026-09-15", "seconds": 14400, "afterBillableSeconds": 14400}
    quiet = apply_script.update_payload(dict(base, sendBillable=False), "acct")
    loud = apply_script.update_payload(dict(base, sendBillable=True), "acct")
    check("update omits billable when Tempo would get it right",
          "billableSeconds" not in quiet)
    check("update sends billable when Tempo would leave it too high",
          loud.get("billableSeconds") == 14400)
    # omitted so a start time the user chose in the UI survives,
    # and so the UTC shift that comes from sending it cannot happen
    check("update omits startTime", "startTime" not in loud)
    check("update never carries a bypass flag",
          "bypassPeriodClosuresAndApprovals" not in loud)


def test_apply_preconditions():
    """apply: preconditions catch what changed under the proposal"""
    required = ["_Account_"]
    target = {"tempoWorklogId": 1, "startDate": "2026-09-15", "timeSpentSeconds": 21600,
              "billableSeconds": 21600, "startTime": "08:00:00",
              "issue": {"id": 10017},
              "attributes": {"values": [{"key": "_Account_", "value": "200-17"}]}}
    proposed = {"action": "UPDATE", "date": "2026-09-15", "templateId": "t1", "worklogId": 1,
                "issueId": 10017, "beforeSeconds": 21600, "beforeBillableSeconds": 21600,
                "startTime": "08:00:00", "attributes": {"_Account_": "200-17"}}
    check("an unchanged target passes",
          apply_script.check_precondition(proposed, {}, {1: target}, required) is None)

    drifts = [
        ("hours changed", dict(target, timeSpentSeconds=14400), "timeSpentSeconds"),
        ("billable changed", dict(target, billableSeconds=3600), "billableSeconds"),
        # startTime is guarded because the user can see and set it in the UI
        ("startTime changed", dict(target, startTime="13:00:00"), "startTime"),
        ("date changed", dict(target, startDate="2026-09-16"), "startDate"),
        ("issue changed", dict(target, issue={"id": 999}), "issue"),
        ("attribute changed",
         dict(target,
              attributes={"values": [{"key": "_Account_", "value": "200-16"}]}),
         "_Account_"),
    ]
    for label, mutated, expected in drifts:
        reason = apply_script.check_precondition(proposed, {}, {1: mutated}, required)
        check("%s is caught" % label, reason is not None and expected in reason, str(reason))

    check("a vanished target is caught",
          "no longer in the range" in
          (apply_script.check_precondition(proposed, {}, {}, required) or ""))

    # a create is guarded on the row COUNT, because one row per (date, template) is a
    # property of the proposal and Tempo itself permits several
    creating = {"action": "CREATE", "date": "2026-09-15", "templateId": "t1"}
    check("a create whose date gained a worklog since the proposal is caught",
          apply_script.check_precondition(
              creating, {("2026-09-15", "t1"): [target]}, {}, required) is not None)
    check("a create over an empty slot passes",
          apply_script.check_precondition(creating, {}, {}, required) is None)

    # the case the whole design change exists for: a create deliberately proposed onto a
    # date that already carried the item. apply refused every one of these, so the path
    # was broken end to end while plan, the table and the confirmation all looked right.
    onto_occupied = {"action": "CREATE", "date": "2026-09-15", "templateId": "t1",
                     "existingCount": 1}
    check("a create proposed onto an occupied date passes",
          apply_script.check_precondition(
              onto_occupied, {("2026-09-15", "t1"): [target]}, {}, required) is None)
    check("and is caught when that date gained another since",
          apply_script.check_precondition(
              onto_occupied, {("2026-09-15", "t1"): [target, target]}, {},
              required) is not None)
    check("and when it lost the one it had",
          apply_script.check_precondition(
              onto_occupied, {}, {}, required) is not None)


def test_apply_failure_reach():
    """apply: a failure's reach, keyed on Tempo's message not the status"""
    state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
    apply_script._handle_failure(
        tempo.TempoError(tempo.Scope.GROUP_MONTH, 403, "Period is closed", "/x"),
        {"date": "2026-08-17", "account": {"key": "200-17"}}, state)
    check("a closed period is remembered by month", "2026-08" in state["closedMonths"])
    check("another row in that month is covered",
          apply_script._group_reason({"date": "2026-08-31", "account": {}}, state) is not None)
    check("a row in another month is not covered",
          apply_script._group_reason({"date": "2026-09-01", "account": {}}, state) is None)
    # phrased as an inference: the closure unit being the calendar month rests on
    # three dates in one tenant, not on documentation
    covered = apply_script._group_reason({"date": "2026-08-31", "account": {}}, state)
    check("the month reason is phrased as an observation, not an assertion",
          "a row in" in covered and "were not attempted" in covered, covered)

    state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
    apply_script._handle_failure(
        tempo.TempoError(tempo.Scope.GROUP_ACCOUNT, 403, "Account is closed or archived", "/x"),
        {"date": "2026-09-01", "account": {"key": "200-17"}}, state)
    # keyed on the project account, not the template: one account pairs with several issues
    check("a closed account is remembered by account key", "200-17" in state["closedAccounts"])
    check("another template on that account is covered",
          apply_script._group_reason(
              {"date": "2026-09-02", "account": {"key": "200-17"}}, state) is not None)
    check("a different account is not covered",
          apply_script._group_reason(
              {"date": "2026-09-02", "account": {"key": "200-16"}}, state) is None)

    try:
        apply_script._handle_failure(
            tempo.TempoError(tempo.Scope.GLOBAL, 401, "Unauthorized", "/x"),
            {"date": "2026-09-01", "account": {}}, state)
        check("a global failure stops the run", False, "did not raise")
    except apply_script.GlobalStop as stop:
        check("a global failure stops the run", True)
        # never paraphrased to "an authentication problem": an approval-status refusal
        # lands in this bucket and is not an authentication problem
        check("the stop carries Tempo's own wording", "Unauthorized" in stop.reason)


def test_skill_md_generated():
    """SKILL.md's factual tables are generated, not transcribed"""
    # every table of names, exit codes and field names in SKILL.md is rendered by
    # tests/contract.py from the scripts' own constants. This is the check that they
    # have not drifted -- it replaces a dozen "this literal appears somewhere" pins,
    # which passed happily while three reviews found the prose around them wrong.
    generator = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract.py")
    done = subprocess.run([sys.executable, "-X", "utf8", generator, "--check"],
                          capture_output=True, text=True)
    check("SKILL.md matches the scripts' constants", done.returncode == 0,
          (done.stdout + done.stderr).strip().replace(chr(10), " | "))

    # what the generator cannot check: that the declared field list is what a build emits.
    # Without this the list could be internally consistent and still wrong.
    # the tenant's answers are canned, so this runs with no token,
    # and tests/live_check.py repeats it against a real build.
    with _Stubs() as stubs:
        stubs.set(tempo, "resolve_author_account_id",
                  lambda config=None, token=None: ("acct-1", "config"))
        stubs.set(tempo, "read_all", _canned_read_all)
        built = catalogue.build(8, _OFFLINE, "t", datetime.date(2026, 9, 14))
    if built["templates"]:
        emitted = set(built["templates"][0])
        declared = {name for name, _meaning in catalogue.TEMPLATE_FIELDS}
        check("every declared template field is emitted", declared <= emitted,
              "missing: %s" % sorted(declared - emitted))
        check("every emitted template field is declared", emitted <= declared,
              "undeclared: %s" % sorted(emitted - declared))
    else:
        check("template field check", False, "no templates to compare against")

    # and the direction a generator cannot express: a name that was retired must not
    # linger in the surrounding prose, where nothing regenerates it
    for name, path in sorted(_PROSE.items()):
        check("%s is readable" % name, os.path.exists(path))
    for retired in ("`language`", "!! NOT WRITEABLE", "`dateKind` makes `ask`",
                    "explicit` makes `ask", "Weekends are excluded"):
        for name, path in sorted(_PROSE.items()):
            if not os.path.exists(path):
                continue
            text = io.open(path, encoding="utf-8").read()
            check("retired wording %r is gone from %s" % (retired, name),
                  retired not in text)


def test_second_review():
    """the defects the second review found stay fixed"""
    # each of these was a real defect that reached a user-visible wrong number or
    # status. They are pinned here because every one of them passed the previous
    # 195 checks -- the suite tested guarantees, not these behaviours.

    # a row the user already saw blocked is labelled as such, so the result text does
    # not read it as a precondition that went stale after they confirmed
    check("the blocked-at-plan marker exists",
          apply_script.BLOCKED_AT_PLAN
          and apply_script.BLOCKED_AT_PLAN.startswith("blocked when the proposal"))

    # a superseded proposal needs its own code: sharing exit 3 with "no such proposal"
    # made two situations with opposite responses indistinguishable
    check("superseded has its own exit code", apply_script.EXIT_SUPERSEDED == 4)
    check("it is distinct from every other apply code",
          len({apply_script.EXIT_ALL_WRITTEN, apply_script.EXIT_PARTIAL,
               apply_script.EXIT_GLOBAL_STOP, apply_script.EXIT_USAGE,
               apply_script.EXIT_SUPERSEDED}) == 5)
    check("Superseded is a ConfigError so load_proposal's callers still catch it",
          issubclass(apply_script.Superseded, tempo.ConfigError))

    # overlap is decided on the dates a proposal actually covers. Comparing spans made
    # a proposal for the 1st and the 30th collide with every date between them.
    wide = {"rows": [{"date": "2026-09-01"}, {"date": "2026-09-30"}]}
    middle = {"rows": [{"date": "2026-09-15"}]}
    same = {"rows": [{"date": "2026-09-30"}]}
    check("a span that shares no date does not collide",
          not (apply_script._dates_of(wide) & apply_script._dates_of(middle)))
    check("a genuinely shared date does collide",
          bool(apply_script._dates_of(wide) & apply_script._dates_of(same)))

    # Results is constructed on every apply, and `uuid` was missing from the module
    # for one commit -- 195 checks passed because nothing here built one
    # Results reads RESULT_DIR when it is called, so pointing it at a temporary directory
    # keeps the probe's record out of the user's state.
    original_result_dir = apply_script.RESULT_DIR
    result_dir = apply_script.RESULT_DIR = tempfile.mkdtemp()
    probe = None
    try:
        probe = apply_script.Results("selfcheck-probe")
        entry = probe.record(
            {"date": "2026-01-01", "templateId": "t", "issueId": 1,
             "account": {"key": "K"}, "hours": 1.0, "action": "CREATE"},
            apply_script.WRITTEN, "probe", worklog_id=1)
        check("Results records a run id", bool(entry.get("run")))
        # two removals folded onto one date share a date, a template and an action, so
        # without the worklog id the second was never recorded when a run stopped between
        # them. A create keys without it, because the proposal names no worklog and the
        # record names the id Tempo assigned.
        for worklog_id in (11, 12):
            probe.record(
                {"date": "2026-01-02", "templateId": "t", "issueId": 1,
                 "account": {"key": "K"}, "hours": 0.0, "action": "DELETE",
                 "worklogId": worklog_id},
                apply_script.WRITTEN, "removed")
        probe.close()
        seen = apply_script.Results.previous("selfcheck-probe")
        check("previous() reads an earlier run back",
              seen.get(("2026-01-01", "t", "CREATE", None), {}).get("status")
              == apply_script.WRITTEN)
        check("two folded removals stay two records",
              len([k for k in seen if k[2] == "DELETE"]) == 2, sorted(seen))
    finally:
        _release_result_dir(result_dir, original_result_dir, [probe])

    # what happens on an occupied date follows the SHAPE of the date input. Keying it
    # on "explicit" made a named range abort on its first occupied day.
    # an occupied date used to produce no row, which was the tool deciding that a second
    # entry must be a duplicate. Verified live that Tempo accepts several worklogs for one
    # issue on one date, so the row is proposed and the reader judges it.
    candidate = {
        "date": "2026-09-15",
        "templateId": "tf10a378a",
        "template": {"issueId": 1, "attributes": {}, "account": {"key": "200-17"},
                     "count": 4, "lastUsed": "2026-09-08", "dailyTotalHours": [8.0],
                     "daysUsed": 1, "descriptions": []},
        "steps": [{"hours": 3.0}],
        "existing": [{"tempoWorklogId": 1, "timeSpentSeconds": 3 * 3600}],
    }
    produced = plan._resolve_action(candidate, [])
    check("an occupied date still produces a row", len(produced) == 1)
    check("and that row is a second worklog, not a change",
          produced[0]["action"] == "CREATE")
    check("carrying only the hours asked for", produced[0]["hours"] == 3.0)
    check("and what the date already holds, for the note",
          produced[0]["existingSeconds"] == 3 * 3600)

    # the delete-in-disguise guard is retired: a small update is a legitimate correction
    # now that reducing a row to nothing deletes it outright
    template = {"dailyTotalHours": [8.0], "daysUsed": 1, "account": {"key": "K", "status": "OPEN"}}
    for action in ("CREATE", "UPDATE"):
        row = {"action": action, "date": "2026-09-16", "templateId": "t", "issueId": 1,
               "seconds": 3600, "hours": 1.0, "attributes": {"_A_": "K"},
               "account": {"key": "K", "status": "OPEN", "resolved": True},
               "blocked": False, "blockedReason": None}
        plan.apply_local_checks([row], {"t": dict(template, usable=True, unusableReason=None)},
                                120, tempo.parse_date("2026-09-10"), ["_A_"])
        check("a 1h %s is allowed" % action, row["blocked"] is False,
              str(row["blockedReason"]))

    # a tenant may require no ACCOUNT-typed attribute at all, and then a template with
    # no resolvable account is perfectly writeable. Blocking it here refused every
    # template on such a tenant, for a failure that had never been observed.
    entry = {"id": "tX", "issueId": 1, "attributes": {"_Free_": "text"}, "account": None}
    catalogue._mark_usability(entry, ["_Free_"], [])
    check("no account requirement does not make a template unusable", entry["usable"] is True)


def test_third_review():
    """the defects the third review found stay fixed"""
    # the day total is the number the reader acts on, so a row that will never be
    # written must not be in it -- and the worklog a blocked update names must stay
    # visible, or the same fix turns an overstatement into an understatement.
    def item(issue, hours, action="CREATE", before=None, blocked=None, notes=()):
        return {"date": "2026-09-08", "templateId": "t%d" % issue, "issueId": issue,
                "attributes": {}, "action": action, "hours": hours,
                "seconds": int(hours * 3600), "dayBeforeSeconds": before,
                "existingSeconds": before or 0, "reading": None,
                "descriptions": ["Working on work item ACME-%d" % issue],
                "account": {"key": "200-%d" % issue, "name": "N", "status": "OPEN",
                            "resolved": True},
                "blocked": bool(blocked), "blockedReason": blocked,
                "annotations": list(notes)}

    def rendered(proposed, existing=()):
        return "\n".join(plan._body([("2026-09-08", list(proposed),
                                                  list(existing))]))

    # the day total had a green check behind a function the week grid never called, so
    # the live total counted blocked rows while the anchor said it left them out
    out = rendered([item(17, 8.0, before=8 * 3600, blocked="closed"), item(16, 1.0)])
    # a blocked row leaves the date holding what it already had, so the total carries
    # those 8 hours plus the 1 that will land. Contributing zero instead dropped them,
    # because a blocked cell takes the slot its already-logged row would have used.
    check("the day total keeps what a blocked row leaves behind",
          [l for l in out.splitlines() if "day total" in l][0].strip().endswith("9.00"),
          out)
    check("and the blocked cell is still shown with its marker", "8.00->16.00!" in out)
    check("with its reason under that week", "NOT WRITEABLE: closed" in out)

    # a date that comes to nothing is not the same as a date nobody asked about
    out = rendered([item(17, 0.0, action="DELETE", before=8 * 3600)])
    check("a date emptied by the proposal shows 0.00, not a blank",
          [l for l in out.splitlines() if "day total" in l][0].strip().endswith("0.00"),
          out)

    # a note belongs to a cell, and the week grid dropped every one of them
    out = rendered([item(17, 8.0, notes=["month may be closed"])])
    check("a cell's note is printed under its week",
          "month may be closed" in out, out)

    # two work items whose labels genuinely match used to overwrite each other. They have
    # to have no account key: with one, _ambiguous_accounts appends the issue id and the
    # labels never collide, which is why the first version of this check passed either
    # way. Against the pre-fix renderer this shows one label and a day total of 4.00 for
    # twelve hours that still get written, so the check discriminates.
    a, b = item(17, 8.0), item(17, 4.0)
    for row, issue in ((a, 17), (b, 99)):
        row["account"] = None
        row["issueId"] = issue
        row["templateId"] = "t%d" % issue
        row["descriptions"] = []
    out = rendered([a, b])
    labelled = [l for l in out.splitlines() if l.startswith("  (no project account)")]
    check("two items with identical labels both survive", len(labelled) == 2, out)
    check("and the day total counts both",
          [l for l in out.splitlines() if "day total" in l][0].strip().endswith("12.00"),
          out)

    # every renderer check used one date, so the claim that a week's notes sit under THAT
    # week -- rather than after the last table, where week one's reasons used to land --
    # was asserted nowhere. Against the pre-fix renderer the note is absent altogether,
    # because annotations were computed and never read.
    first = item(17, 8.0, notes=["month may be closed"])
    second = item(16, 1.0)
    second["date"] = "2026-09-15"
    out = "\n".join(plan._body([("2026-09-08", [first], []),
                                            ("2026-09-15", [second], [])]))
    lines = out.splitlines()
    headings = [i for i, l in enumerate(lines) if l.strip().startswith("week of")]
    note_at = [i for i, l in enumerate(lines) if "month may be closed" in l]
    check("a two-week proposal renders two tables", len(headings) == 2, out)
    check("and week one's note sits before week two's table",
          bool(note_at) and note_at[0] < headings[1], out)

    # the blocked reason, likewise: under its own week
    blocked_first = item(17, 8.0, blocked="account is closed")
    out = "\n".join(plan._body([("2026-09-08", [blocked_first], []),
                                            ("2026-09-15", [second], [])]))
    lines = out.splitlines()
    headings = [i for i, l in enumerate(lines) if l.strip().startswith("week of")]
    reason_at = [i for i, l in enumerate(lines) if "NOT WRITEABLE" in l]
    check("a blocked reason sits under its own week",
          bool(reason_at) and reason_at[0] < headings[1], out)

    # --dry-run was removed: nothing in any plan asked for it, it appended untried rows
    # to the record file the skill reads to decide what landed, and its exit rules were
    # wrong in both directions. Pinned so it does not come back by accident.
    for script, flag in (("apply.py", "dry"), ("catalogue.py", "--out")):
        check("%s exposes no %s flag" % (script, flag),
              flag not in io.open(os.path.join(SCRIPTS_DIR, script),
                                  encoding="utf-8").read().lower())

    # re-run recognition reached CREATE only, so an UPDATE an earlier run had applied
    # came back as skipped-precondition -- the report that makes a user log twice
    row = {"action": "UPDATE", "worklogId": 77, "seconds": 6 * 3600,
           "afterBillableSeconds": 6 * 3600}
    mine = {"status": apply_script.WRITTEN, "worklogId": 77, "run": "r1"}
    applied = {77: {"timeSpentSeconds": 6 * 3600, "billableSeconds": 6 * 3600}}
    untouched = {77: {"timeSpentSeconds": 8 * 3600, "billableSeconds": 8 * 3600}}
    check("an update our earlier run applied is recognised",
          apply_script._is_our_earlier_write(row, mine, applied) is True)
    check("one still holding its old hours is not",
          apply_script._is_our_earlier_write(row, mine, untouched) is False)
    check("a row no earlier run wrote is not",
          apply_script._is_our_earlier_write(row, None, applied) is False)
    check("a create is still recognised by its worklog id",
          apply_script._is_our_earlier_write(
              {"action": "CREATE"}, {"status": apply_script.WRITTEN, "worklogId": 77},
              applied) is True)

    # what apply.py can write, read off its own dispatch rather than listed here: a
    # capability that changes takes its documentation with it, which is how "delete is
    # out of scope" survived delete being implemented
    apply_source = io.open(os.path.join(SCRIPTS_DIR, "apply.py"), encoding="utf-8").read()
    # matched on the stem, because the prose says "deleting worklogs" rather than
    # "delete" -- and "delete" is not a substring of "deleting", which is how the first
    # version of this check passed against the very wording it was written to catch
    implemented = set()
    for stem, writer in (("creat", "_write_creates"),
                         ("updat", "_write_updates"),
                         ("delet", "_write_deletes")):
        if "def %s(" % writer in apply_source:
            implemented.add(stem)
    check("apply.py implements all three write actions",
          implemented == {"creat", "updat", "delet"}, sorted(implemented))

    # the sections that say what the tool cannot do must not name one of them
    cannot = {}
    # the heading is matched loosely because it is prose someone may reword -- the check
    # is about the capability claim under it, and a rename must not silently disable it
    for name, path, pattern in (
            ("SKILL.md", _PROSE["SKILL.md"], r"^## What this skill (?:does|will) not do$"),
            ("README.md", _PROSE["README.md"], r"^## What it (?:does|will) not do$")):
        text = io.open(path, encoding="utf-8").read()
        found = re.search(pattern, text, re.M)
        check("%s still has a capability section" % name, found is not None, pattern)
        if found is None:
            continue
        start = found.end()
        # the first paragraph is the flat list of things it does not do
        body = text[start:].strip().split("\n\n")[0].lower()
        cannot[name] = body
    for name, body in sorted(cannot.items()):
        for action in sorted(implemented):
            check("%s does not claim %s is unsupported" % (name, action),
                  action not in body, body[:70])

    # and the same for the phrasings that describe an absent capability in passing
    for retired in ("delete is out of scope",
                    "deleting a worklog is not something this skill does",
                    "this plugin does not implement",
                    "delete is not implemented"):
        for name, path in sorted(_PROSE.items()):
            text = io.open(path, encoding="utf-8").read().lower()
            check("%r is gone from %s" % (retired, name), retired not in text)


def test_grouped_render():
    """the grouped render"""
    proposed = _proposed

    # what a cell says is what the item comes to that day, whichever way it gets there
    check("a create from nothing reads as an addition",
          plan._cell_text(proposed("CREATE", 8 * 3600)) == "+8.00")
    check("a create onto existing hours reads as a transition",
          plan._cell_text(proposed("CREATE", 8 * 3600, before=7 * 3600)) == "7.00->15.00")
    check("an update reads as a transition to its result",
          plan._cell_text(proposed("UPDATE", 90 * 60, before=2 * 3600)) == "2.00->1.50")
    check("a removal reads as a transition to nothing",
          plan._cell_text(proposed("DELETE", 0, before=2 * 3600)) == "2.00->0.00")

    # a blocked row must still say why: a rewrite dropped that once already
    groups = [("2026-09-08",
               [proposed("CREATE", 2 * 3600, blocked="hours would be 0")], [])]
    rendered = "\n".join(plan._body(groups))
    check("a blocked cell is marked", "!" in rendered)
    check("and its reason is stated below the week",
          "NOT WRITEABLE: hours would be 0" in rendered)
    # read off the day total line itself, and its last figure whole,
    # since a positional line read the item row and an endswith would take 10.00 for 0.00
    totals = [l for l in rendered.splitlines() if "day total" in l]
    check("a blocked row does not count towards the day total",
          bool(totals) and totals[0].split()[-1] == "0.00", rendered)

    # the account key and the Jira key are different vocabularies, and the table used to
    # show only the first while the skill invites naming the second
    check("a Jira key is taken from the description",
          plan._jira_key(["Working on work item ACME-182"]) == "ACME-182")
    check("and absent when there is none", plan._jira_key(["no key here"]) is None)
    check("and absent when there is no description at all", plan._jira_key(None) is None)

    # the key is resolved per issue, so a worklog whose description someone edited in the
    # UI still shares a row with its siblings instead of splitting the work item in two
    edited = proposed("CREATE", 3600)
    edited["descriptions"] = ["reworded by hand"]
    normal = proposed("CREATE", 3600)
    normal["descriptions"] = ["Working on work item ACME-182"]
    rendered = plan._body([("2026-09-08", [normal], []), ("2026-09-09", [edited], [])])
    labels = [l for l in rendered if "200-17" in l]
    check("an edited description does not split the work item",
          len(labels) == 1, labels)
    check("and the row still carries the key", "(ACME-182)" in labels[0])

    groups = [("2026-09-08", [proposed("CREATE", 8 * 3600)], [])]
    groups[0][1][0]["descriptions"] = ["Working on work item ACME-182"]
    rendered = "\n".join(plan._body(groups))
    check("the label carries it", "(ACME-182)" in rendered)
    check("and nothing states a template's history below the table",
          "in your history" not in rendered)
    check("nor the billable behaviour", "Billable hours" not in rendered)

    # four notes were dropped as clutter: each restated the cell beside it, and together
    # they pushed `month may be closed` -- the one that says a row may be refused --
    # to the bottom of a list nobody read to the end of
    earlier = proposed("CREATE", 8 * 3600, before=7 * 3600)
    earlier["date"] = "2026-08-08"
    earlier["existingCount"] = 1
    rows = [earlier]
    plan.annotate(rows, {}, datetime.date(2026, 9, 14), {}, [])
    notes = rows[0]["annotations"]
    check("a create onto occupied hours is not annotated with what is already there",
          not any("already" in n for n in notes), str(notes))
    check("but a date in an earlier month still says the month may be closed",
          notes == ["month may be closed"], str(notes))

    update = proposed("UPDATE", 6 * 3600, before=7 * 3600, reading="read as: set to 6.00h")
    update["beforeSeconds"] = 7 * 3600
    update["beforeBillableSeconds"] = 7 * 3600
    update["afterBillableSeconds"] = 6 * 3600
    plan.annotate([update], {}, datetime.date(2026, 9, 14), {}, [])
    check("a change narrates neither its reading nor its billable",
          update["annotations"] == [], str(update["annotations"]))
    # the reading itself must survive: the renderer reads it to spot a folded removal
    check("while the reading is still on the row for the renderer to use",
          update["reading"] == "read as: set to 6.00h")


def test_show_grid():
    """show renders the same grid, with nothing internal in it"""
    proposed = _proposed

    # two layouts for one set of facts meant a change to the grid reached only one of
    # them, and the chronological one led with a worklog id Tempo never shows the user
    worklogs = [
        {"startDate": "2026-09-08", "timeSpentSeconds": 7 * 3600, "tempoWorklogId": 90001,
         "issue": {"id": 10017}, "description": "Working on work item ACME-182",
         "attributes": {"values": [{"key": "_Account_", "value": "200-17"}]}},
        {"startDate": "2026-09-08", "timeSpentSeconds": 3600, "tempoWorklogId": 90002,
         "issue": {"id": 10016}, "description": "Working on work item ACME-177",
         "attributes": {"values": [{"key": "_Account_", "value": "200-16"}]}},
    ]
    accounts = {"200-17": {"key": "200-17", "name": "Dev", "status": "OPEN"},
                "200-16": {"key": "200-16", "name": "Gov", "status": "OPEN"}}
    out = show.render(worklogs, datetime.date(2026, 9, 7), datetime.date(2026, 9, 13),
                      accounts, ["_Account_"])
    check("show lays the week out as a grid", "week of 2026-09-07" in out, out)
    check("with the weekday as the column", "Tue 08" in out, out)
    check("and the day total beneath it",
          any(l.strip().endswith("8.00") and "day total" in l for l in out.splitlines()),
          out)
    check("the Jira key reaches the label the same way it does in a proposal",
          "(ACME-182)" in out and "(ACME-177)" in out, out)
    for internal in ("90001", "90002", "Worklog"):
        check("%s is not in what show prints" % internal, internal not in out, out)

    # Tempo accepts several worklogs for one item on one date, and a cell is meant to be
    # the item's total for that day. Keeping only the first understated the cell AND the
    # day total -- the direction that makes someone log the same hours again.
    def existing(seconds, issue=1):
        return {"date": "2026-11-20", "seconds": seconds, "issueId": issue,
                "attributes": {}, "descriptions": ["Working on work item ACME-182"],
                "account": {"key": "200-17", "name": "Dev", "status": "OPEN"}}

    out = "\n".join(plan._body(
        [("2026-11-20", [], [existing(3600), existing(3600)])]))
    cell = [l for l in out.splitlines() if l.startswith("  200-17")][0]
    check("two records for one item on one date add up in the cell",
          cell.strip().endswith("2.00"), cell)
    check("and the day total counts both",
          [l for l in out.splitlines() if "day total" in l][0].strip().endswith("2.00"),
          out)

    # but a proposed row still owns its cell: it already says what the day comes to
    proposal_row = proposed("UPDATE", 90 * 60, before=2 * 3600)
    proposal_row["date"] = "2026-11-20"
    out = "\n".join(plan._body(
        [("2026-11-20", [proposal_row], [existing(3600), existing(3600)])]))
    cell = [l for l in out.splitlines() if l.startswith("  200-17")][0]
    check("an existing row does not land on top of a proposed one",
          "2.00->1.50" in cell, cell)

    # the rows that only fold records together have nothing of their own to show
    groups = [("2026-09-08",
               [proposed("UPDATE", 90 * 60, before=2 * 3600),
                proposed("DELETE", 0, reading=plan.FOLDED)], [])]
    rendered = "\n".join(plan._body(groups))
    check("a folded removal gets no cell of its own",
          rendered.count("200-17") == 1)
    check("and the cell it folded into carries the result",
          "2.00->1.50" in rendered)


def test_cell_is_one_work_item():
    """a cell is one work item, and a repeated label says what differs"""
    def work_item(issue, hours, attributes, account=None):
        return {"date": "2026-09-08", "templateId": "x", "issueId": issue,
                "attributes": attributes, "action": "CREATE", "hours": hours,
                "seconds": int(hours * 3600), "dayBeforeSeconds": None,
                "existingSeconds": 0, "reading": None, "descriptions": [],
                "annotations": [], "blocked": False, "blockedReason": None,
                "account": account or {"key": "200-17", "name": "Dev",
                                       "status": "OPEN", "resolved": True}}

    def render_one(rows):
        return "\n".join(plan._body([("2026-09-08", rows, [])]))

    # a work item is its issue AND every required attribute value. Keying a cell on the
    # account alone merged two templates differing only in a second required attribute:
    # one lost its hours from the table and the day total while still being written.
    out = render_one([
        work_item(1, 8.0, {"_Account_": "200-17", "_Category_": "DEV"}),
        work_item(1, 4.0, {"_Account_": "200-17", "_Category_": "SUPPORT"})])
    rows = [l for l in out.splitlines() if l.startswith("  200-17")]
    check("two items differing only in a second attribute both survive",
          len(rows) == 2, out)
    check("and the day total counts both",
          [l for l in out.splitlines() if "day total" in l][0].strip().endswith("12.00"),
          out)
    check("the repeated label says which value differs",
          "DEV" in rows[0] and "SUPPORT" in rows[1], rows)
    check("without repeating what the label already carries",
          "_Category_=" not in out, out)

    # the older collision, one account carrying two issues, still separates on the issue
    out = render_one([work_item(1, 8.0, {"_Account_": "200-17"}),
                      work_item(2, 4.0, {"_Account_": "200-17"})])
    check("one account with two issues separates on the issue id",
          "issue 1" in out and "issue 2" in out, out)

    # and nothing is appended when the labels already differ
    out = render_one([
        work_item(1, 8.0, {"_Account_": "200-17"}),
        work_item(2, 4.0, {"_Account_": "200-16"},
                  {"key": "200-16", "name": "Gov", "status": "OPEN", "resolved": True})])
    check("distinct labels carry no discriminator",
          "issue 1" not in out and "issue 2" not in out, out)

    # a create whose worklog id the bulk response never returned is recorded written with
    # no id, so looking that id up could not confirm it and the re-run reported hours it
    # had written itself as not landed -- the one direction that makes a user log twice
    idless = {"action": "CREATE", "date": "2026-09-08", "templateId": "t",
              "worklogId": None}
    check("a create written without a captured id is recognised on a re-run",
          apply_script._is_our_earlier_write(
              idless, {"status": apply_script.WRITTEN, "worklogId": None}, {}) is True)
    check("but a row an earlier run did not write is not",
          apply_script._is_our_earlier_write(
              idless, {"status": apply_script.SKIPPED_PRECONDITION, "worklogId": None},
              {}) is False)


def test_verb_below_nothing():
    """a verb cannot take a date below nothing"""
    tpl = {"issueId": 1, "attributes": {}, "count": 4, "lastUsed": "2026-09-08",
           "dailyTotalHours": [8.0], "daysUsed": 4, "descriptions": [],
           "account": {"key": "200-17", "status": "OPEN", "resolved": True}}

    def overshoot(steps, held):
        candidate = {"date": "2026-09-15", "templateId": "t", "template": tpl,
                     "steps": steps,
                     "existing": [{"tempoWorklogId": 5, "timeSpentSeconds": int(h * 3600),
                                   "billableSeconds": int(h * 3600),
                                   "startTime": "08:00:00", "startDate": "2026-09-15"}
                                  for h in held]}
        rows = plan._resolve_action(candidate, [])
        plan.apply_local_checks(rows, {"t": dict(tpl, usable=True, unusableReason=None)},
                                None, datetime.date(2026, 9, 14), [])
        return rows[0]

    # subtracting past zero produced a negative UPDATE that was sent, where Tempo's
    # minimum of one second turns it into an opaque 400
    row = overshoot([{"verb": "SUBTRACT", "hours": 5.0}], [2.0])
    check("subtracting more than the date holds is blocked", row["blocked"] is True)
    check("and says what to do instead",
          "remove the row" in (row["blockedReason"] or ""))

    # `-0.0 == 0` is true, so a residue that rounded to nothing used to delete the
    # worklog rather than be refused
    row = overshoot([{"verb": "SUBTRACT", "hours": 1.504}], [1.5])
    check("an overshoot that rounds to nothing does not delete", row["action"] != "DELETE")
    check("it is blocked instead", row["blocked"] is True)

    # rounding happens before the zero test, so a residue on EITHER side arrives as a
    # clean 0.00. The first fix guarded only the negative side.
    row = overshoot([{"verb": "SUBTRACT", "hours": 1.496}], [1.5])
    check("a positive residue that rounds to nothing does not delete",
          row["action"] != "DELETE")

    # an exact zero is still the removal
    row = overshoot([{"verb": "SUBTRACT", "hours": 1.5}], [1.5])
    check("subtracting exactly what is there removes it", row["action"] == "DELETE")
    check("and is not blocked", row["blocked"] is False)

    # a removal folded into a blocked row must be blocked with it. Every other block is a
    # property of the template or the date and catches a whole group; this guard was the
    # first that could catch one row of one, and the removals went ahead and destroyed a
    # worklog for an instruction the code had just refused.
    folding = {"date": "2026-09-15", "templateId": "t", "template": tpl,
               "steps": [{"verb": "SUBTRACT", "hours": 5.0}],
               "existing": [{"tempoWorklogId": 5 + i, "timeSpentSeconds": 3600,
                             "billableSeconds": 3600, "startTime": "08:00:00",
                             "startDate": "2026-09-15"} for i in range(2)]}
    rows = plan._resolve_action(folding, [])
    plan.apply_local_checks(rows, {"t": dict(tpl, usable=True, unusableReason=None)},
                            None, datetime.date(2026, 9, 14), [])
    check("an overshoot blocks the row carrying the result", rows[0]["blocked"] is True)
    check("and every removal folded into it",
          all(r["blocked"] for r in rows[1:]), [r["action"] for r in rows])
    check("saying why", "folded into" in (rows[1]["blockedReason"] or ""))


def test_cold_start():
    """cold start is told apart by type, not by wording"""
    # setup.py read `"no worklogs visible" in message` to decide which situation it was
    # in. Rewording that message for clarity turned every cold start into a misreported
    # ambiguous author, and every check still passed.
    check("an empty timesheet raises its own type",
          issubclass(tempo.NoWorklogs, tempo.ConfigError))
    check("so existing ConfigError handlers still catch it",
          isinstance(tempo.NoWorklogs("x"), tempo.ConfigError))
    source = io.open(os.path.join(SCRIPTS_DIR, "setup.py"), encoding="utf-8").read()
    check("setup branches on the type", "except tempo.NoWorklogs" in source)
    check("and no longer on the wording", "no worklogs visible" not in source)
    check("the ambiguous-author status is gone with its exit code",
          "AMBIGUOUS" not in source)


def _built_for_proposal():
    """The catalogue build_proposal reads, as catalogue.build would return it for one template."""
    account = {"key": "200-17", "name": "Widget Platform \u2013 Development", "status": "OPEN",
               "attributeKey": "_Account_", "resolved": True, "collidesWith": []}
    template = {"id": "tf10a378a", "issueId": 10017, "attributes": {"_Account_": "200-17"},
                "count": 4, "lastUsed": "2026-09-08",
                "descriptions": ["Working on work item ACME-182"],
                "dailyTotalHours": [8.0], "daysUsed": 4, "account": account,
                "usable": True, "unusableReason": None}
    return {"templates": [template],
            "requiredAttributes": [{"key": "_Account_", "name": "Account", "type": "ACCOUNT"}],
            "weekendHistory": {"count": 0, "lastUsed": None, "dates": []}}


def test_ids_order_and_wrapper():
    """ids order by time, and a wrapper failure is always exit 2"""
    # apply compares proposal ids as strings to decide which is newer. At one-second
    # resolution two made in the same second ordered by their random suffix, so the
    # newer one could be judged superseded and refused with no override.
    # the id is made inside build_proposal, so that is what runs here,
    # with every collaborator that would reach Tempo replaced by its canned answer.
    intent = {"userInput": "log 8 hours on the 15th",
              "items": [{"templateId": "tf10a378a", "hours": 8.0}]}
    with _Stubs() as stubs:
        stubs.set(tempo, "resolve_author_account_id",
                  lambda config=None, token=None: ("acct-1", "config"))
        stubs.set(catalogue, "build",
                  lambda weeks, config=None, token=None, today=None: _built_for_proposal())
        stubs.set(catalogue, "_accounts_by_key",
                  lambda config, token: {"200-17": _built_for_proposal()["templates"][0]["account"]})
        stubs.set(plan, "resolve_dates",
                  lambda intent, author, weekend, config, token:
                  ([datetime.date(2026, 9, 15)], [], "explicit"))
        stubs.set(plan, "read_snapshot", lambda dates, author, config, token: [])
        stubs.set(tempo, "read",
                  lambda path, params=None, config=None, token=None:
                  {"numberOfDaysAllowedIntoFuture": 120} if path == "/4/globalconfiguration"
                  else {"no canned response": path})
        made = [plan.build_proposal(intent, _OFFLINE, "t", datetime.date(2026, 9, 14))["id"]
                for _ in range(200)]
    check("ids made back to back are distinct", len(set(made)) == len(made))
    check("and sort into the order they were made", made == sorted(made))

    plan_source = io.open(os.path.join(SCRIPTS_DIR, "plan.py"), encoding="utf-8").read()
    check("the id carries sub-second precision", '%H%M%S%f' in plan_source)

    # a wrapper that cannot run anything must say so with exit 2, never with the
    # interpreter's own code, which the skill would read against a script's exit table
    for name in ("tempo.sh", "tempo.cmd"):
        source = io.open(os.path.join(SCRIPTS_DIR, name), encoding="utf-8").read()
        check("%s proves the interpreter takes -X utf8 before using it" % name,
              '-X utf8 -c ""' in source)


def test_evals_loadable():
    """the evals stay loadable and pinned to the skill they test"""
    # these test the half this suite cannot: whether a model reading SKILL.md does what it
    # says. Nothing here runs them -- that needs a model and a token -- but a file that
    # will not parse, or an expectation list someone emptied, fails silently at the moment
    # it is needed most, which is months from now
    evals_path = os.path.join(os.path.dirname(SCRIPTS_DIR), "evals", "evals.json")
    check("the evals file is there", os.path.exists(evals_path), evals_path)
    suite = json.load(io.open(evals_path, encoding="utf-8"))

    skill_text = io.open(os.path.join(os.path.dirname(SCRIPTS_DIR), "SKILL.md"),
                         encoding="utf-8").read()
    declared = skill_text.split("name:", 1)[1].split(chr(10), 1)[0].strip()
    check("the evals name the skill they test", suite["skill_name"] == declared,
          "%s vs %s" % (suite["skill_name"], declared))

    ids = [e["id"] for e in suite["evals"]]
    check("every eval has its own id", len(set(ids)) == len(ids), str(ids))
    for entry in suite["evals"]:
        label = entry.get("name") or entry["id"]
        check("eval %s has a prompt" % label, bool(entry.get("prompt", "").strip()))
        # an eval with no expectations grades as a pass on nothing at all
        check("eval %s says what must be true of the run" % label,
              len(entry.get("expectations") or []) >= 2,
              str(entry.get("expectations")))


def test_entry_points():
    """every script's entry point still runs"""
    # three times now a signature or import change has passed every unit check and
    # crashed on the first real invocation, because nothing here walked the entry path.
    # --help reaches the module import and the argument handling without touching Tempo.
    # Some scripts reject it with their own usage message, which is a pass: the oracle is
    # the absence of a traceback, not the exit code.
    for name in ("tempo", "setup", "catalogue", "show", "plan", "apply"):
        finished = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(SCRIPTS_DIR, name + ".py"), "--help"],
            capture_output=True)
        stderr = finished.stderr.decode("utf-8", "replace")
        check("%s.py imports and reaches its argument handling" % name,
              "Traceback" not in stderr)

    # and the one path a --help cannot reach: building a proposal end to end.
    source = io.open(os.path.join(SCRIPTS_DIR, "plan.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            calls[node.name] = len(node.args.args)
    for caller, callee in (("build_proposal", "build_rows"),
                           ("build_rows", "_resolve_action"),
                           ("build_proposal", "build_header"),
                           ("build_proposal", "resolve_dates")):
        wanted = calls.get(callee)
        used = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee]
        check("%s calls %s with the arity it declares" % (caller, callee),
              bool(used) and all(len(c.args) <= wanted for c in used))


def test_read_post_passes():
    """read_post lets the read-only POST through"""
    # the suite pins both refusals, and a policy that refused everything would pass them,
    # so the direction that must keep working is pinned here
    sent = []
    with _Stubs() as stubs:
        stubs.set(tempo, "_send", lambda method, url, token, body=None:
                  sent.append((method, url, token, body)) or {"results": []})
        try:
            answer = tempo.read_post("/4/worklogs/search", {"from": "2026-09-01"}, _OFFLINE, "t")
            check("read_post() lets /4/worklogs/search through", True)
        except Exception as error:
            answer = None
            check("read_post() lets /4/worklogs/search through", False,
                  "%s: %s" % (type(error).__name__, error))
    check("exactly one request is sent", len(sent) == 1, str(sent))
    if sent:
        method, url, token, body = sent[0]
        check("it is a POST", method == "POST", method)
        check("to the configured base, with no doubled version segment",
              url == "http://127.0.0.1:9/4/worklogs/search", url)
        check("carrying the explicit token", token == "t", token)
        check("and the body unchanged", body == {"from": "2026-09-01"}, str(body))
    check("the answer is what the transport returned", answer == {"results": []}, str(answer))


def test_send_decoding():
    """_send decodes UTF-8 and classifies a refusal without echoing the token"""
    # the live check's "UTF-8 survives the transport" block, offline: the bytes are
    # the UTF-8 encoding of EN DASH itself, not a \u escape json would decode for us
    real_send = tempo._send
    name = "Widget Platform \u2013 Development"
    payload = json.dumps({"results": [{"key": "200-17", "name": name}]},
                         ensure_ascii=False).encode("utf-8")
    check("the canned body carries the raw UTF-8 bytes", b"\xe2\x80\x93" in payload)

    requests = []

    def answering(request):
        requests.append(request)
        return io.BytesIO(payload)

    with _Stubs() as stubs:
        stubs.set(tempo.urllib.request, "urlopen", answering)
        decoded = real_send("GET", "http://127.0.0.1:9/4/accounts", "t")
    got = decoded["results"][0]["name"]
    check("the decoded name keeps U+2013", got == name, ascii(got))
    check("and carries no cp1252 mojibake", "\u00e2\u20ac" not in got, ascii(got))
    check("the request carries the bearer header",
          bool(requests) and requests[0].get_header("Authorization") == "Bearer t")

    # a token file is put in place so _scrub has a secret to find,
    # and the secret is put in the URL so there is something to redact
    secret = "tokenvalueZZ9qq"
    scratch = os.path.join(tempfile.mkdtemp(), "token.txt")
    with io.open(scratch, "w", encoding="utf-8", newline="") as handle:
        handle.write(secret + "\n")
    url = "http://127.0.0.1:9/4/worklogs/90001?probe=" + secret

    def refusing(request):
        raise tempo.urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {},
            io.BytesIO(b'{"errors":[{"message":"Period is closed"}]}'))

    def unreachable(request):
        raise tempo.urllib.error.URLError("connection refused")

    try:
        with _Stubs() as stubs:
            stubs.set(tempo, "TOKEN_PATH", scratch)
            stubs.set(tempo.urllib.request, "urlopen", refusing)
            try:
                real_send("PUT", url, secret, {"timeSpentSeconds": 3600})
                check("an HTTPError becomes a TempoError", False, "no exception raised")
            except tempo.TempoError as error:
                check("an HTTPError becomes a TempoError", True)
                check("classified by Tempo's message", error.scope == tempo.Scope.GROUP_MONTH,
                      error.scope)
                check("with the status kept", error.status == 403, str(error.status))
                check("and Tempo's own wording kept", error.message == "Period is closed",
                      error.message)
                check("the token is redacted from the path",
                      secret not in error.path and "<redacted>" in error.path, error.path)
                check("and never reaches the message", secret not in str(error), str(error))
            except Exception as error:
                check("an HTTPError becomes a TempoError", False, type(error).__name__)

            stubs.set(tempo.urllib.request, "urlopen", unreachable)
            try:
                real_send("GET", url, secret)
                check("a transport failure becomes a TempoError", False, "no exception raised")
            except tempo.TempoError as error:
                check("a transport failure becomes a TempoError", True)
                check("and is global", error.scope == tempo.Scope.GLOBAL, error.scope)
                check("with the token redacted there too", secret not in str(error), str(error))
    finally:
        os.remove(scratch)


def test_read_all_offline():
    """read_all stays on the configured host and refuses a truncated read"""
    # metadata.next names one fixed regional host whatever host was asked,
    # so following it verbatim would move every later page off the configured baseUrl
    sent = []

    def paging(method, url, token, body=None):
        sent.append(url)
        if len(sent) == 1:
            return {"results": [{"tempoWorklogId": 90001}],
                    "metadata": {"next": "https://api.us.tempo.io/4/elsewhere?offset=50&limit=50"}}
        return {"results": [{"tempoWorklogId": 90002}], "metadata": {}}

    with _Stubs() as stubs:
        stubs.set(tempo, "_send", paging)
        rows = tempo.read_all("/4/worklogs/user/acct", {"limit": 50}, _OFFLINE, "t")
    check("both pages are returned",
          [r["tempoWorklogId"] for r in rows] == [90001, 90002], str(rows))
    check("two requests are sent", len(sent) == 2, str(sent))
    if len(sent) == 2:
        check("the second page is rebuilt onto the configured base and path",
              sent[1] == "http://127.0.0.1:9/4/worklogs/user/acct?offset=50&limit=50", sent[1])
        check("and the host metadata.next named is not used", "api.us.tempo.io" not in sent[1])
        check("nor its path", "elsewhere" not in sent[1])

    # a cap hit raises, because a short list makes every create precondition pass
    # and duplicates hours on the days the missing pages held
    endless = []

    def forever(method, url, token, body=None):
        endless.append(url)
        return {"results": [{"tempoWorklogId": len(endless)}],
                "metadata": {"next": "https://api.us.tempo.io/4/worklogs?offset=%d" % len(endless)}}

    with _Stubs() as stubs:
        stubs.set(tempo, "_send", forever)
        try:
            short = tempo.read_all("/4/worklogs/user/acct", None, _OFFLINE, "t")
            check("reaching MAX_PAGES raises", False, "returned %d rows" % len(short))
        except tempo.TempoError as error:
            check("reaching MAX_PAGES raises", True)
            check("with the global scope", error.scope == tempo.Scope.GLOBAL, error.scope)
            check("saying the snapshot would be incomplete", "incomplete" in error.message,
                  error.message)
        except Exception as error:
            check("reaching MAX_PAGES raises", False, type(error).__name__)
    check("after exactly MAX_PAGES requests", len(endless) == tempo.MAX_PAGES, str(len(endless)))


def test_setup_diagnose_unauthorized():
    """setup: a 401 diagnosis decides the cluster by probing the universal host"""
    # the live check's "wrong cluster returns 401" block, offline
    secret = "tokenvalueZZ9qq"
    probes = []

    def accepting(path, params=None, config=None, token=None):
        probes.append((path, dict(config), token))
        return {"results": []}

    def refusing(path, params=None, config=None, token=None):
        probes.append((path, dict(config), token))
        raise tempo.TempoError(tempo.Scope.GLOBAL, 401, "Unauthorized", path)

    regional = {"baseUrl": "https://api.us.tempo.io/4"}
    with _Stubs() as stubs:
        stubs.set(tempo, "read", accepting)
        text = "\n".join(setup_script._diagnose_unauthorized(regional, secret))
    check("a regional host the universal one overrules is named as the cause",
          "THIS IS THE CAUSE" in text, text)
    check("with the fix spelled out", "set baseUrl %s" % setup_script.UNIVERSAL_BASE_URL in text,
          text)
    check("the probe went to the universal host",
          len(probes) == 1 and probes[0][1]["baseUrl"] == setup_script.UNIVERSAL_BASE_URL,
          str(probes))
    check("with the caller's token", bool(probes) and probes[0][2] == secret)
    check("and the caller's config is left as it was",
          regional == {"baseUrl": "https://api.us.tempo.io/4"}, str(regional))
    check("the token is never echoed", secret not in text)

    del probes[:]
    with _Stubs() as stubs:
        stubs.set(tempo, "read", refusing)
        text = "\n".join(setup_script._diagnose_unauthorized(regional, secret))
    check("when the universal host refuses too, the cluster is unlikely",
          "unlikely to be the cause" in text, text)
    check("and is not named as the cause", "THIS IS THE CAUSE" not in text, text)
    check("the refusal's status is quoted", "(401)" in text, text)
    check("the token is not echoed there either", secret not in text)

    del probes[:]
    with _Stubs() as stubs:
        stubs.set(tempo, "read", accepting)
        text = "\n".join(setup_script._diagnose_unauthorized(
            {"baseUrl": "https://api.tempo.io/4/"}, secret))
    check("on the universal host nothing is probed", probes == [], str(probes))
    check("and the cluster is unlikely to be the cause", "unlikely to be the cause" in text, text)


def _apply_row(action, date, account_key, worklog_id=None, issue_id=10017):
    """One proposal row with every field the write paths and Results read."""
    return {"date": date, "templateId": catalogue.template_id(issue_id, {"_Account_": account_key}),
            "issueId": issue_id, "account": {"key": account_key, "status": "OPEN"},
            "attributes": {"_Account_": account_key}, "action": action,
            "hours": 1.0, "seconds": 3600, "worklogId": worklog_id,
            "beforeSeconds": 7200, "beforeBillableSeconds": 7200, "startTime": "08:00:00",
            "sendBillable": False, "afterBillableSeconds": 3600, "existingCount": 0,
            "blocked": False, "blockedReason": None}


def _release_result_dir(result_dir, original_result_dir, opened):
    """Undo a temporary RESULT_DIR without masking the error that ended the block."""
    # restored first, so a failure below cannot leave later groups writing into the temp dir.
    # an open .jsonl handle makes the removal fail on Windows, so every Results is closed before it,
    # and the removal ignores errors so it never replaces the exception already propagating.
    apply_script.RESULT_DIR = original_result_dir
    for results in opened:
        if results is not None:
            results.close()
    shutil.rmtree(result_dir, ignore_errors=True)


def test_apply_failure_status():
    """apply: what a refused row, a covered row and a stopped run are recorded as"""
    # closedMonths alone is pinned in test_apply_failure_reach; this is what the user
    # reads afterwards -- which rows were sent and refused, which were never tried
    original_result_dir = apply_script.RESULT_DIR
    result_dir = apply_script.RESULT_DIR = tempfile.mkdtemp()
    opened = []
    sent = []
    sent_dates = []

    def refusing(refusals):
        def write(method, path, body=None, config=None, token=None):
            sent.append((method, path))
            if isinstance(body, list):
                sent_dates.extend(item["startDate"] for item in body)
            for marker, error in refusals:
                if marker in path:
                    raise error
            return [] if method == "POST" else {}
        return write

    closed_month = tempo.TempoError(tempo.Scope.GROUP_MONTH, 403, "Period is closed", "/x")
    closed_account = tempo.TempoError(tempo.Scope.GROUP_ACCOUNT, 403,
                                      "Account is closed or archived", "/x")
    try:
        # updates: a closed month covers a later row in that month, and only that month
        results = apply_script.Results("unit-updates")
        opened.append(results)
        state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
        rows = [_apply_row("UPDATE", "2026-08-17", "200-17", 90001),
                _apply_row("UPDATE", "2026-08-31", "200-16", 90002),
                _apply_row("UPDATE", "2026-09-01", "200-17", 90003)]
        del sent[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "write", refusing([("/90001", closed_month)]))
            apply_script._write_updates(rows, "acct", results, state, _OFFLINE, "t")
        results.close()
        statuses = [e["status"] for e in results.rows]
        check("a refused update is recorded failed", statuses[0] == apply_script.FAILED,
              str(statuses))
        check("a later update in that closed month is skipped-propagated",
              statuses[1] == apply_script.SKIPPED_PROPAGATED, str(statuses))
        check("and the propagated reason names the month",
              "2026-08" in (results.rows[1]["reason"] or ""), results.rows[1]["reason"])
        check("an update in another month is still written", statuses[2] == apply_script.WRITTEN,
              str(statuses))
        check("the covered update was never sent",
              [p for _, p in sent] == ["/4/worklogs/90001", "/4/worklogs/90003"], str(sent))

        # deletes: a closed account covers a later row on that account, in any month
        results = apply_script.Results("unit-deletes")
        opened.append(results)
        state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
        rows = [_apply_row("DELETE", "2026-09-01", "200-17", 90001),
                _apply_row("DELETE", "2026-10-02", "200-17", 90002),
                _apply_row("DELETE", "2026-09-03", "200-16", 90003)]
        del sent[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "write", refusing([("/90001", closed_account)]))
            apply_script._write_deletes(rows, results, state, _OFFLINE, "t")
        results.close()
        statuses = [e["status"] for e in results.rows]
        check("a refused delete is recorded failed", statuses[0] == apply_script.FAILED,
              str(statuses))
        check("a later delete on that closed account is skipped-propagated",
              statuses[1] == apply_script.SKIPPED_PROPAGATED, str(statuses))
        check("a delete on another account is still written", statuses[2] == apply_script.WRITTEN,
              str(statuses))
        check("the covered delete was never sent",
              [p for _, p in sent] == ["/4/worklogs/90001", "/4/worklogs/90003"], str(sent))

        # a refused bulk batch: every row in it was sent, so every row is failed
        results = apply_script.Results("unit-bulk")
        opened.append(results)
        state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
        batch = [_apply_row("CREATE", "2026-09-01", "200-17"),
                 _apply_row("CREATE", "2026-09-02", "200-17")]
        del sent[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "write", refusing([("/bulk", closed_account)]))
            apply_script._send_bulk(10017, batch, "acct", results, state, _OFFLINE, "t",
                                    ["_Account_"])
        results.close()
        check("every row of a refused batch is recorded failed",
              [e["status"] for e in results.rows] == [apply_script.FAILED] * 2,
              str([e["status"] for e in results.rows]))
        check("and the account is remembered for later batches", "200-17" in state["closedAccounts"])
        check("the batch was sent once", sent == [("POST", "/4/worklogs/issue/10017/bulk")],
              str(sent))

        # creates: a later batch in the closed month is covered before it is sent.
        # batches go out in issue order, so issue 10016's is the one refused first
        results = apply_script.Results("unit-creates")
        opened.append(results)
        state = {"closedMonths": set(), "closedAccounts": set(), "stop": None}
        rows = [_apply_row("CREATE", "2026-08-17", "200-16", issue_id=10016),
                _apply_row("CREATE", "2026-08-31", "200-17", issue_id=10017),
                _apply_row("CREATE", "2026-09-01", "200-17", issue_id=10017)]
        del sent[:]
        del sent_dates[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "write", refusing([("/10016/", closed_month)]))
            apply_script._write_creates(rows, "acct", results, state, _OFFLINE, "t",
                                        ["_Account_"])
        results.close()
        by_date = dict((e["date"], e["status"]) for e in results.rows)
        check("the refused create is recorded failed",
              by_date.get("2026-08-17") == apply_script.FAILED, str(by_date))
        check("a create on another issue in that month is skipped-propagated",
              by_date.get("2026-08-31") == apply_script.SKIPPED_PROPAGATED, str(by_date))
        check("a create in the next month is still written",
              by_date.get("2026-09-01") == apply_script.WRITTEN, str(by_date))
        check("the covered batch was never sent", "2026-08-31" not in sent_dates
              and sent_dates == ["2026-08-17", "2026-09-01"], str(sent_dates))
    finally:
        _release_result_dir(result_dir, original_result_dir, opened)


def test_apply_global_stop():
    """apply: a global failure records the refused row failed and leaves the rows after it untried"""
    # the UNTRIED rows are recorded by run() when GlobalStop reaches it,
    # so this goes through run() with the snapshot and the writes both canned
    original_result_dir = apply_script.RESULT_DIR
    result_dir = apply_script.RESULT_DIR = tempfile.mkdtemp()
    results = None
    unauthorized = tempo.TempoError(tempo.Scope.GLOBAL, 401, "Unauthorized", "/x")
    unavailable = tempo.TempoError(tempo.Scope.GLOBAL, 503, "Service Unavailable", "/x")

    def target(worklog_id, date):
        return {"tempoWorklogId": worklog_id, "startDate": date, "timeSpentSeconds": 7200,
                "billableSeconds": 7200, "startTime": "08:00:00", "issue": {"id": 10017},
                "attributes": {"values": [{"key": "_Account_", "value": "200-17"}]}}

    def proposal(name, rows):
        return {"id": name, "authorAccountId": "acct", "requiredAttributes": ["_Account_"],
                "range": {"from": "2026-09-01", "to": "2026-09-30"},
                "defaultStartTime": "08:00:00", "rows": rows}

    try:
        sent = []

        def write(method, path, body=None, config=None, token=None):
            sent.append(path)
            if path.endswith("/90001") or "/10016/" in path:
                raise unavailable if method == "POST" else unauthorized
            return [] if method == "POST" else {}

        snapshot = [target(90001, "2026-09-01"), target(90002, "2026-09-02")]
        with _Stubs() as stubs:
            stubs.set(tempo, "read_all",
                      lambda path, params=None, config=None, token=None: list(snapshot))
            stubs.set(tempo, "write", write)
            results, stop = apply_script.run(proposal("unit-global-updates", [
                _apply_row("UPDATE", "2026-09-01", "200-17", 90001),
                _apply_row("UPDATE", "2026-09-02", "200-17", 90002)]), _OFFLINE, "t")
        by_id = dict((e["worklogId"], e["status"]) for e in results.rows)
        check("a global refusal stops the run", stop is not None and "Unauthorized" in stop,
              str(stop))
        check("the update after it is recorded untried",
              by_id.get(90002) == apply_script.UNTRIED, str(by_id))
        check("and was never sent", sent == ["/4/worklogs/90001"], str(sent))
        # the refused update WAS sent, so the sweep must not report it as never attempted
        check("the refused update is recorded failed, not untried",
              by_id.get(90001) == apply_script.FAILED, str(by_id))
        check("and its failure is recorded once",
              [e["worklogId"] for e in results.rows].count(90001) == 1,
              str([e["worklogId"] for e in results.rows]))

        # the same holds on the delete path, which has its own loop
        del sent[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "read_all",
                      lambda path, params=None, config=None, token=None: list(snapshot))
            stubs.set(tempo, "write", write)
            results, stop = apply_script.run(proposal("unit-global-deletes", [
                _apply_row("DELETE", "2026-09-01", "200-17", 90001),
                _apply_row("DELETE", "2026-09-02", "200-17", 90002)]), _OFFLINE, "t")
        by_id = dict((e["worklogId"], e) for e in results.rows)
        check("a global refusal on a delete stops the run",
              stop is not None and "Unauthorized" in stop, str(stop))
        check("the refused delete is recorded failed, not untried",
              by_id.get(90001, {}).get("status") == apply_script.FAILED, str(by_id.get(90001)))
        check("with the refusal as its reason",
              "401" in (by_id.get(90001, {}).get("reason") or ""), str(by_id.get(90001)))
        check("the delete after it is recorded untried",
              by_id.get(90002, {}).get("status") == apply_script.UNTRIED, str(by_id.get(90002)))
        check("and was never sent", sent == ["/4/worklogs/90001"], str(sent))
        check("each delete is recorded once", len(results.rows) == 2,
              str([(e["worklogId"], e["status"]) for e in results.rows]))

        # a create batch that fails globally WAS sent, so it is reconciled first,
        # and only the batches after it are untried
        del sent[:]
        with _Stubs() as stubs:
            stubs.set(tempo, "read_all", lambda path, params=None, config=None, token=None: [])
            stubs.set(tempo, "write", write)
            results, stop = apply_script.run(proposal("unit-global-creates", [
                _apply_row("CREATE", "2026-09-01", "200-16", issue_id=10016),
                _apply_row("CREATE", "2026-09-02", "200-17", issue_id=10017)]), _OFFLINE, "t")
        by_date = dict((e["date"], e) for e in results.rows)
        check("a global failure on a create stops the run", stop is not None and "503" in stop,
              str(stop))
        check("the sent batch is reconciled to failed, not untried",
              by_date.get("2026-09-01", {}).get("status") == apply_script.FAILED,
              str(by_date.get("2026-09-01")))
        check("because the re-read showed it did not land",
              "did not land" in (by_date.get("2026-09-01", {}).get("reason") or ""))
        check("the batch after it is recorded untried",
              by_date.get("2026-09-02", {}).get("status") == apply_script.UNTRIED,
              str(by_date.get("2026-09-02")))
        check("and was never sent", sent == ["/4/worklogs/issue/10016/bulk"], str(sent))
    finally:
        _release_result_dir(result_dir, original_result_dir, [results])


_TESTS = (test_endpoint_policy, test_interfaces_refuse, test_url_join, test_failure_classification,
          test_setup_config_values, test_setup_token_shape, test_catalogue_name_folding,
          test_catalogue_collisions, test_catalogue_template_id, test_catalogue_unusable_templates,
          test_tempo_nouns, test_plan_billable, test_plan_arithmetic, test_apply_start_times,
          test_apply_payloads, test_apply_preconditions, test_apply_failure_reach,
          test_skill_md_generated, test_second_review, test_third_review, test_grouped_render,
          test_show_grid, test_cell_is_one_work_item, test_verb_below_nothing, test_cold_start,
          test_ids_order_and_wrapper, test_evals_loadable, test_entry_points,
          test_read_post_passes, test_send_decoding, test_read_all_offline,
          test_setup_diagnose_unauthorized, test_apply_failure_status, test_apply_global_stop)


def _run_all():
    global _group
    for t in _TESTS:
        _group = t.__name__
        try:
            t()
        except Exception as exc:
            # a check that raises is itself a failure
            _results.append((_group, "(crashed)", False,
                             "raised %s: %s" % (type(exc).__name__, exc)))
    return _results


def main():
    """Print a per-check PASS/FAIL breakdown grouped by test, then a summary.
    Exits non-zero if any check fails."""
    _run_all()
    order, groups = [], {}
    for g, name, ok, detail in _results:
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append((name, ok, detail))
    for g in order:
        print(g)
        for name, ok, detail in groups[g]:
            line = "  [%s] %s" % ("PASS" if ok else "FAIL", name)
            if not ok and detail:
                line += " -- " + str(detail)
            # the detail can quote a non-ASCII value, and the console may be cp1252
            print(line.encode("ascii", "backslashreplace").decode("ascii"))
    npass = sum(1 for r in _results if r[2])
    nfail = len(_results) - npass
    print("")
    print("%d passed, %d failed" % (npass, nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
