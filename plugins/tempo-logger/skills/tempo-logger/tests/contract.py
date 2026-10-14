"""Render SKILL.md's factual tables from the scripts' own constants.

Every table this file produces used to be transcribed into SKILL.md by hand, and
three reviews running, that transcription is where the false claims came from — a
renamed status, an exit code that moved, a field the code stopped emitting. The
constants and their one-clause meanings now live side by side in the scripts, and
these tables are generated from them, so the two cannot disagree.

What is deliberately NOT generated: the guidance. A table can say `skipped-propagated`
means "not sent, and NOT recorded anywhere"; it cannot say "never soften this, because
skipped reads as handled". That judgement is the model's brief and stays as prose,
outside the markers, where it is free to be edited without touching a fact.

Two uses, one source:
    python -X utf8 contract.py --check    exit 1 if SKILL.md is out of date
    python -X utf8 contract.py --write    rewrite the marked blocks in place
"""

import io
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

# import from source, never from a cached .pyc. A one-character change to a constant
# leaves the file the same size, and if it lands in the same mtime tick as the last
# import, Python reuses the stale bytecode — so this check would read the OLD constants
# and pass. That was observed, not theorised: changing EXIT_SUPERSEDED from 4 to 7 and
# back again fooled it until the cache was cleared.
sys.dont_write_bytecode = True
shutil.rmtree(os.path.join(SCRIPTS, "__pycache__"), ignore_errors=True)

import apply as apply_script  # noqa: E402
import catalogue  # noqa: E402
import plan  # noqa: E402
import setup as setup_script  # noqa: E402
import show  # noqa: E402

SKILL = os.path.join(HERE, "..", "SKILL.md")

BEGIN = "<!-- generated: %s -->"
END = "<!-- /generated: %s -->"


def _table(headings, rows):
    out = ["| " + " | ".join(headings) + " |",
           "|" + "|".join("---" for _ in headings) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def blocks():
    """Every generated block, keyed by its marker name."""
    return {
        "setup-status": _table(
            ["`status:`", "Exit", "Meaning"],
            [("`%s`" % slug, code, meaning)
             for slug, code, meaning in setup_script.STATUS_MEANINGS]),

        "setup-config-keys": _table(
            ["Key", "Effect"],
            [("`%s`" % key, meaning)
             for key, meaning in setup_script.CONFIG_KEY_MEANINGS]),

        "show-exits": _table(
            ["Exit", "Meaning"],
            list(show.EXIT_MEANINGS)),

        "catalogue-exits": _table(
            ["Exit", "Meaning"],
            list(catalogue.EXIT_MEANINGS)),

        "catalogue-fields": _table(
            ["Field", "What it is"],
            [("`%s`" % field, meaning)
             for field, meaning in catalogue.TEMPLATE_FIELDS]),

        "intent-schema": _table(
            ["Field", "Required", "Meaning"],
            [("`%s`" % field, "**yes**" if required else "no", meaning)
             for field, required, meaning in plan.INTENT_FIELDS]),

        "plan-exits": _table(
            ["Exit", "Meaning"],
            list(plan.EXIT_MEANINGS)),

        "apply-exits": _table(
            ["Exit", "Meaning"],
            list(apply_script.EXIT_MEANINGS)),

        "apply-statuses": _table(
            ["Status", "Meaning"],
            [("`%s`" % status, meaning)
             for status, meaning in apply_script.ROW_STATUS_MEANINGS]),
    }


def _replace(text, name, body):
    """Swap one marked block's contents, leaving the markers in place."""
    begin, end = BEGIN % name, END % name
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    return text[:start] + "\n" + body + "\n" + text[stop:]


def _current(text, name):
    begin, end = BEGIN % name, END % name
    if begin not in text:
        return None
    start = text.index(begin) + len(begin)
    return text[start:text.index(end)].strip("\n")


def main(argv):
    if len(argv) != 2 or argv[1] not in ("--check", "--write"):
        sys.stderr.write("usage: contract.py {--check|--write}\n")
        return 2

    # newline translation is left on for the read so the comparison below is about the
    # blocks rather than the checkout: a CRLF working tree reported all nine stale.
    # the write keeps newline="" so what goes back is the LF this file holds in the index.
    text = io.open(SKILL, encoding="utf-8").read()
    wanted = blocks()

    missing = [name for name in wanted if (BEGIN % name) not in text]
    if missing:
        sys.stderr.write("SKILL.md has no marker for: %s\n" % ", ".join(sorted(missing)))
        return 1

    stale = [name for name, body in sorted(wanted.items()) if _current(text, name) != body]

    if argv[1] == "--check":
        if stale:
            sys.stderr.write(
                "SKILL.md is out of date with the scripts' constants: %s\n"
                "run  contract.py --write  to regenerate\n" % ", ".join(stale))
            return 1
        sys.stdout.write("all %d generated blocks match the scripts\n" % len(wanted))
        return 0

    for name, body in sorted(wanted.items()):
        text = _replace(text, name, body)
    io.open(SKILL, "w", encoding="utf-8", newline="").write(text)
    sys.stdout.write("rewrote %d block(s): %s\n"
                     % (len(stale), ", ".join(stale) if stale else "none were stale"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
