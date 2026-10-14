"""Checks that need a live Tempo tenant, run by hand against your own.

tests/tempo_logger_tests.py covers the scripts with no token and no network.
What it cannot cover is what a real tenant answers:
that UTF-8 survives the transport, that pagination returns every page,
that a wrong cluster answers 401, and that a real catalogue build holds its invariants.
Those live here, under a name tools/run_tests.py never discovers,
because they need a token and a reachable tenant.

Read-only: every call is a GET through tempo.read(), and nothing is created, changed or removed.
Assertions are invariants of the tenant's answers rather than any one tenant's keys,
and a check whose premise this tenant does not offer is reported as skipped, never as passed.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from the skill directory, through the wrapper that forces UTF-8:
    scripts/tempo.sh ../tests/live_check [wrong-cluster-base-url]
The wrong-cluster probe takes its URL from that argument or from TEMPO_WRONG_BASE_URL,
and otherwise tries the regional hosts below, skipping when none of them refuses the token.
"""

import os
import sys

# import the modules under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)

import tempo  # noqa: E402
import setup as setup_script  # noqa: E402
import catalogue  # noqa: E402

# a UTF-8 EN DASH decoded as cp1252 arrives as these two characters followed by a third,
# so their presence is the signature of the decoding defect, and a real dash or a euro sign is not
MOJIBAKE = "\u00e2\u20ac"

# the regional hosts the publish gate allows, tried in turn when no wrong cluster is named
REGIONAL_BASE_URLS = ("https://api.us.tempo.io/4",)


# every check records (group, name, ok, detail), and a skipped one records ok as None,
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(label, condition, detail=""):
    ok = bool(condition)
    _results.append((_group, label, ok, "" if ok else detail))


def skip(label, reason):
    _results.append((_group, label, None, reason))


_BUILT = []


def _built():
    """One catalogue build shared by the blocks that read it, so the tenant is asked once."""
    if not _BUILT:
        _BUILT.append(catalogue.build(8))
    return _BUILT[0]


def _keys(accounts):
    return [a.get("key") for a in accounts]


def test_live_utf8():
    """live: UTF-8 survives the transport"""
    try:
        accounts = tempo.read_all("/4/accounts", {"limit": 50})
        names = [a.get("name") or "" for a in accounts]
        replacement = sum(n.count("\ufffd") for n in names)
        check("no replacement characters", replacement == 0, "%d found" % replacement)

        # a name that decoded as cp1252 would carry this pair instead of the dash
        mojibake = [n for n in names if MOJIBAKE in n]
        check("no cp1252 mojibake", not mojibake, str(mojibake[:2]))

        # a tenant with no dashed name gives the transport nothing to get wrong here,
        # which is a missing premise rather than a pass
        if any("\u2013" in n for n in names):
            check("an account name carries a real EN DASH", True)
        else:
            skip("an account name carries a real EN DASH",
                 "none of %d account names holds U+2013" % len(names))

        # the same accounts read at a page size that forces several pages must come back whole,
        # and at most 20 pages, well inside the client's page cap
        small = max(1, -(-len(accounts) // 20))
        if small >= len(accounts):
            skip("pagination returned every page",
                 "%d account(s) fit on one page" % len(accounts))
        else:
            paged = tempo.read_all("/4/accounts", {"limit": small})
            check("pagination returned every page",
                  sorted(_keys(paged)) == sorted(_keys(accounts)),
                  "%d accounts at limit %d, %d at limit 50" % (len(paged), small, len(accounts)))
    except (tempo.TempoError, tempo.ConfigError) as error:
        check("live account read", False, str(error))


def test_live_author():
    """live: author identity"""
    try:
        token = tempo.load_token()
        account_id, source = tempo.resolve_author_account_id()
        check("author account id resolved", bool(account_id), "empty")
        check("source is derived or config", source in ("derived", "config"), source)
        # the detail never quotes either value, since one of them is the credential
        check("account id is not echoed with a token",
              account_id != token and token not in account_id, "the token appears in the account id")
    except (tempo.TempoError, tempo.ConfigError) as error:
        check("author identity", False, str(error))


def _wrong_cluster(config):
    """A base URL that refuses this token with 401, with whether the caller named it.

    A named URL is a claim that it is the wrong cluster, so its answer is checked.
    A probed one is only a guess, and a host that accepts the token is this tenant's own cluster."""
    named = os.environ.get("TEMPO_WRONG_BASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if named:
        return [named], True
    configured = config["baseUrl"].rstrip("/")
    universal = setup_script.UNIVERSAL_BASE_URL.rstrip("/")
    return [u for u in REGIONAL_BASE_URLS if u.rstrip("/") not in (configured, universal)], False


def test_live_cluster():
    """live: the wrong cluster returns 401, which is what makes the probe decidable"""
    # this is the premise the 401 diagnosis rests on. The notes recorded it from the
    # spec rather than from a call, and if a wrong cluster answered anything but 401
    # the whole cluster branch would be unreachable.
    try:
        token = tempo.load_token()
        config = tempo.load_config()
        candidates, named = _wrong_cluster(config)
        wrong = None
        for base_url in candidates:
            probe = dict(config)
            probe["baseUrl"] = base_url
            try:
                tempo.read("/4/accounts", {"limit": 1}, probe, token)
                if named:
                    check("wrong cluster is refused", False, "%s returned 200" % base_url)
            except tempo.TempoError as error:
                check("wrong cluster returns 401", error.status == 401,
                      "%s answered %s" % (base_url, error.status))
                if error.status == 401:
                    wrong = probe
                    break

        if wrong is None:
            skip("diagnosis identifies the cluster as the cause",
                 "no wrong cluster refused the token; name one with TEMPO_WRONG_BASE_URL")
        else:
            lines = "\n".join(setup_script._diagnose_unauthorized(wrong, token))
            check("diagnosis identifies the cluster as the cause", "THIS IS THE CAUSE" in lines, lines)
            check("diagnosis names the fix", "set baseUrl" in lines)
            check("diagnosis never echoes the token", token not in lines)

        # the control: on the universal host the cluster must be ruled out instead,
        # or every genuine bad-token case would be misdiagnosed as a cluster problem.
        universal = dict(config)
        universal["baseUrl"] = setup_script.UNIVERSAL_BASE_URL
        control = "\n".join(setup_script._diagnose_unauthorized(universal, token))
        check("universal host does not blame the cluster",
              "THIS IS THE CAUSE" not in control and "unlikely to be the cause" in control)
    except (tempo.TempoError, tempo.ConfigError) as error:
        check("cluster probe", False, str(error))


def test_live_catalogue():
    """live: a real catalogue build holds its invariants"""
    try:
        built = _built()
        config, token = tempo.load_config(), tempo.load_token()

        # a collision group is only as good as the names behind it,
        # so every group is checked against the tenant's own accounts rather than against known keys
        names = {k: a["name"] for k, a in catalogue._accounts_by_key(config, token).items()}
        groups = built["accountNameCollisions"]
        if not groups:
            skip("every collision group shares one folded name", "this tenant has no colliding names")
        for group in groups:
            keys = group["keys"]
            check("collision group %r has at least two distinct keys" % group["foldedName"],
                  len(set(keys)) >= 2, str(keys))
            check("collision group %r shares one folded name" % group["foldedName"],
                  all(catalogue.fold_name(names.get(k) or "") == group["foldedName"] for k in keys),
                  str([names.get(k) for k in keys]))
            # deduplicating would leave one candidate for the shared name,
            # so every template on a member must point at all the others
            for template in built["templates"]:
                account = template.get("account") or {}
                if account.get("key") in keys:
                    check("template %s names the accounts it collides with" % template["id"],
                          set(account.get("collidesWith") or [])
                          == set(keys) - {account["key"]},
                          str(account.get("collidesWith")))

        attributes = tempo.read_all("/4/work-attributes", {"limit": 50}, config, token)
        expected = sorted((a["key"], a.get("name"), a.get("type"))
                          for a in attributes if a.get("required"))
        got = sorted((a["key"], a["name"], a["type"]) for a in built["requiredAttributes"])
        check("required attributes were read, not assumed", got == expected,
              "%s vs %s" % (got, expected))
        check("templates were built", len(built["templates"]) > 0)
        check("every template carries an issue id",
              all(t["issueId"] for t in built["templates"]))
        required = {a["key"] for a in built["requiredAttributes"]}
        check("every template carries the required attribute values",
              all(set(t["attributes"]) == required for t in built["templates"]))
        check("templates are ranked by count then recency",
              [t["count"] for t in built["templates"]]
              == sorted([t["count"] for t in built["templates"]], reverse=True))

        # the account name must arrive intact, which is the end-to-end proof
        # that UTF-8 survived every hop into the catalogue
        names = [(t.get("account") or {}).get("name") or "" for t in built["templates"]]
        check("no account name in the catalogue is damaged",
              not [n for n in names if "\ufffd" in n or MOJIBAKE in n], str(names))
        if any("\u2013" in n for n in names):
            check("an account name kept its EN DASH", True)
        else:
            skip("an account name kept its EN DASH", "no template's account name holds U+2013")

        # weekend evidence must be about the past only, or a future weekend row
        # this skill proposed becomes its own justification next time
        weekend = built["weekendHistory"]
        check("weekend evidence has count and lastUsed",
              "count" in weekend and "lastUsed" in weekend)
        check("no weekend date is in the future",
              all(d <= built["today"] for d in weekend["dates"]), str(weekend))
    except (tempo.TempoError, tempo.ConfigError) as error:
        check("live catalogue build", False, str(error))


def test_live_template_fields():
    """live: the declared template fields are what a real build emits"""
    # tests/tempo_logger_tests.py checks this against a canned tenant,
    # and this repeats it against the build a real tenant produces.
    try:
        built = _built()
        if built["templates"]:
            emitted = set(built["templates"][0])
            declared = {name for name, _meaning in catalogue.TEMPLATE_FIELDS}
            check("every declared template field is emitted", declared <= emitted,
                  "missing: %s" % sorted(declared - emitted))
            check("every emitted template field is declared", emitted <= declared,
                  "undeclared: %s" % sorted(emitted - declared))
        else:
            check("live template field check", False, "no templates to compare against")
    except (tempo.TempoError, tempo.ConfigError) as error:
        check("live template field check", False, str(error))


_TESTS = (test_live_utf8, test_live_author, test_live_cluster, test_live_catalogue,
          test_live_template_fields)


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
    """Print a per-check PASS/FAIL/SKIP breakdown grouped by test, then a summary.
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
            mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
            line = "  [%s] %s" % (mark, name)
            if not ok and detail:
                line += " -- " + str(detail)
            # the detail can quote a non-ASCII value, and the console may be cp1252
            print(line.encode("ascii", "backslashreplace").decode("ascii"))
    npass = sum(1 for r in _results if r[2] is True)
    nfail = sum(1 for r in _results if r[2] is False)
    nskip = len(_results) - npass - nfail
    print("")
    print("%d passed, %d failed, %d skipped" % (npass, nfail, nskip))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
