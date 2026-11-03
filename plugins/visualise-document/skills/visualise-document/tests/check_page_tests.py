"""Deterministic self-check for the visualise-document page checker.

The markup checks run over small synthetic pages, and the geometry checks over synthetic measurements,
so nothing here opens a browser: the measuring script is exercised by hand against real pages instead.
The browser fallbacks are exercised with sys.executable and a stubbed subprocess.run standing in for a browser,
so nothing opens a real browser there either.
Each check has cases that must fire and cases that must stay quiet, built one variation at a time.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/check_page_tests.py
"""

import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

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

# the README addresses spelt out here rather than read from check_page,
# so a typo in its constant fails the quiet cases
README_BASE = "https://github.com/tundra-east/tundra/blob/main/plugins/visualise-document/"
README_EN = README_BASE + "README.md"
README_ZH_TW = README_BASE + "README.zh-TW.md"
README_ZH_CN = README_BASE + "README.zh-CN.md"

# the closing line a finished page carries,
# so the pages built for the other checks raise only the finding each one varies
FOOTER = '<footer><a href="%s">visualise-document</a></footer>' % README_EN


def _page(*captions, ids=("fig-1",)):
    figures = "".join('<figure id="%s"><svg></svg><figcaption>%s</figcaption></figure>' % (i, c)
                      for i, c in zip(ids, captions))
    return "<html><head></head><body><main>%s</main>%s</body></html>" % (figures, FOOTER)


def _gathered(page):
    return [f for f in check_page.markup_findings(page) if "gathered list" in f]


def _duplicates(page):
    return [f for f in check_page.markup_findings(page) if "is used" in f]


def _unshown(page):
    return [f for f in check_page.markup_findings(page) if "does not show its id" in f]


def _unlinked(page):
    return [f for f in check_page.markup_findings(page) if "does not link to the plugin's README" in f]


def _body(inner):
    # a page holding only this, with no closing line unless inner is one
    return "<html><head></head><body>%s</body></html>" % inner


def _figure(inner, ident="fig-1"):
    return '<html><head></head><body><main><figure id="%s">%s</figure></main>%s</body></html>' % (ident, inner, FOOTER)


# ----- synthetic measurements ----------------------------------------------------


def _b(x, y, w, h, text=None):
    b = {"x": x, "y": y, "w": w, "h": h}
    if text is not None:
        b["text"] = text
    return b


def _measured(texts, rects=(), frame=(0, 0, 800, 400), page=(1264, 1264), figure="fig-1", boxes=(), text_width=None):
    measured = {"page": {"scrollWidth": page[0], "clientWidth": page[1]},
                "svgs": [{"figure": figure, "frame": _b(*frame), "rects": list(rects), "texts": list(texts),
                          "boxes": list(boxes)}]}
    if text_width is not None:
        measured["page"]["textWidth"] = text_width
    return measured


RED = [208, 59, 59, 1]
GREY = [136, 136, 136, 1]


def _box(fill=RED, stroke=RED, fill_opacity=1.0, dashed=False, x=100, y=100, w=120, h=40):
    b = _b(x, y, w, h)
    b.update({"fill": fill, "fillOpacity": fill_opacity, "stroke": stroke, "dashed": dashed})
    return b


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
        elif "paragraphs run" in f:
            kinds.append("text column")
        elif "no tint" in f:
            kinds.append("untinted")
        elif "no fill" in f:
            kinds.append("unfilled")
    return kinds


def _untinted(*boxes, **kw):
    # the coloured check alone, since its quiet cases include neutral borders the neutral check reports
    return [k for k in _kinds(_measured([], boxes=boxes, **kw)) if k != "unfilled"]


def _unfilled(*boxes, **kw):
    return _kinds(_measured([], boxes=boxes, **kw))


# laid over white, the red's channels close by its spread times the fill's alpha
SPREAD = 208 - 59
# laid over white, the grey's channels sit this far below white times its alpha
GREY_DEPTH = 255 - 136


# ----- browser fallbacks -----------------------------------------------------------

# the Python running this suite stands in for a browser:
# a real executable that rejects the browser's flags, so it measures nothing and writes no screenshot
NOT_A_BROWSER = sys.executable

PLAIN = "<html><head></head><body>%s</body></html>" % FOOTER
DUPLICATED = '<html><head></head><body><i id="a"></i><i id="a"></i>%s</body></html>' % FOOTER
MEASURED = {"page": {"scrollWidth": 1264, "clientWidth": 1264, "scrollHeight": check_page.SHOT_HEIGHT}, "svgs": []}


def _main(page_text, *flags, browser=NOT_A_BROWSER, shots=None):
    """The exit status of check_page.main on a temporary copy of the page, with its printed lines kept out of the report.
    shots, when given, is the folder passed to --shots."""
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "page.html")
        with open(page, "w", encoding="utf-8", newline="\n") as f:
            f.write(page_text)
        argv = [page, "--browser", browser]
        for flag in flags:
            # --shots names a folder inside the temporary one
            argv += [flag, os.path.join(tmp, "shots")] if flag == "--shots" else [flag]
        if shots is not None:
            argv += ["--shots", shots]
        # main reconfigures sys.stdout, which a StringIO cannot do
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with contextlib.redirect_stdout(out):
            return check_page.main(argv)


def _main_measured(page_text, *flags, measured=MEASURED, **kwargs):
    """_main with a browser that returns these measurements, and writes no screenshot unless subprocess.run is stubbed."""
    real = check_page.measure
    check_page.measure = lambda page_text, browser: measured
    try:
        return _main(page_text, *flags, **kwargs)
    finally:
        check_page.measure = real


def _measure_with_first_failing(error, browsers):
    """measure_with_any with browser "a" raising error and every other browser measuring the page."""
    def fake(page_text, browser):
        if browser == "a":
            raise error
        return MEASURED
    real = check_page.measure
    check_page.measure = fake
    try:
        return check_page.measure_with_any(PLAIN, browsers)
    finally:
        check_page.measure = real


# the page each screenshot was taken of, by the screenshot's path, as the fake browser read it
_received = {}


def _fake_run(args, **kwargs):
    """A stand-in for subprocess.run: browser "a" writes nothing, "b" every shot, "c" the light shots only.
    The browser is named by its file name, so a real file called "b" works as --browser."""
    browser = os.path.basename(args[0])
    png = [a for a in args if a.startswith("--screenshot=")][0][len("--screenshot="):]
    page = urllib.request.url2pathname(urllib.parse.urlparse(args[-1]).path)
    with open(page, encoding="utf-8") as f:
        _received[png] = f.read()
    dark = "--force-dark-mode" in args
    if browser == "b" or (browser == "c" and not dark):
        with open(png, "wb") as f:
            f.write(b"png")


@contextlib.contextmanager
def _fake_browsers():
    real = subprocess.run
    subprocess.run = _fake_run
    _received.clear()
    try:
        yield _received
    finally:
        subprocess.run = real


def _expected_shots(out, height):
    # the names _shoot writes, a light and a dark shot for each offset
    return [(pathlib.Path(out) / ("%s_%05d.png" % (mode, offset))).resolve()
            for offset in check_page.shot_offsets(height) for mode in ("light", "dark")]


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


def test_unshown_id_fires():
    check("the id appears nowhere", len(_unshown(_page("A short title"))), 1)
    check("the id appears only inside the drawing",
          len(_unshown(_figure("<svg><text>fig-1</text></svg><figcaption>A short title</figcaption>"))), 1)
    check("fig-12 in the caption is not fig-1", len(_unshown(_page("fig-12 A short title"))), 1)
    check("the label inside a nested svg only",
          len(_unshown(_figure("<svg><svg><text>fig-1</text></svg></svg><figcaption>A short title</figcaption>"))), 1)
    check("the label after a nested svg closes, still in the outer one",
          len(_unshown(_figure("<svg><svg></svg><text>fig-1</text></svg><figcaption>A short title</figcaption>"))), 1)
    named = _unshown(_page("A short title", ids=("fig-4",)))
    check("the figure is named", [f.startswith("fig-4 does not show its id") and "'fig-4'" in f for f in named], [True])


def test_unshown_id_quiet():
    check("the id in the caption", _unshown(_page("fig-1 A short title")), [])
    check("the id in a label element above the drawing",
          _unshown(_figure('<div class="fig-label">fig-3</div><svg></svg><figcaption>A short title</figcaption>', "fig-3")), [])
    check("the id in its own element, then text starting with a digit",
          _unshown(_figure("<span>fig-2</span>0.1.7 ships the split<svg></svg>", "fig-2")), [])
    check("a figure without an id", _unshown("<html><body><figure><svg></svg><figcaption>A short title</figcaption></figure></body></html>"), [])
    check("a self-closing svg before the caption", _unshown(_figure("<svg/><figcaption>fig-1 A short title</figcaption>")), [])


def test_unshown_id_beside_other_checks():
    sentence = "Excel stays before the split" + STOP
    shown = _page("fig-1 " + sentence + "D26" + COMMA + "D34")
    check("a gathered list in a caption that shows the id adds no id finding", (len(_gathered(shown)), _unshown(shown)), (1, []))
    # the existing gathered-list pages carry no id in their captions, so they raise both findings
    unshown = _page(sentence + "D26" + COMMA + "D34")
    check("each filter keeps only its own finding",
          (len(_gathered(unshown)), len(_unshown(unshown)), len(check_page.markup_findings(unshown))), (1, 1, 2))
    duplicated = '<svg><defs><marker id="ah"></marker></defs></svg><svg><defs><marker id="ah"></marker></defs></svg>'
    check("the duplicate-id page has no figure, so no id finding", _unshown(duplicated), [])


def test_readme_link_fires():
    check("a page with no link at all", len(_unlinked(_body(""))), 1)
    check("a link to another plugin's README",
          len(_unlinked(_body('<a href="https://github.com/tundra-east/tundra/blob/main/plugins/whoami/README.md">whoami</a>'))), 1)
    check("a link to the repository root",
          len(_unlinked(_body('<a href="https://github.com/tundra-east/tundra">visualise-document</a>'))), 1)
    check("a README in a language the plugin does not ship",
          len(_unlinked(_body('<a href="%s">visualise-document</a>' % (README_BASE + "README.ja.md")))), 1)
    check("the README address as plain text, not a link", len(_unlinked(_body("<p>%s</p>" % README_EN))), 1)
    check("the README address on a link element, not an a", len(_unlinked(_body('<link href="%s">' % README_EN))), 1)
    found = _unlinked(_body(""))
    check("the finding opens with what is missing",
          [f.startswith("the page does not link to the plugin's README") for f in found], [True])
    check("the finding names all three README addresses",
          [" or ".join((README_EN, README_ZH_TW, README_ZH_CN)) in f for f in found], [True])


def test_readme_link_quiet():
    check("the English README", _unlinked(_body('<a href="%s">visualise-document</a>' % README_EN)), [])
    check("the Traditional Chinese README", _unlinked(_body('<a href="%s">visualise-document</a>' % README_ZH_TW)), [])
    check("the Simplified Chinese README", _unlinked(_body('<a href="%s">visualise-document</a>' % README_ZH_CN)), [])
    check("whitespace around the address", _unlinked(_body('<a href="  %s\n ">visualise-document</a>' % README_EN)), [])


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


def test_text_column():
    limit = check_page.TEXT_MAX_W + check_page.TOLERANCE
    check("a text column exactly at the limit", _kinds(_measured([], text_width=limit)), [])
    check("a text column one pixel past the limit", _kinds(_measured([], text_width=limit + 1)), ["text column"])
    check("a page with no text width", _kinds(_measured([])), [])
    found = check_page.geometry_findings(_measured([], text_width=limit + 1))
    check("the measured width is named", ["paragraphs run %d px wide" % (limit + 1) in f for f in found], [True])


def test_unfilled_box_fires():
    check("fill none behind a grey border", _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY)), ["unfilled"])
    check("opaque white fill", _unfilled(_box(fill=[255, 255, 255, 1], stroke=GREY)), ["unfilled"])
    lightest = check_page.UNFILLED_LIGHTEST
    check("a neutral fill exactly at the lightest", _unfilled(_box(fill=[lightest] * 3 + [1], stroke=GREY)), ["unfilled"])
    # a grey border thinned until its channels over white sit one step below the lightest
    faint = (255 - (lightest - 1)) / GREY_DEPTH
    check("a faint border still darker than the lightest",
          _unfilled(_box(fill=[0, 0, 0, 0], stroke=[136, 136, 136, faint])), ["unfilled"])
    check("a box one pixel above the minimum size",
          _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY, w=check_page.BOX_MIN_W + 1, h=check_page.BOX_MIN_H + 1)),
          ["unfilled"])
    # a 200 grey fill thinned until its channels over white sit halfway between the lightest and white
    thinned = (255 - lightest) / (255 - 200) / 2
    check("a neutral fill thinned by fill-opacity",
          _unfilled(_box(fill=[200, 200, 200, 1], fill_opacity=thinned, stroke=GREY)), ["unfilled"])
    found = check_page.geometry_findings(_measured([], frame=(50, 20, 800, 400), figure="fig-3",
                                                   boxes=[_box(fill=[0, 0, 0, 0], stroke=GREY, x=150, y=120)]))
    check("the figure, position in the frame and border are named",
          [f.startswith("fig-3:") and "box at 100,100" in f and "#888888 border" in f for f in found], [True])


def test_unfilled_box_quiet():
    lightest = check_page.UNFILLED_LIGHTEST
    check("a neutral fill one step darker than the lightest",
          _unfilled(_box(fill=[lightest - 1] * 3 + [1], stroke=GREY)), [])
    check("a pale neutral fill", _unfilled(_box(fill=[243, 242, 239, 1], stroke=GREY)), [])
    check("the same 200 grey fill at full fill-opacity", _unfilled(_box(fill=[200, 200, 200, 1], stroke=GREY)), [])
    check("a fill whose darkest channel alone is below the lightest",
          _unfilled(_box(fill=[255, 255, lightest - 1, 1], stroke=GREY)), [])
    check("a dashed border", _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY, dashed=True)), [])
    check("a gradient fill", _unfilled(_box(fill=None, stroke=GREY)), [])
    check("no stroke", _unfilled(_box(fill=[0, 0, 0, 0], stroke=None)), [])
    check("a stroke of none", _unfilled(_box(fill=[0, 0, 0, 0], stroke=[0, 0, 0, 0])), [])
    # a grey border thinned until its channels over white sit halfway between the lightest and white
    faint = (255 - lightest) / GREY_DEPTH / 2
    check("a border too faint to tell from the page", _unfilled(_box(fill=[0, 0, 0, 0], stroke=[136, 136, 136, faint])), [])
    check("a shape at the minimum size",
          _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY, w=check_page.BOX_MIN_W, h=check_page.BOX_MIN_H)), [])
    check("a box at the minimum height, wide enough", _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY, h=check_page.BOX_MIN_H)), [])
    check("a box at the minimum width, tall enough", _unfilled(_box(fill=[0, 0, 0, 0], stroke=GREY, w=check_page.BOX_MIN_W)), [])
    check("a coloured border with no fill is reported once, as untinted", _unfilled(_box(fill=[0, 0, 0, 0])), ["untinted"])


def test_untinted_box_fires():
    check("fill none behind a red border", _untinted(_box(fill=[0, 0, 0, 0])), ["untinted"])
    check("opaque white fill", _untinted(_box(fill=[255, 255, 255, 1])), ["untinted"])
    check("red fill at alpha 0.01", _untinted(_box(fill=[208, 59, 59, 0.01])), ["untinted"])
    check("red fill thinned by fill-opacity 0.02", _untinted(_box(fill_opacity=0.02)), ["untinted"])
    # alpha 0.2 or fill-opacity 0.2 alone leaves chroma about 30, and both together about 6
    check("fill alpha and fill-opacity multiply", _untinted(_box(fill=[208, 59, 59, 0.2], fill_opacity=0.2)), ["untinted"])
    at = check_page.TINT_CHROMA_MAX / SPREAD
    check("fill chroma exactly at the limit", _untinted(_box(fill=[208, 59, 59, at])), ["untinted"])
    found = check_page.geometry_findings(_measured([], frame=(50, 20, 800, 400), figure="fig-3",
                                                   boxes=[_box(fill=[0, 0, 0, 0], x=150, y=120)]))
    check("the figure, position in the frame and border are named",
          [f.startswith("fig-3:") and "box at 100,100" in f and "#d03b3b border" in f for f in found], [True])
    # fill none lays over white as pure white, so its chroma is 0
    check("the fill chroma is named", ["(fill chroma 0)" in f for f in found], [True])
    unnamed = check_page.geometry_findings(_measured([], figure="", boxes=[_box(fill=[0, 0, 0, 0])]))
    check("a figure with no id is named by its index", [f.startswith("svg 1:") for f in unnamed], [True])


def test_untinted_box_quiet():
    check("red fill at alpha 0.1 is a tint", _untinted(_box(fill=[208, 59, 59, 0.1])), [])
    above = (check_page.TINT_CHROMA_MAX + 0.5) / SPREAD
    check("fill chroma just above the limit", _untinted(_box(fill=[208, 59, 59, above])), [])
    check("a dashed border", _untinted(_box(fill=[0, 0, 0, 0], dashed=True)), [])
    check("a grey border", _untinted(_box(fill=[0, 0, 0, 0], stroke=[136, 136, 136, 1])), [])
    check("a red border at alpha 0.1 is not coloured", _untinted(_box(fill=[0, 0, 0, 0], stroke=[208, 59, 59, 0.1])), [])
    check("an arrowhead-sized shape", _untinted(_box(fill=[0, 0, 0, 0], w=20, h=12)), [])
    check("a box too short, wide enough", _untinted(_box(fill=[0, 0, 0, 0], h=12)), [])
    check("a box too narrow, tall enough", _untinted(_box(fill=[0, 0, 0, 0], w=20)), [])
    check("a gradient fill", _untinted(_box(fill=None)), [])
    check("no stroke", _untinted(_box(fill=[0, 0, 0, 0], stroke=None)), [])


def test_find_browser():
    check("a given path that does not exist", check_page.find_browser(os.path.join(_HERE, "no-such-browser")), None)
    check("a given path that exists", check_page.find_browser(os.path.abspath(__file__)), os.path.abspath(__file__))


def test_find_browsers():
    check("a given path that does not exist", check_page.find_browsers(os.path.join(_HERE, "no-such-browser")), [])
    check("a given path that exists, alone", check_page.find_browsers(os.path.abspath(__file__)), [os.path.abspath(__file__)])


def test_inject():
    check("just before </head>", check_page._inject("<html><head></head><body></body></html>", "<x>"),
          "<html><head><x></head><body></body></html>")
    check("before an upper-case </HEAD>", check_page._inject("<html><head></HEAD><body></body></html>", "<x>"),
          "<html><head><x></head><body></body></html>")
    check("only the first of two heads", check_page._inject("<head></head><head></head>", "<x>"),
          "<head><x></head><head></head>")
    check("a page with no head gets it at the start", check_page._inject("<body></body>", "<x>"), "<x><body></body>")


def test_shot_offsets():
    tall = check_page.SHOT_HEIGHT
    check("a page of height 0", check_page.shot_offsets(0), [0])
    check("a page of height 1", check_page.shot_offsets(1), [0])
    check("a page exactly one window tall", check_page.shot_offsets(tall), [0])
    check("one pixel past a window", check_page.shot_offsets(tall + 1), [0, tall])


def test_shot_args():
    light = check_page.shot_args("browser", "file:///page.html", "out.png", "profile", False)
    dark = check_page.shot_args("browser", "file:///page.html", "out.png", "profile", True)
    check("the browser comes first", light[0], "browser")
    check("headless", "--headless=new" in light, True)
    check("the page comes last", light[-1], "file:///page.html")
    check("the window is WIDTH by SHOT_HEIGHT",
          "--window-size=%d,%d" % (check_page.WIDTH, check_page.SHOT_HEIGHT) in light, True)
    check("a profile of its own", "--user-data-dir=profile" in light, True)
    check("the screenshot path", "--screenshot=out.png" in light, True)
    dark_flags = ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
    check("dark adds both dark flags", [f in dark for f in dark_flags], [True, True])
    check("light carries neither dark flag", [f in light for f in dark_flags], [False, False])
    check("dark keeps the page last", dark[-1], "file:///page.html")


def test_measure_with_any():
    check("no browser measures", check_page.measure_with_any(PLAIN, [NOT_A_BROWSER]), (None, None))
    check("past a browser raising RuntimeError",
          _measure_with_first_failing(RuntimeError("no measurements"), ["a", "b"]), (MEASURED, "b"))
    check("past a browser raising OSError", _measure_with_first_failing(OSError("cannot run"), ["a", "b"]), (MEASURED, "b"))
    check("past a browser timing out",
          _measure_with_first_failing(subprocess.TimeoutExpired("a", 120), ["a", "b"]), (MEASURED, "b"))


def test_screenshots():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "shots")
        got = check_page.screenshots(PLAIN, check_page.SHOT_HEIGHT, [NOT_A_BROWSER], out)
        check("no browser writes a screenshot", got, ([], None))
    # height 1 needs a single offset, so a light and a dark shot
    with _fake_browsers() as received:
        with tempfile.TemporaryDirectory() as out:
            check("a browser writing every shot", check_page.screenshots(PLAIN, 1, ["b"], out), (_expected_shots(out, 1), "b"))
        with tempfile.TemporaryDirectory() as out:
            check("past a browser writing nothing", check_page.screenshots(PLAIN, 1, ["a", "b"], out),
                  (_expected_shots(out, 1), "b"))
        with tempfile.TemporaryDirectory() as out:
            check("past a browser writing the light shot only", check_page.screenshots(PLAIN, 1, ["c", "b"], out),
                  (_expected_shots(out, 1), "b"))
        with tempfile.TemporaryDirectory() as out:
            check("a missing dark shot gives None", check_page._shoot(PLAIN, 1, "c", pathlib.Path(out)), None)
        with tempfile.TemporaryDirectory() as tmp:
            # screenshots left by an earlier run at the paths of the first light and dark shots
            stale = os.path.join(tmp, "light_00000.png")
            for name in ("light_00000.png", "dark_00000.png"):
                with open(os.path.join(tmp, name), "wb") as f:
                    f.write(b"stale")
            got = check_page.screenshots(PLAIN, 1, ["a"], tmp)
            check("a stale screenshot is not counted", got, ([], None))
            check("a stale screenshot is removed", os.path.exists(stale), False)
        # one pixel past a window needs a second offset, one window down
        tall = check_page.SHOT_HEIGHT
        with tempfile.TemporaryDirectory() as out:
            received.clear()
            shots = _expected_shots(out, tall + 1)
            check("a page past one window gets every offset", check_page.screenshots(PLAIN, tall + 1, ["b"], out), (shots, "b"))
            moved = [p for p in shots if p.name.endswith("_%05d.png" % tall)]
            check("the copy one window down is moved up a window, light and dark",
                  ["translateY(-%dpx)" % tall in received.get(str(p), "") for p in moved], [True, True])


def test_main_no_measurements():
    check("no browser returns measurements", _main(PLAIN), check_page.EXIT_NO_BROWSER)
    check("--shots with no measurements", _main(PLAIN, "--shots"), check_page.EXIT_NO_BROWSER)
    # a page with a markup finding, and no browser that measures it
    check("--shots with no measurements and a markup finding", _main(DUPLICATED, "--shots"), check_page.EXIT_NO_BROWSER)


def test_main_no_screenshots():
    check("measured, no screenshot, no finding", _main_measured(PLAIN, "--shots"), check_page.EXIT_NO_BROWSER)
    check("measured, no screenshot, a markup finding", _main_measured(DUPLICATED, "--shots"), check_page.EXIT_FOUND)
    check("measured without --shots, no finding", _main_measured(PLAIN), check_page.EXIT_OK)


def test_main_screenshots():
    # a page measured one pixel taller than a window, and a browser file named "b" that writes every shot
    tall = check_page.SHOT_HEIGHT + 1
    measured = dict(MEASURED, page=dict(MEASURED["page"], scrollHeight=tall))
    with _fake_browsers(), tempfile.TemporaryDirectory() as tmp:
        browser = os.path.join(tmp, "b")
        with open(browser, "wb"):
            pass
        shots = os.path.join(tmp, "shots")
        check("measured, every screenshot, no finding", _main_measured(PLAIN, measured=measured, browser=browser, shots=shots),
              check_page.EXIT_OK)
        check("the folder holds the shots of every offset",
              sorted(p.resolve() for p in pathlib.Path(shots).glob("*.png")), sorted(_expected_shots(shots, tall)))


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
