"""Setup check and config writer for the tempo-logger skill.

Never calls a data-changing endpoint, but it does write the config file —
the "read-only" column in the design's file table is about the former.

Two things shape this file.

It cannot prompt. A script run as a tool call has no usable stdin, so `check`
reports an outcome and stops, and every fix is either something the user does
outside (create a token) or a separate `set` call. Nothing here waits for input.

A 401 has three causes that look identical in the response: a bad token, the
wrong cluster, and whitespace inside the token. So `check` does not report
"unauthorized" — it separates the three, and the cluster one it decides by
probing rather than by guessing.
"""

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tempo  # noqa: E402

# api.tempo.io routes to whichever cluster holds the tenant's data,
# so it is the control in the cluster probe: a specific cluster can be wrong,
# the universal host cannot be.
UNIVERSAL_BASE_URL = "https://api.tempo.io/4"

# the model branches on the outcome,
# so each one gets its own code rather than a single "failed"
# that would force it to parse the prose.
EXIT_READY = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_TOKEN = 3
EXIT_COLD_START = 4

# the contract this script publishes. Each meaning sits beside the constant it
# describes so the two cannot drift apart, and tests/contract.py renders SKILL.md's
# tables from here rather than anyone transcribing them by hand.
STATUS_MEANINGS = (
    ("ready", EXIT_READY, "setup is complete; continue"),
    ("no-token", EXIT_NO_TOKEN, "no token file was found"),
    ("cold-start", EXIT_COLD_START, "no worklogs are visible, so there is no history to work from"),
    ("unauthorized", EXIT_ERROR, "Tempo refused the credentials; the output separates the three causes"),
    ("error", EXIT_ERROR, "anything else; the output says what"),
)

CONFIG_KEYS = ("authorAccountId", "baseUrl", "defaultStartTime")

CONFIG_KEY_MEANINGS = (
    ("authorAccountId", "whose worklogs are read and written"),
    ("baseUrl", "which Tempo host is called"),
    ("defaultStartTime", "the earliest a created row may start"),
)

QUOTE_CHARS = ("'", '"')


def _legacy_notes():
    """Say when a pre-XDG path is the one in use, so the move is visible not silent."""
    notes = []
    if tempo.token_path_in_use() == tempo.LEGACY_TOKEN_PATH:
        notes.append("  note:    token is still at the old path %s" % tempo.LEGACY_TOKEN_PATH)
        notes.append("           move it to %s when convenient" % tempo.TOKEN_PATH)
    if tempo.config_path_in_use() == tempo.LEGACY_CONFIG_PATH:
        notes.append("  note:    config is still at the old path %s" % tempo.LEGACY_CONFIG_PATH)
        notes.append("           move it to %s when convenient" % tempo.CONFIG_PATH)
    return notes


# ---------------------------------------------------------------- token file shape

def _token_shape_notes():
    """Report anomalies in the token file that survive stripping.

    load_token() strips the outer whitespace, so a trailing newline can never
    reach the wire from here — which means the whitespace cause that does survive
    is whitespace in the middle, from a paste that wrapped across lines.

    Reads bytes and decodes permissively, because a file saved in another encoding
    must produce a diagnosis rather than a traceback out of the diagnostic itself.
    """
    notes = []
    # the path actually in use, which may still be the pre-XDG one
    path = tempo.token_path_in_use()
    if path is None:
        return ["the token file does not exist at %s" % tempo.TOKEN_PATH]
    with io.open(path, "rb") as handle:
        raw_bytes = handle.read()
    raw = raw_bytes.decode("utf-8", errors="replace")

    if "�" in raw:
        notes.append("the file is not valid UTF-8 — re-save it as UTF-8 or plain ASCII")
    if raw.startswith("﻿"):
        notes.append("the file starts with a byte-order mark, which becomes part of the token")

    token = raw.strip().lstrip("﻿")
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) > 1:
        notes.append(
            "the file has %d non-empty lines — a token is one line, "
            "so the paste probably wrapped" % len(lines)
        )
    if re.search(r"\s", token):
        notes.append("the token contains whitespace inside it, which a paste that wrapped explains")
    if token[:1] in QUOTE_CHARS or token[-1:] in QUOTE_CHARS:
        notes.append("the token is wrapped in quotes — save it without them")
    if token.lower().startswith("bearer "):
        notes.append(
            "the token starts with 'Bearer ' — save only the token, the header is added for you"
        )
    if any(ord(ch) > 127 for ch in token):
        notes.append(
            "the token contains non-ASCII characters, which usually means an editor "
            "substituted a typographic character on paste"
        )

    # say so when nothing is wrong,
    # because a cause listed with no evidence under it
    # reads as an unexamined cause rather than an eliminated one.
    if not notes:
        notes.append("no anomalies — a single line, no whitespace inside, plain ASCII")

    # the length is safe to print and is the only way to spot a truncated paste
    # without echoing any part of the secret.
    notes.append("token length after stripping: %d characters" % len(token))
    return notes


# ---------------------------------------------------------------- 401 diagnosis

def _diagnose_unauthorized(config, token):
    """Separate the three causes of a 401, deciding the cluster one by probing."""
    lines = ["Tempo refused the credentials. There are three causes and they look identical:", ""]

    lines.append("1. The token file's contents")
    for note in _token_shape_notes():
        lines.append("   - " + note)
    lines.append("")

    lines.append("2. The cluster")
    configured = config["baseUrl"].rstrip("/")
    if configured == UNIVERSAL_BASE_URL.rstrip("/"):
        lines.append(
            "   baseUrl is the universal host, which routes to whichever cluster holds "
            "your data, so the cluster is unlikely to be the cause."
        )
    else:
        probe = dict(config)
        probe["baseUrl"] = UNIVERSAL_BASE_URL
        try:
            tempo.read("/4/accounts", {"limit": 1}, probe, token)
            lines.append(
                "   THIS IS THE CAUSE. baseUrl is %s, which refused the token, "
                "but the universal host %s accepted it." % (configured, UNIVERSAL_BASE_URL)
            )
            lines.append("   Fix it with:  setup set baseUrl %s" % UNIVERSAL_BASE_URL)
        except tempo.TempoError as error:
            lines.append(
                "   baseUrl is %s and the universal host %s refused the token too (%s), "
                "so the cluster is unlikely to be the cause."
                % (configured, UNIVERSAL_BASE_URL, error.status)
            )
    lines.append("")

    lines.append("3. The token itself")
    lines.append("   It may be revoked, expired, or from a different Tempo tenant.")
    lines.append(
        "   Create a fresh one under Tempo > Settings > Data Access > API integration "
        "and overwrite %s." % tempo.TOKEN_PATH
    )
    return lines


# ---------------------------------------------------------------- check

def _check():
    out = []
    config = tempo.load_config()

    out.append("Checking tempo-logger setup.")
    out.append("  baseUrl: %s" % config["baseUrl"])
    config_path = tempo.config_path_in_use()
    out.append("  config:  %s" % (config_path or tempo.CONFIG_PATH + " (absent, using defaults)"))
    out.append("  token:   %s" % (tempo.token_path_in_use() or tempo.TOKEN_PATH + " (absent)"))
    out.extend(_legacy_notes())
    out.append("")

    # 1 — the token file
    try:
        token = tempo.load_token()
    except tempo.ConfigError as error:
        out.append("status: no-token")
        out.append("")
        out.append(str(error))
        out.append("")
        out.append("Save the token as the only line of %s, then run this check again."
                   % tempo.TOKEN_PATH)
        _emit(out)
        return EXIT_NO_TOKEN
    out.append("  ok    token file readable")

    # 2 — auth, probed on the endpoint the catalogue needs rather than on a general-purpose one,
    # because a token can be scoped to worklogs alone
    # and would then pass a generic probe and fail in stage 2.
    try:
        tempo.read("/4/accounts", {"limit": 1}, config, token)
        out.append("  ok    project accounts readable")
    except tempo.TempoError as error:
        if error.status == 401:
            out.append("")
            out.append("status: unauthorized")
            out.append("")
            out.extend(_diagnose_unauthorized(config, token))
            _emit(out)
            return EXIT_ERROR
        out.append("  FAIL  project accounts not readable — %s %s" % (error.status, error.message))
        out.append("")
        out.append("status: error")
        if error.status == 403:
            out.append("")
            out.append(
                "The token authenticated but is not allowed to read project accounts. "
                "The catalogue cannot be built without them, so this needs a token "
                "with wider scope."
            )
        elif error.status == 0:
            # a mistyped cluster hostname fails DNS rather than returning 401,
            # so it never reaches the 401 diagnosis
            # and would otherwise read as a network outage.
            out.append("")
            out.append(
                "The request never reached Tempo. Either the network is down, or "
                "baseUrl names a host that does not exist — it is currently %s, and "
                "the default is %s." % (config["baseUrl"], UNIVERSAL_BASE_URL)
            )
        _emit(out)
        return EXIT_ERROR

    # 3 — author identity, which also proves the worklog read the whole skill rests on.
    # when authorAccountId is configured, resolve_author_account_id returns it without
    # reading anything, so cold start could never be reported —
    # a wrong id or a narrowed token then looked `ready` and produced an empty catalogue.
    try:
        account_id, source = tempo.resolve_author_account_id(config, token)
        if source == "config":
            visible = tempo.read("/4/worklogs/user/" + account_id,
                                 {"limit": 1}, config, token).get("results", [])
            if not visible:
                out.append("  FAIL  no worklogs are visible for the configured author")
                out.append("")
                out.append("status: cold-start")
                out.append("")
                out.append(
                    "authorAccountId is set to %s in %s, and that account has no "
                    "worklogs this token can see. Either the id is wrong, or the "
                    "account genuinely has no history — log one worklog in the Tempo "
                    "UI, or correct the id." % (account_id, tempo.CONFIG_PATH))
                _emit(out)
                return EXIT_COLD_START
    except tempo.NoWorklogs as error:
        out.append("  FAIL  author account id could not be resolved")
        out.append("")
        out.append("status: cold-start")
        out.append("")
        out.append(str(error))
        _emit(out)
        return EXIT_COLD_START
    except tempo.ConfigError as error:
        # the only other way the derivation fails is a token seeing several authors,
        # which no token has been seen doing. It refuses rather than guessing, and says
        # so here without a status of its own.
        out.append("  FAIL  author account id could not be resolved")
        out.append("")
        out.append("status: error")
        out.append("")
        out.append(str(error))
        out.append("")
        out.append("Once you know which account id is yours:")
        out.append("  setup set authorAccountId <id>")
        _emit(out)
        return EXIT_ERROR
    except tempo.TempoError as error:
        out.append("  FAIL  worklogs not readable — %s %s" % (error.status, error.message))
        out.append("")
        out.append("status: error")
        _emit(out)
        return EXIT_ERROR

    out.append("  ok    author account id %s (%s)" % (account_id, source))
    out.append("")
    out.append("status: ready")
    _emit(out)
    return EXIT_READY


# ---------------------------------------------------------------- set

def _validate(key, value):
    """Return the parsed value, or raise ConfigError naming what is wrong.

    Validation belongs here rather than at the point of use, because a bad value
    written now surfaces several scripts later, where its origin is invisible.
    """
    if key == "baseUrl":
        if not value.startswith("https://"):
            raise tempo.ConfigError(
                "baseUrl must start with https:// — over http the token would cross "
                "the network in clear text"
            )
        return value.rstrip("/")
    if key == "defaultStartTime":
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$", value):
            raise tempo.ConfigError("defaultStartTime must be HH:MM:SS, got %r" % value)
        return value
    if not value.strip():
        raise tempo.ConfigError("%s cannot be empty" % key)
    return value


def _set(argv):
    if len(argv) != 4:
        sys.stderr.write("usage: setup.py set <key> <value>\nkeys: %s\n" % ", ".join(CONFIG_KEYS))
        return EXIT_USAGE
    key, value = argv[2], argv[3]
    if key not in CONFIG_KEYS:
        sys.stderr.write(
            "unknown config key %r — a key nothing reads would be written silently.\nkeys: %s\n"
            % (key, ", ".join(CONFIG_KEYS))
        )
        return EXIT_USAGE

    try:
        parsed = _validate(key, value)
    except tempo.ConfigError as error:
        sys.stderr.write(str(error) + "\n")
        return EXIT_USAGE

    # read the file that is actually in use — which may still be the pre-XDG one.
    # reading only the XDG path meant a user whose config was still at the old location
    # got a brand-new file holding one key, silently losing authorAccountId —
    # the author was then re-derived from worklogs, which for a wide token can be a colleague.
    existing = {}
    source = tempo.config_path_in_use()
    if source:
        try:
            with io.open(source, encoding="utf-8") as handle:
                existing = json.load(handle)
        except ValueError as error:
            sys.stderr.write(
                "%s is not valid JSON (%s) — fix or delete it before setting a key, "
                "because overwriting would discard whatever else it holds\n"
                % (source, error)
            )
            return EXIT_ERROR
    existing[key] = parsed
    migrated = source == tempo.LEGACY_CONFIG_PATH

    directory = os.path.dirname(tempo.CONFIG_PATH)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)

    # write through a temporary file in the same directory,
    # because a crash midway through a plain overwrite leaves truncated JSON
    # that makes load_config() raise on every later run, including this script's own check.
    temporary = tempo.CONFIG_PATH + ".tmp"
    with io.open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(existing, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, tempo.CONFIG_PATH)

    sys.stdout.write("set %s = %s in %s\n" % (key, json.dumps(parsed), tempo.CONFIG_PATH))
    if migrated:
        sys.stdout.write(
            "carried %d existing key(s) over from %s, which is no longer read — "
            "delete it when you are satisfied\n" % (len(existing) - 1, tempo.LEGACY_CONFIG_PATH))
    return EXIT_READY


# ---------------------------------------------------------------- cli entry

def _emit(lines):
    sys.stdout.write("\n".join(lines) + "\n")


def _main(argv):
    command = argv[1] if len(argv) > 1 else None
    if command == "check":
        try:
            return _check()
        except tempo.TempoError as error:
            _emit(["status: error", "", str(error)])
            return EXIT_ERROR
    if command == "set":
        return _set(argv)
    sys.stderr.write(
        "usage: setup.py check\n"
        "       setup.py set <key> <value>\n"
        "keys: %s\n" % ", ".join(CONFIG_KEYS)
    )
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
