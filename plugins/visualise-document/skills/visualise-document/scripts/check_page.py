"""Report the layout defects of a visualise-document page that can be measured rather than judged.

Two kinds of check run:
- in the page's markup: element ids used twice (a second marker with the same id points every arrow at the first),
  and a caption that ends in a gathered list of source identifiers instead of citing each sentence;
- in a headless Chromium-family browser at 1280 px: text cut off by its figure's edge, text running past the box it sits in,
  two labels drawn over each other, a page wider than the window,
  and a box with a coloured solid border but no tint of colour behind it.

What it cannot see is still found by looking at the screenshots: a line drawn through a label, crossing lines, a missing arrowhead.
With --shots it also writes those screenshots, light and dark, one window apart down the whole page, and prints their paths.

Usage:
    python check_page.py <page.html> [--browser <path>] [--shots <folder>]

Exit status 0 when nothing is found, 1 when something is,
2 when no browser can be found or, with --shots, when no browser writes a screenshot (the markup checks still print).
"""

import argparse
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_NO_BROWSER = 2

WIDTH = 1280
SHOT_HEIGHT = 1400
# a measured edge within this many pixels of its limit is rounding, not a defect
TOLERANCE = 1.0
# two labels overlap only when they share more than this in both directions, so stacked lines that touch do not count
OVERLAP_MIN = 2.0
# a shape is a box only above this size, so arrowheads and dots are left out
BOX_MIN_W = 24.0
BOX_MIN_H = 16.0
# chroma (largest minus smallest channel, 0-255) of a colour once laid over white —
# a border above BORDER_CHROMA_MIN is coloured, and a fill at or below TINT_CHROMA_MAX shows no colour.
# measured fills of coloured boxes fell at 1 or at 12 and above, with nothing between
BORDER_CHROMA_MIN = 40.0
TINT_CHROMA_MAX = 8.0

BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
BROWSER_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "brave-browser", "chrome")

# a source identifier: a short token carrying a digit, such as D12, section 3.1, Q4 or FR-4
_NOT_IDENT = r"\s\u3001,\uff0c;\uff1b\u3002!?\uff01\uff1f()\uff08\uff09"
IDENT = r"(?=[^%s]*\d)[^%s]{1,12}" % (_NOT_IDENT, _NOT_IDENT)
# a caption whose last sentence is nothing but two or more identifiers separated by list punctuation;
# an ASCII full stop ends a sentence only before a space, since it also sits inside identifiers such as 3.1
GATHERED = re.compile(r"(?:^|[\u3002\uff01\uff1f)\uff09]|[.!?]\s)\s*(%s(?:\s*[\u3001,\uff0c;\uff1b]\s*%s)+)\s*[\u3002.]?\s*$" % (IDENT, IDENT))

MEASURE_JS = r"""
<script>
addEventListener('load', () => {
  const root = document.documentElement;
  const out = {page: {scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, scrollHeight: root.scrollHeight}, svgs: []};
  const box = r => ({x: r.left, y: r.top, w: r.width, h: r.height});
  // a colour in any CSS syntax read back as [r, g, b, alpha] by painting one pixel,
  // [0, 0, 0, 0] for none, and null for what is not a plain colour, such as a gradient
  const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const colour = v => {
    if (!v || v === 'none') return [0, 0, 0, 0];
    if (!CSS.supports('color', v)) return null;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = v;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  document.querySelectorAll('svg').forEach(svg => {
    const fig = svg.closest('figure');
    const entry = {figure: fig && fig.id ? fig.id : '', frame: box(svg.getBoundingClientRect()), rects: [], texts: [], boxes: []};
    svg.querySelectorAll('rect').forEach(r => {
      const b = r.getBoundingClientRect();
      if (b.width > 0 && b.height > 0) entry.rects.push(box(b));
    });
    svg.querySelectorAll('rect, circle, ellipse, polygon').forEach(el => {
      const b = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      entry.boxes.push(Object.assign(box(b), {
        fill: colour(s.fill), fillOpacity: parseFloat(s.fillOpacity), stroke: colour(s.stroke),
        dashed: s.strokeDasharray !== 'none' && s.strokeDasharray !== ''}));
    });
    svg.querySelectorAll('text').forEach(t => {
      const b = t.getBoundingClientRect();
      if (b.width > 0 && b.height > 0 && t.textContent.trim()) entry.texts.push(Object.assign(box(b), {text: t.textContent.trim()}));
    });
    out.svgs.push(entry);
  });
  const pre = document.createElement('pre');
  pre.id = '__check_page';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
});
</script>
"""


class _Markup(HTMLParser):
    """Collects every element id with its count, and the text of each figure caption with the figure it belongs to."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = {}
        self.captions = []
        self._figure = []
        self._caption = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids[attrs["id"]] = self.ids.get(attrs["id"], 0) + 1
        if tag == "figure":
            self._figure.append(attrs.get("id") or "")
        elif tag == "figcaption":
            self._caption = []

    def handle_endtag(self, tag):
        if tag == "figure" and self._figure:
            self._figure.pop()
        elif tag == "figcaption" and self._caption is not None:
            self.captions.append((self._figure[-1] if self._figure else "", "".join(self._caption)))
            self._caption = None

    def handle_data(self, data):
        if self._caption is not None:
            self._caption.append(data)


def markup_findings(text):
    """The findings that need only the page's markup."""
    parser = _Markup()
    parser.feed(text)
    found = []
    for ident, count in sorted(parser.ids.items()):
        if count > 1:
            found.append("id %r is used %d times; a marker or link that names it reaches only the first" % (ident, count))
    for figure, caption in parser.captions:
        m = GATHERED.search(" ".join(caption.split()))
        if m:
            found.append("%s: the caption ends in a gathered list %r; cite each sentence's passages right after it" % (figure or "a figure", m.group(1)))
    return found


def _right(b):
    return b["x"] + b["w"]


def _bottom(b):
    return b["y"] + b["h"]


def _contains(outer, x, y):
    return outer["x"] <= x <= _right(outer) and outer["y"] <= y <= _bottom(outer)


def _excess(inner, outer):
    """How far inner runs past outer on each side, as {side: pixels}, keeping only what exceeds the tolerance."""
    sides = {
        "left": outer["x"] - inner["x"],
        "right": _right(inner) - _right(outer),
        "top": outer["y"] - inner["y"],
        "bottom": _bottom(inner) - _bottom(outer),
    }
    return {side: d for side, d in sides.items() if d > TOLERANCE}


def _overlap(a, b):
    w = min(_right(a), _right(b)) - max(a["x"], b["x"])
    h = min(_bottom(a), _bottom(b)) - max(a["y"], b["y"])
    return w > OVERLAP_MIN and h > OVERLAP_MIN


def _label(t):
    text = t["text"]
    return repr(text if len(text) <= 40 else text[:39] + "...")


def _describe(excess):
    return ", ".join("%s %.0f px" % (side, d) for side, d in sorted(excess.items()))


def _over_white(colour, opacity=1.0):
    """The colour as it shows laid over white, with its own alpha and the element's opacity both applied."""
    r, g, b, alpha = colour
    a = alpha * opacity
    return [c * a + 255 * (1 - a) for c in (r, g, b)]


def _chroma(rgb):
    return max(rgb) - min(rgb)


def _untinted(box):
    """The fill's chroma when a box has a coloured solid border and almost no colour behind it, otherwise None."""
    if box["dashed"] or box["fill"] is None or box["stroke"] is None:
        return None
    if box["w"] <= BOX_MIN_W or box["h"] <= BOX_MIN_H:
        return None
    if _chroma(_over_white(box["stroke"])) <= BORDER_CHROMA_MIN:
        return None
    tint = _chroma(_over_white(box["fill"], box["fillOpacity"]))
    return tint if tint <= TINT_CHROMA_MAX else None


def geometry_findings(measured):
    """The findings from what the browser measured: clipped text, text past its box, overlapping labels, a page too wide,
    and coloured borders with no tint behind them."""
    found = []
    page = measured.get("page") or {}
    if page.get("scrollWidth", 0) > page.get("clientWidth", 0) + TOLERANCE:
        found.append("the page is %d px wide in a %d px window, so it scrolls sideways" % (page["scrollWidth"], page["clientWidth"]))
    for i, svg in enumerate(measured.get("svgs") or []):
        where = svg.get("figure") or "svg %d" % (i + 1)
        frame = svg["frame"]
        texts = svg.get("texts") or []
        for t in texts:
            clipped = _excess(t, frame)
            if clipped:
                found.append("%s: %s is cut off by the figure's edge (%s)" % (where, _label(t), _describe(clipped)))
                continue
            # the box a label sits in is the smallest rectangle holding its centre;
            # a label whose centre lies in no rectangle is free-standing and has no box to run past
            cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
            holders = [r for r in svg.get("rects") or [] if _contains(r, cx, cy)]
            if holders:
                holder = min(holders, key=lambda r: r["w"] * r["h"])
                past = _excess(t, holder)
                if past:
                    found.append("%s: %s runs past the box it sits in (%s)" % (where, _label(t), _describe(past)))
        for a_index, a in enumerate(texts):
            for b in texts[a_index + 1:]:
                if _overlap(a, b):
                    found.append("%s: %s and %s are drawn over each other" % (where, _label(a), _label(b)))
        for b in svg.get("boxes") or []:
            tint = _untinted(b)
            if tint is not None:
                found.append("%s: the box at %.0f,%.0f has a #%02x%02x%02x border but no tint behind it (fill chroma %.0f); give it a pale fill of the border's colour"
                             % (where, b["x"] - frame["x"], b["y"] - frame["y"], b["stroke"][0], b["stroke"][1], b["stroke"][2], tint))
    return found


def find_browsers(given=None):
    """Every Chromium-family browser on this machine, in the order to try them, or the given one alone."""
    if given:
        return [given] if os.path.isfile(given) else []
    found = [path for path in BROWSER_PATHS if os.path.isfile(path)]
    for name in BROWSER_NAMES:
        path = shutil.which(name)
        if path and path not in found:
            found.append(path)
    return found


def find_browser(given=None):
    browsers = find_browsers(given)
    return browsers[0] if browsers else None


def _inject(page_text, snippet):
    """The page with snippet placed just before </head>, or at the very start when the page has no head."""
    injected = re.sub(r"</head>", lambda _: snippet + "</head>", page_text, count=1, flags=re.IGNORECASE)
    return injected if injected != page_text else snippet + page_text


def measure(page_text, browser):
    """Render a copy of the page with the measuring script injected and read back what it measured."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "page.html"
        copy.write_text(_inject(page_text, MEASURE_JS), encoding="utf-8", newline="\n")
        args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=3000",
                "--window-size=%d,1000" % WIDTH, "--user-data-dir=%s" % (pathlib.Path(tmp) / "profile"),
                "--dump-dom", copy.resolve().as_uri()]
        proc = subprocess.run(args, capture_output=True, timeout=120)
    dom = proc.stdout.decode("utf-8", errors="replace")
    m = re.search(r'<pre id="__check_page">(.*?)</pre>', dom, re.DOTALL)
    if not m:
        raise RuntimeError("the browser returned no measurements (exit %d)" % proc.returncode)
    return json.loads(html.unescape(m.group(1)))


def measure_with_any(page_text, browsers):
    """What the first browser able to render the page measured, with that browser, or (None, None)."""
    for browser in browsers:
        try:
            return measure(page_text, browser), browser
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            continue
    return None, None


def shot_offsets(height):
    """The scroll offsets whose windows together cover a page of this height."""
    return list(range(0, max(int(height), 1), SHOT_HEIGHT))


def shot_args(browser, page_uri, png, profile, dark):
    args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=3000",
            "--window-size=%d,%d" % (WIDTH, SHOT_HEIGHT), "--user-data-dir=%s" % profile, "--screenshot=%s" % png]
    if dark:
        args += ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
    return args + [page_uri]


def _shoot(page_text, height, browser, out):
    """Every screenshot this browser writes for the page, or None as soon as one is missing."""
    written = []
    with tempfile.TemporaryDirectory() as tmp:
        # a profile of its own, since a headless call can hand off to the user's running browser and write nothing
        profile = pathlib.Path(tmp) / "profile"
        for offset in shot_offsets(height):
            copy = pathlib.Path(tmp) / ("page-%d.html" % offset)
            # the page is moved up instead of scrolled, since a headless screenshot always shows the top of the page
            shifted = _inject(page_text, "<style>body{transform:translateY(-%dpx)}</style>" % offset)
            copy.write_text(shifted, encoding="utf-8", newline="\n")
            for mode in ("light", "dark"):
                png = (out / ("%s_%05d.png" % (mode, offset))).resolve()
                if png.exists():
                    png.unlink()
                subprocess.run(shot_args(browser, copy.resolve().as_uri(), png, profile, mode == "dark"),
                               capture_output=True, timeout=120)
                if not png.is_file():
                    return None
                written.append(png)
    return written


def screenshots(page_text, height, browsers, out_dir):
    """Light and dark screenshots covering the page from the first browser that writes them all, with that browser."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for browser in browsers:
        written = _shoot(page_text, height, browser, out)
        if written:
            return written, browser
    return [], None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("page")
    parser.add_argument("--browser", help="a Chromium-family browser to measure with, when it is not in a usual place")
    parser.add_argument("--shots", metavar="FOLDER", help="also write light and dark screenshots of the whole page into FOLDER")
    args = parser.parse_args(argv)
    # labels are in the page's language, and a Windows console cannot print most of them
    sys.stdout.reconfigure(errors="backslashreplace")

    text = pathlib.Path(args.page).read_text(encoding="utf-8")
    found = markup_findings(text)
    browsers = find_browsers(args.browser)
    measured, _ = measure_with_any(text, browsers)
    if measured:
        found += geometry_findings(measured)
    for line in found:
        print(line)
    if not browsers:
        print("no Chromium-family browser found, so nothing was measured; only the markup was checked")
        return EXIT_NO_BROWSER
    if not measured:
        print("no browser returned measurements (tried %s), so only the markup was checked" % ", ".join(browsers))
        return EXIT_NO_BROWSER
    print("%d finding(s)" % len(found))
    if args.shots:
        pngs, used = screenshots(text, measured["page"].get("scrollHeight", SHOT_HEIGHT), browsers, args.shots)
        if not pngs:
            print("no browser wrote a screenshot (tried %s), so the page was not checked by eye" % ", ".join(browsers))
            return EXIT_FOUND if found else EXIT_NO_BROWSER
        print("%d screenshot(s) by %s, top of the page first:" % (len(pngs), used))
        for png in pngs:
            print(png)
    return EXIT_FOUND if found else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
