"""Shared Tempo REST client for the tempo-logger skill.

Two deliberate properties, both from the design:

Read and write are separate interfaces and each rejects the other's endpoints.
That is what makes "only apply.py touches data-changing endpoints" a mechanical
property rather than a convention, so the check is symmetric on purpose.

Failures are classified by how many proposal rows they affect, not by status code.
Two completely different failures share 403, and their handling is opposite,
so the classification key is errors[].message.
"""

import datetime
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _xdg(variable, fallback, windows_variable=None):
    """An XDG base directory, honouring the platform's own convention on Windows.

    Windows has no XDG, so APPDATA and LOCALAPPDATA stand in for the config and
    state roots. An explicitly exported XDG variable wins on every platform,
    because someone who set it meant it.
    """
    explicit = os.environ.get(variable)
    if explicit:
        return explicit
    if os.name == "nt" and windows_variable:
        native = os.environ.get(windows_variable)
        if native:
            return native
    return os.path.expanduser(fallback)


CONFIG_DIR = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config", "APPDATA"), "tempo")
STATE_DIR = os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state", "LOCALAPPDATA"), "tempo")

# .txt, because an extensionless file reads as something with a format on Windows,
# where every other file has one -- it is one line of plain text and should say so
TOKEN_PATH = os.path.join(CONFIG_DIR, "token.txt")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# the pre-XDG locations are still read, because moving a credential out from under
# a working setup without warning is worse than carrying two paths for a while.
# setup check names the legacy path when it is the one in use.
LEGACY_TOKEN_PATH = os.path.expanduser("~/.tempo_token")
LEGACY_CONFIG_PATH = os.path.expanduser("~/.tempo/config.json")

DEFAULT_BASE_URL = "https://api.tempo.io/4"

# a page cap exists so a mis-resolved range cannot spin forever,
# but a truncated read is a global failure rather than a partial result —
# an incomplete snapshot makes every CREATE precondition pass and duplicates the day.
MAX_PAGES = 40

READ_RETRY_ATTEMPTS = 4
READ_RETRY_BASE_SECONDS = 1.0

# named rather than inline so a check can pin them against what SKILL.md documents —
# a default that only exists inside load_config() can drift from its documentation
# without anything failing.
DEFAULT_START_TIME = "08:00:00"


# ---------------------------------------------------------------- failure scopes

class Scope:
    """How many proposal rows a failure affects."""

    ROW = "row"
    GROUP_MONTH = "group-month"
    GROUP_ACCOUNT = "group-account"
    GLOBAL = "global"
    RETRYABLE = "retryable"
    BULK_IN_PROGRESS = "bulk-in-progress"


class TempoError(Exception):
    """A classified Tempo failure.

    `scope` is what apply.py branches on; `message` is Tempo's own wording,
    which must reach the user because our own paraphrase would hide
    the approval-status case that lands in the global bucket unverified.
    """

    def __init__(self, scope, status, message, path):
        super().__init__("%s %s: %s" % (status, path, message))
        self.scope = scope
        self.status = status
        self.message = message
        self.path = path


class ConfigError(Exception):
    """Setup is incomplete or inconsistent. Never carries the token."""


class NoWorklogs(ConfigError):
    """Nothing is logged at all, so there is no author to derive and nothing to read.

    A ConfigError as far as every caller is concerned; the separate type exists only so
    setup.py can tell this apart from a failed derivation without reading the wording.
    """


# ---------------------------------------------------------------- config & token

def load_config():
    """Read the config file, tolerating absence.

    Every value is optional; the defaults are the design's defaults.
    """
    config = {}
    path = config_path_in_use()
    if path:
        with io.open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    config.setdefault("baseUrl", DEFAULT_BASE_URL)
    config.setdefault("defaultStartTime", DEFAULT_START_TIME)
    return config


def config_path_in_use():
    """The config file actually being read, or None when there is none."""
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        if os.path.exists(path):
            return path
    return None


def token_path_in_use():
    """The token file actually being read, or None when there is none."""
    for path in (TOKEN_PATH, LEGACY_TOKEN_PATH):
        if os.path.exists(path):
            return path
    return None


def load_token():
    """Read the token from file at call time.

    Never from an environment variable: a variable exported in one terminal is
    invisible to shells spawned later by another process, and the resulting call
    sends an empty Authorization header whose 401 is indistinguishable from a bad token.

    Stripping happens here rather than in a shell so the token never passes
    through a command line where it would land in shell history.
    """
    path = token_path_in_use()
    if path is None:
        raise ConfigError(
            "token file not found at %s — create one under "
            "Tempo > Settings > Data Access > API integration" % TOKEN_PATH
        )
    with io.open(path, encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        raise ConfigError("token file %s is empty" % path)
    return token


# ---------------------------------------------------------------- endpoint policy

def _normalise(path):
    """Reduce a path to its shape so id segments do not defeat the whitelist.

    The version segment is dropped first, because it is a bare digit and would
    otherwise be normalised into {id} — which made every write endpoint miss
    the whitelist and left the read/write split entirely inoperative.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[0] in ("4", "v4"):
        parts = parts[1:]
    return "/" + "/".join("{id}" if _looks_like_id(p) else p for p in parts)


def _looks_like_id(segment):
    if segment.isdigit():
        return True
    # a Tempo author account id looks like 712020:uuid, and account keys vary too much
    # to pattern-match, so only the two shapes that actually appear in a path are handled.
    return ":" in segment


# the whitelist is explicit rather than derived from the method,
# because /4/worklogs/search is a POST that only reads —
# a rule like "POST means write" would ban a legitimate read,
# and "POST ending in /search means read" breaks the day Tempo adds a read POST elsewhere.
#
# stored without the version segment, matching what _normalise returns.
MUTATING = frozenset([
    ("POST", "/worklogs"),
    ("POST", "/worklogs/issue/{id}/bulk"),
    ("PUT", "/worklogs/{id}"),
    ("DELETE", "/worklogs/{id}"),
    ("POST", "/worklogs/{id}/work-attribute-values"),
    ("PUT", "/worklogs/{id}/work-attribute-values"),
    ("DELETE", "/worklogs/{id}/work-attribute-values/{id}"),
])


def is_mutating(method, path):
    return (method.upper(), _normalise(path)) in MUTATING


# ---------------------------------------------------------------- transport

def _build_url(base_url, path, params=None):
    """Join the configured base with a path, tolerating the version prefix on both sides.

    The verification notes cite every endpoint as /4/..., and the configured
    baseUrl already carries /4, so a naive join produces /4/4/... — a 404 whose
    cause is invisible in the message.
    """
    if path.startswith("http"):
        url = path
    else:
        base = base_url.rstrip("/")
        tail = path.lstrip("/")
        if base.endswith("/4") and tail.startswith("4/"):
            tail = tail[2:]
        url = base + "/" + tail
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
    return url


def _classify(status, body_text, path):
    """Map a failure onto a scope, keyed on the message rather than the status.

    403 covers two unrelated failures whose handling is opposite: a closed period
    propagates across the whole month, while a closed project account means the
    local catalogue is stale. Matching is case-insensitive and by substring
    because both casings have been observed and Tempo may add context to the wording;
    an exact match that misses would escalate one closed month into a global stop.
    """
    message = _extract_message(body_text) or body_text.strip() or "(no message)"
    lowered = message.lower()

    if status == 403:
        if "period is closed" in lowered:
            return Scope.GROUP_MONTH, message
        if "account is closed" in lowered:
            return Scope.GROUP_ACCOUNT, message
        return Scope.GLOBAL, message
    if status == 401:
        return Scope.GLOBAL, message
    if status == 409:
        return Scope.BULK_IN_PROGRESS, message
    if status == 429:
        return Scope.RETRYABLE, message
    if status in (400, 404):
        return Scope.ROW, message
    if 500 <= status < 600:
        return Scope.GLOBAL, message
    return Scope.GLOBAL, message


def _extract_message(body_text):
    try:
        body = json.loads(body_text)
    except (ValueError, TypeError):
        return None
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return first.get("message")
    return None


def _send(method, url, token, body=None):
    """One HTTP round trip. Raises TempoError with a scope, never leaking the token."""
    data = None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        scope, message = _classify(error.code, raw, url)
        raise TempoError(scope, error.code, message, _scrub(url)) from None
    except urllib.error.URLError as error:
        # a transport failure is global: the next call will fail the same way
        raise TempoError(Scope.GLOBAL, 0, "transport error: %s" % error.reason, _scrub(url)) from None


def _scrub(text):
    """Paths are safe to echo, but a token must never reach a message or a log.

    Reading the token here must not be able to mask the original failure,
    so any problem reading it leaves the text unchanged rather than raising.
    """
    text = str(text)
    try:
        secret = load_token()
    except Exception:
        return text
    return text.replace(secret, "<redacted>")


# ---------------------------------------------------------------- read interface

def read(path, params=None, config=None, token=None):
    """GET one resource.

    Retries 429 with backoff here because a read is idempotent.
    Writes deliberately do not retry in this layer — see write().
    """
    config = config or load_config()
    token = token or load_token()
    if is_mutating("GET", path):
        raise ConfigError("read() refused a data-changing endpoint: %s" % path)

    url = _build_url(config["baseUrl"], path, params)
    return _with_read_backoff(lambda: _send("GET", url, token))


def read_post(path, body, config=None, token=None):
    """POST to a read-only endpoint such as /4/worklogs/search."""
    config = config or load_config()
    token = token or load_token()
    if is_mutating("POST", path):
        raise ConfigError("read_post() refused a data-changing endpoint: %s" % path)

    url = _build_url(config["baseUrl"], path)
    return _with_read_backoff(lambda: _send("POST", url, token, body))


def read_all(path, params=None, config=None, token=None):
    """Follow metadata.next and return every result.

    metadata.next is an absolute URL that always points at one fixed regional host
    (observed: EU) whatever host the first call went to, so following it verbatim silently moves later
    pages onto a different host and makes the configured baseUrl stop applying
    after page one. Only its query string is reused.

    A page-cap hit raises rather than returning a short list: an incomplete
    snapshot makes every CREATE precondition pass, which duplicates worklogs
    on days that already have them.
    """
    config = config or load_config()
    token = token or load_token()
    results = []
    page = read(path, params, config, token)
    results.extend(page.get("results", []))

    pages = 1
    next_url = (page.get("metadata") or {}).get("next")
    while next_url:
        if pages >= MAX_PAGES:
            raise TempoError(
                Scope.GLOBAL, 0,
                "pagination exceeded %d pages for %s — snapshot would be incomplete"
                % (MAX_PAGES, path),
                path,
            )
        query = urllib.parse.urlparse(next_url).query
        rebuilt = _build_url(config["baseUrl"], path) + ("?" + query if query else "")
        page = _with_read_backoff(lambda url=rebuilt: _send("GET", url, token))
        results.extend(page.get("results", []))
        next_url = (page.get("metadata") or {}).get("next")
        pages += 1

    return results


def _with_read_backoff(call):
    delay = READ_RETRY_BASE_SECONDS
    for attempt in range(READ_RETRY_ATTEMPTS):
        try:
            return call()
        except TempoError as error:
            last = attempt == READ_RETRY_ATTEMPTS - 1
            if error.scope != Scope.RETRYABLE or last:
                raise
            time.sleep(delay + random.uniform(0, delay / 2))
            delay *= 2
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- write interface

def write(method, path, body=None, config=None, token=None):
    """Call a data-changing endpoint. Only apply.py should import this.

    429 is raised rather than retried. A safe retry needs to know whether the
    first attempt landed, and answering that needs worklog semantics this layer
    does not have — for a create it means comparing the row count for that
    (date, template), while an update is value-idempotent and can simply be resent.
    """
    config = config or load_config()
    token = token or load_token()
    if not is_mutating(method, path):
        raise ConfigError(
            "write() refused a non-data-changing endpoint: %s %s — use read()" % (method, path)
        )

    url = _build_url(config["baseUrl"], path)
    return _send(method, url, token, body)


# ---------------------------------------------------------------- shared rendering

# show.py and plan.py must render a row identically, and the format carries about a
# dozen safety rules. Duplicating them in two files would let the two drift apart,
# and the drift would be in the main safety mechanism, so they live here
# rather than in either caller.

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_date(text):
    """Parse YYYY-MM-DD, refusing anything else rather than guessing a format."""
    try:
        year, month, day = [int(part) for part in text.split("-")]
        return datetime.date(year, month, day)
    except (ValueError, AttributeError):
        raise ConfigError("dates must be YYYY-MM-DD, got %r" % text)


def weekday_name(date):
    return WEEKDAY_NAMES[date.weekday()]


def is_weekend(date):
    return date.weekday() >= 5


def format_hours(seconds):
    """Two decimals always, so a column of hours stays aligned and 8 never reads as 8 minutes."""
    return "%.2fh" % ((seconds or 0) / 3600.0)


def format_account(account):
    """`key · name`, with the key ALWAYS present.

    Names can collide within one tenant, so a name on its own does not identify an
    account. An unresolved key is rendered as such instead of being left blank,
    because a blank reads as "no account" rather than "could not look it up".
    """
    if not account:
        return "(no project account)"
    key = account.get("key") or "(no key)"
    name = account.get("name")
    if not name:
        return "%s · (name not found — renamed or removed?)" % key
    status = account.get("status")
    suffix = "" if status in (None, "OPEN") else "  [%s]" % status
    return "%s · %s%s" % (key, name, suffix)


def format_issue(issue_id, description):
    """`issue <id>` — deliberately unlike `worklog <id>`.

    A bare number is ambiguous between the two, and the place a user most often
    sees bare numbers is this output, so each id always arrives with its noun.
    """
    text = "issue %s" % issue_id
    if description:
        text += ' · "%s"' % description
    return text


def format_worklog_ref(worklog_id):
    return "worklog %s" % worklog_id


# ---------------------------------------------------------------- author identity

def resolve_author_account_id(config=None, token=None):
    """Return the author account id, and how it was obtained.

    There is no "who am I" endpoint, but an unfiltered worklog query returns only
    what the token can see, which for a personal token is one author.

    The query must not use limit=1: a single row always has exactly one distinct
    author, so the guard would pass for an administrator token whose first row
    belongs to a colleague, and every subsequent write would be attributed to them
    with nothing on the confirmation table to show it.
    """
    config = config or load_config()
    token = token or load_token()

    configured = config.get("authorAccountId")
    if configured:
        return configured, "config"

    rows = read("/4/worklogs", {"limit": 50}, config, token).get("results", [])
    authors = {row["author"]["accountId"] for row in rows if row.get("author")}

    if len(authors) == 1:
        return authors.pop(), "derived"
    if not authors:
        # flagged rather than left to be recognised by its wording: setup.py used to read
        # `"no worklogs visible" in message`, and rewording this text for clarity turned
        # every cold start into a misreported ambiguous author with no check noticing
        raise NoWorklogs(
            "nothing is logged on this timesheet yet. Tempo has no endpoint that says "
            "who a token belongs to, so with no worklog to read the author cannot be "
            "worked out and even a read cannot run. Logging one worklog in the Tempo UI "
            "fixes that, and supplies the work item and required attribute values a "
            "write needs at the same time"
        )
    # never observed: this token sees one author, and a broader one was only ever a
    # supposition. The guard stays because deriving the wrong id writes under someone
    # else's name, silently, with no output anywhere showing the author.
    raise ConfigError(
        "this token can see %d distinct authors, so the account cannot be derived. "
        "Tempo's worklogs carry an account id and no name, so the ids cannot be told "
        "apart here. Set authorAccountId in %s once you know which is yours"
        % (len(authors), CONFIG_PATH)
    )


# ---------------------------------------------------------------- cli entry

def _main(argv):
    """A thin probe so stage 1 is verifiable without the other scripts."""
    if len(argv) < 2 or argv[1] not in ("check", "whoami"):
        sys.stderr.write("usage: tempo.py {check|whoami}\n")
        return 2
    try:
        config = load_config()
        load_token()
        if argv[1] == "whoami":
            account_id, source = resolve_author_account_id(config)
            sys.stdout.write(json.dumps({"authorAccountId": account_id, "source": source}) + "\n")
        else:
            tenant = read("/4/globalconfiguration", None, config)
            sys.stdout.write(json.dumps({"baseUrl": config["baseUrl"], "tenant": tenant}) + "\n")
        return 0
    except (ConfigError, TempoError) as error:
        sys.stderr.write(str(error) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
