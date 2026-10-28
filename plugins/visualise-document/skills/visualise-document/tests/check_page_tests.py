"""Deterministic self-check for the visualise-document page checker.

The markup checks run over small synthetic pages, and the geometry checks over synthetic measurements,
so nothing here opens a browser: the measuring script is exercised by hand against real pages instead.
Each check has cases that must fire and cases that must stay quiet, built one variation at a time.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/check_page_tests.py
"""

import os
import sys

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "scripts")))
import check_page  # noqa: E402

# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- synthetic pages ---------------------------------------------------------

# CJK punctuation spelt as escapes, so the source stays ASCII
COMMA = "、"
STOP = "。"
OPEN = "（"
CLOSE = "）"
SECTION = "§"


def _page(*captions, ids=("fig-1",)):
    figures = "".join('<figure id="%s"><svg></svg><figcaption>%s</figcaption></figure>' % (i, c)
                      for i, c in zip(ids, captions))
    return "<html><head></head><body><main>%s</main></body></html>" % figures


def _gathered(page):
    return [f for f in check_page.markup_findings(page) if "gathered list" in f]


def _duplicates(page):
    return [f for f in check_page.markup_findings(page) if "is used" in f]


# ----- synthetic measurements ----------------------------------------------------


def _b(x, y, w, h, text=None):
    b = {"x": x, "y": y, "w": w, "h": h}
    if text is not None:
        b["text"] = text
    return b


def _measured(texts, rects=(), frame=(0, 0, 800, 400), page=(1264, 1264), figure="fig-1"):
    return {"page": {"scrollWidth": page[0], "clientWidth": page[1]},
            "svgs": [{"figure": figure, "frame": _b(*frame), "rects": list(rects), "texts": list(texts)}]}


def _kinds(measured):
    found = check_page.geometry_findings(measured)
    kinds = []
    for f in found:
        if "cut off" in f:
            kinds.append("clipped")
        elif "runs past" in f:
            kinds.append("past box")
        elif "drawn over" in f:
            kinds.append("overlap")
        elif "scrolls sideways" in f:
            kinds.append("too wide")
    return kinds


# ----- tests -----------------------------------------------------------------------


def test_gathered_list_fires():
    sentence = "Excel stays before the split" + STOP
    check("CJK list after a full stop", len(_gathered(_page(sentence + "D26" + COMMA + "D19" + COMMA + SECTION + "3.8"))), 1)
    check("two identifiers are already a list", len(_gathered(_page(sentence + "D26" + COMMA + "D34"))), 1)
    check("English list after a sentence", len(_gathered(_page("Excel stays before the split. D26, D19, " + SECTION + "3.8"))), 1)
    check("a labelled list such as 'reflects: D1, D2'", len(_gathered(_page(sentence + "reflects:D26" + COMMA + "D27" + COMMA + "D34"))), 1)
    check("whitespace across lines in the markup", len(_gathered(_page(sentence + "\n   D26" + COMMA + "\n D34\n"))), 1)
    named = _gathered(_page(sentence + "D26" + COMMA + "D34", ids=("fig-7",)))
    check("the figure is named", [f.startswith("fig-7:") for f in named], [True])


def test_gathered_list_quiet():
    inline = "Excel stays before the split " + OPEN + "D34" + CLOSE + STOP + "The invoice links to it " + OPEN + "D26" + CLOSE + STOP
    check("each sentence cites its own passages", _gathered(_page(inline)), [])
    check("one identifier at the end is a citation, not a list", _gathered(_page("Excel stays before the split" + STOP + "D26")), [])
    check("a closing list of words with no digits", _gathered(_page("Two buyers share it" + STOP + "owner" + COMMA + "buyer")), [])
    check("a list inside the last sentence", _gathered(_page("It splits by D26" + COMMA + "D34 and the rest" + STOP)), [])
    check("a version number is not a sentence end", _gathered(_page("Version 3.1 holds" + STOP)), [])
    check("no caption at all", _gathered("<html><body><figure id=\"fig-1\"><svg></svg></figure></body></html>"), [])


def test_duplicate_ids():
    page = '<svg><defs><marker id="ah"></marker></defs></svg><svg><defs><marker id="ah"></marker></defs></svg>'
    found = _duplicates(page)
    check("a marker id used twice", len(found), 1)
    check("the count is named", ["2 times" in f for f in found], [True])
    check("distinct ids are quiet", _duplicates('<marker id="a"></marker><marker id="b"></marker>'), [])


def test_clipped():
    check("text past the right edge", _kinds(_measured([_b(760, 10, 60, 14, "label")])), ["clipped"])
    check("text past the top edge", _kinds(_measured([_b(10, -6, 60, 14, "label")])), ["clipped"])
    check("text one pixel over is rounding", _kinds(_measured([_b(740, 10, 61, 14, "label")])), [])
    check("clipped text is not also reported as past its box",
          _kinds(_measured([_b(760, 10, 60, 14, "label")], rects=[_b(700, 0, 90, 30)])), ["clipped"])


def test_past_box():
    box = _b(100, 100, 120, 40)
    check("label wider than its box", _kinds(_measured([_b(90, 110, 140, 14, "label")], rects=[box])), ["past box"])
    check("label inside its box", _kinds(_measured([_b(110, 110, 80, 14, "label")], rects=[box])), [])
    # the box is the smallest rectangle around the label's centre, not a lane or frame drawn behind everything
    lane = _b(0, 0, 800, 400)
    check("the smallest holder decides", _kinds(_measured([_b(90, 110, 140, 14, "label")], rects=[lane, box])), ["past box"])
    check("a big background alone holds it", _kinds(_measured([_b(90, 110, 140, 14, "label")], rects=[lane])), [])
    check("a free-standing label has no box", _kinds(_measured([_b(300, 300, 140, 14, "label")], rects=[box])), [])


def test_overlap():
    check("two labels at the same spot", _kinds(_measured([_b(10, 10, 60, 14, "a"), _b(20, 12, 60, 14, "b")])), ["overlap"])
    check("stacked lines that touch", _kinds(_measured([_b(10, 10, 60, 14, "a"), _b(10, 23, 60, 14, "b")])), [])
    check("side by side with a gap", _kinds(_measured([_b(10, 10, 60, 14, "a"), _b(80, 10, 60, 14, "b")])), [])


def test_page_width():
    check("a page wider than the window", _kinds(_measured([], page=(3000, 1264))), ["too wide"])
    check("a page that fits", _kinds(_measured([], page=(1264, 1264))), [])


def test_find_browser():
    check("a given path that does not exist", check_page.find_browser(os.path.join(_HERE, "no-such-browser")), None)
    check("a given path that exists", check_page.find_browser(os.path.abspath(__file__)), os.path.abspath(__file__))


def _run_all():
    global _group
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            _group = name
            fn()
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
            if not ok:
                line += " -- " + detail
            # the detail can quote a non-ASCII value, and the console may be cp1252
            print(line.encode("ascii", "backslashreplace").decode("ascii"))
    npass = sum(1 for r in _results if r[2])
    nfail = len(_results) - npass
    print("")
    print("%d passed, %d failed" % (npass, nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
