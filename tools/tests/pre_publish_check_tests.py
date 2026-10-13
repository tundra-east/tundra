#!/usr/bin/env python3
"""Known-answer cases for tools/pre_publish_check.py, plus the mutation test.

This repo's own rule is that a check which has only ever run against good input
has not been tested, and that deleting any one check must turn the suite red.
Both are enforced here: every gate owns a fixture that plants exactly the defect
it exists to catch, and the suite asserts that **no other gate** reports that
fixture. Sole detection is what makes the mutation test real -- remove a gate and
its fixture becomes invisible, so the suite goes red.

Run: python tools/tests/pre_publish_check_tests.py
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pre_publish_check as check  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}{('  -- ' + detail) if detail and not condition else ''}")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def build_fixture(root, *, skill_name="demo-skill", description="A demo skill.",
                  plugin_version="1.0.0",
                  manifest_json=None, frontmatter=None, extra_doc=None, files=None):
    """A minimal but valid plugin repository, with one seam per defect to plant."""
    for rel, text in (files or {}).items():
        write(os.path.join(root, rel), text)
    plugin = "demo-plugin"
    write(os.path.join(root, "plugins", plugin, ".claude-plugin", "plugin.json"),
          manifest_json if manifest_json is not None
          else '{"name": "%s", "version": "%s"}' % (plugin, plugin_version))
    fm = frontmatter if frontmatter is not None else (
        "---\nname: %s\ndescription: %s\n---\n\n# Demo\n\nBody.\n" % (skill_name, description))
    write(os.path.join(root, "plugins", plugin, "skills", "demo-skill", "SKILL.md"), fm)
    if extra_doc:
        write(os.path.join(root, "plugins", plugin, "docs", "note.md"), extra_doc)
    return root


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=selfcheck",
                           "-c", "user.email=selfcheck@noreply.invalid", *args],
                          cwd=root, capture_output=True, text=True)


def init_git_history(root):
    """A repo whose refs/remotes/origin/main points at the initial commit.

    No remote is involved -- the ref is written directly, which is enough for the
    gate's own comparison and keeps the fixture offline. Returns False when git
    cannot run, so the suite degrades instead of failing on a machine without it."""
    if git(root, "init", "-q").returncode != 0:
        return False
    git(root, "add", "-A")
    if git(root, "commit", "-q", "-m", "initial").returncode != 0:
        return False
    sha = git(root, "rev-parse", "HEAD").stdout.strip()
    git(root, "update-ref", "refs/remotes/origin/main", sha)
    return True


def bump(root, version):
    """Change the plugin, optionally bumping its version, and commit."""
    skill = os.path.join(root, "plugins", "demo-plugin", "skills", "demo-skill", "SKILL.md")
    write(skill, open(skill, encoding="utf-8").read() + "\nAnother line.\n")
    if version:
        write(os.path.join(root, "plugins", "demo-plugin", ".claude-plugin", "plugin.json"),
              '{"name": "demo-plugin", "version": "%s"}' % version)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")


def gates_reporting(root):
    """Names of the gates that found something, with the version gate given
    explicit inputs so a fixture needs no git remote."""
    results = check.run(root, [n for n in check.GATES if n != "versions"])
    return {name for name, failures in results.items() if failures}


def captured(fn, *args):
    """Run fn and return (its result, everything it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


# a root README with one of everything the translation gate compares,
# and its translation built from parts so each case can break exactly one of them.
README_SOURCE = (
    "# Demo\n\n" + check.SWITCHER + "\n\n"
    "Read [the guide](docs/guide.md), then [install](#install).\n\n"
    "## Install\n\n```\n/plugin install demo@market\n```\n\n"
    "## Notes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
)
TRANSLATED = {
    "switcher": check.SWITCHER,
    "links": "閱讀 [指南](docs/guide.md)，然後 [安裝](#install)。",
    "install": '<a id="install"></a>\n## 安裝',
    "code": "```\n/plugin install demo@market\n```",
    "notes": "## 備註",
    "table": "| a | b |\n|---|---|\n| 1 | 2 |",
}


def translation(source=README_SOURCE, **override):
    parts = {**TRANSLATED, **override}
    mark = "<!-- translated from README.md, source sha256 %s; see CLAUDE.md -->" % check.source_digest(source)
    return "\n\n".join([mark + "\n# Demo", parts["switcher"], parts["links"], parts["install"],
                        parts["code"], parts["notes"], parts["table"]]) + "\n"


# the root README with a mermaid diagram after its table, and that diagram with translated labels
MERMAID_SOURCE = README_SOURCE + "\n```mermaid\nflowchart TD\n  A[Start] --> B[Done]\n```\n"
MERMAID_TRANSLATED = "```mermaid\nflowchart TD\n  A[開始] --> B[完成]\n```"


def mermaid_translation():
    return translation(source=MERMAID_SOURCE, table=TRANSLATED["table"] + "\n\n" + MERMAID_TRANSLATED)


def translated_repo(source=README_SOURCE, zh_tw=None, zh_cn=None):
    files = {"README.md": source, "README.zh-TW.md": zh_tw if zh_tw is not None else translation(source)}
    if zh_cn is not False:
        files["README.zh-CN.md"] = zh_cn if zh_cn is not None else translation(source)
    return {"files": files}


# a README below the root, translated too, linking back up to the root README
NESTED = "plugins/demo-plugin/"
NESTED_SOURCE = "# Demo plugin\n\n" + check.SWITCHER + "\n\nSee [the overview](../../README.md).\n"


def nested_translation(lang, source=NESTED_SOURCE, root_link=None):
    mark = "<!-- translated from README.md, source sha256 %s; see CLAUDE.md -->" % check.source_digest(source)
    link = root_link or "../../README.%s.md" % lang
    return mark + "\n# Demo plugin\n\n" + check.SWITCHER + "\n\n見 [概覽](%s)。\n" % link


def nested_repo(zh_tw=None):
    files = translated_repo()["files"]
    files[NESTED + "README.md"] = NESTED_SOURCE
    files[NESTED + "README.zh-TW.md"] = zh_tw if zh_tw is not None else nested_translation("zh-TW")
    files[NESTED + "README.zh-CN.md"] = nested_translation("zh-CN")
    return {"files": files}


def case(tmp, label, expected_gate, **kwargs):
    root = os.path.join(tmp, label)
    build_fixture(root, **kwargs)
    reporting = gates_reporting(root)
    if expected_gate is None:
        ok(f"{label}: clean fixture is green", reporting == set(), f"reported {sorted(reporting)}")
        return
    ok(f"{label}: '{expected_gate}' catches it", expected_gate in reporting,
       f"reported {sorted(reporting)}")
    others = reporting - {expected_gate}
    ok(f"{label}: no other gate catches it (sole detector)", others == set(),
       f"also reported {sorted(others)}")


def main():
    tmp = tempfile.mkdtemp(prefix="itpr-selfcheck-")
    try:
        print("Known-answer cases (each must make exactly one gate red):")
        case(tmp, "clean", None)

        # pins the placeholder exemptions: prose that writes a bare host or an
        # ssh remote is not a leak, and the real tree contains all three shapes.
        case(tmp, "placeholders-are-not-leaks", None,
             extra_doc="Use `https://host/path`, or `https://org`, "
                       "or clone from git@ssh.dev.azure.com:v3/x/y/z.\n")

        case(tmp, "bad-json", "json",
             manifest_json='{"name": "demo-plugin", "version": "1.0.0",}')

        case(tmp, "name-mismatch", "frontmatter",
             frontmatter="---\nname: not-the-directory\ndescription: A demo skill.\n---\n\n# Demo\n")

        case(tmp, "no-description", "frontmatter",
             frontmatter="---\nname: demo-skill\n---\n\n# Demo\n")

        case(tmp, "long-description", "descriptions",
             description="word " * 250)

        # the leak tokens are assembled at runtime rather than written as literals,
        # because this file is itself scanned by the leaks gate and a literal here
        # would fail the real repo -- which is also a live demonstration that it works.
        case(tmp, "leak-host", "leaks",
             extra_doc="See https://" + "tickets.internal" + ".invalid/board for detail.\n")

        case(tmp, "leak-ticket", "leaks",
             extra_doc="Fixes " + "PROJ" + "-" + "4821" + " in the billing path.\n")

        case(tmp, "leak-address", "leaks",
             extra_doc="Ask " + "jane.doe" + "@" + "billing.invalid" + " for access.\n")

        # a subdomain of an allowed domain is another organisation's mail,
        # so the domain must match exactly rather than by suffix.
        case(tmp, "leak-address-at-allowed-subdomain", "leaks",
             extra_doc="Ask " + "x" + "@" + "sub.example.com" + " for access.\n")

        # mail domains are case-insensitive, so an allowed domain in any case is still a fixture.
        case(tmp, "allowed-address-in-mixed-case-is-not-a-leak", None,
             extra_doc="Commit as someone@EXAMPLE.Com in the fixture.\n")

        case(tmp, "noreply-address-in-mixed-case-is-not-a-leak", None,
             extra_doc="Commits by bot@users.NoReply.github.com are generated.\n")

        # three of the allowlisted hosts and one allowlisted ticket prefix,
        # so removing any one of the entries this case uses turns it red.
        case(tmp, "allowlisted-hosts-and-prefixes-are-not-leaks", None,
             extra_doc="See https://dev.azure.com/acme, https://acme.visualstudio.com/repo "
                       "and https://www.w3.org/2000/svg, as in ABC-123.\n")

        # each translation defect below breaks one thing the gate compares, and only that one,
        # so deleting any single comparison from the gate leaves its case unreported.
        case(tmp, "translations-current", None, **translated_repo())
        # a Windows checkout turns the README's line endings into CRLF while CI reads LF,
        # and both must agree that a translation made from either is current
        crlf = translated_repo()
        crlf["files"]["README.md"] = README_SOURCE.replace("\n", "\r\n")
        case(tmp, "translations-current-on-crlf-checkout", None, **crlf)
        case(tmp, "translation-stale", "translations",
             **translated_repo(zh_tw=translation(source=README_SOURCE + "\nA new paragraph.\n")))
        case(tmp, "translation-missing", "translations", **translated_repo(zh_cn=False))
        case(tmp, "translation-code-changed", "translations",
             **translated_repo(zh_tw=translation(code="```\n/plugin install 示範@market\n```")))
        # a mermaid diagram's labels are prose, so translating them keeps the translation current,
        # while dropping the diagram changes what the reader sees.
        case(tmp, "translation-mermaid-labels-translated", None,
             **translated_repo(source=MERMAID_SOURCE, zh_tw=mermaid_translation(),
                               zh_cn=mermaid_translation()))
        case(tmp, "translation-mermaid-dropped", "translations",
             **translated_repo(source=MERMAID_SOURCE, zh_tw=translation(source=MERMAID_SOURCE),
                               zh_cn=mermaid_translation()))
        case(tmp, "translation-link-dropped", "translations",
             **translated_repo(zh_tw=translation(links="閱讀指南，然後 [安裝](#install)。")))
        case(tmp, "translation-heading-dropped", "translations",
             **translated_repo(zh_tw=translation(notes="備註")))
        case(tmp, "translation-table-row-dropped", "translations",
             **translated_repo(zh_tw=translation(table="| a | b |\n|---|---|")))
        case(tmp, "translation-anchor-broken", "translations",
             **translated_repo(zh_tw=translation(install="## 安裝")))
        # the switcher's links stay out of the link comparison, so a translation that drops the
        # whole line is visible to the switcher comparison alone
        case(tmp, "translation-switcher-missing", "translations",
             **translated_repo(zh_tw=translation(switcher="")))
        # the source's own broken anchor, with translations that carry a matching explicit one
        broken_source = README_SOURCE + "\nSee [later](#later).\n"
        later = translation(source=broken_source, table="| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                            '<a id="later"></a>見 [稍後](#later)。')
        case(tmp, "readme-anchor-broken", "translations",
             **translated_repo(source=broken_source, zh_tw=later, zh_cn=later))
        # a README below the root is held to the same rules, and a translated link into another
        # translated README counts as the English link it stands for
        case(tmp, "nested-translations-current", None, **nested_repo())
        case(tmp, "nested-translation-stale", "translations",
             **nested_repo(zh_tw=nested_translation("zh-TW", source=NESTED_SOURCE + "\nMore.\n")))
        # the English root README, linked from a translation while the root has one in that language
        case(tmp, "translation-links-english-readme", "translations",
             **nested_repo(zh_tw=nested_translation("zh-TW", root_link="../../README.md")))

        print("\nVersion gate (through the registry, against a real git history):")
        # routed through GATES rather than the function, because the first version
        # of this suite called check_version_bumps directly and the mutation test
        # then found that disabling the registered gate left the suite green.
        gate = check.GATES["versions"]
        root = build_fixture(os.path.join(tmp, "versions"))
        stuck = gate(root, changed={"demo-plugin": True}, published={"demo-plugin": "1.0.0"})
        ok("changed plugin at an unchanged version is caught", len(stuck) == 1)
        bumped = gate(root, changed={"demo-plugin": True}, published={"demo-plugin": "0.9.0"})
        ok("changed plugin that was bumped passes", bumped == [])
        untouched = gate(root, changed={"demo-plugin": False}, published={"demo-plugin": "1.0.0"})
        ok("unchanged plugin at the same version passes", untouched == [])

        git_root = build_fixture(os.path.join(tmp, "versions-git"))
        if init_git_history(git_root):
            bump(git_root, version=None)
            ok("real history: changed but unbumped is caught", gate(git_root) != [])
            bump(git_root, version="1.1.0")
            ok("real history: changed and bumped passes", gate(git_root) == [])
        else:
            ok("real history: git unavailable, gate skipped rather than failed",
               gate(git_root) == [])

        # a README translation inside a plugin is read by nothing at runtime, so adding one alone
        # must not demand a release, while the same plugin's own README still does
        translated_root = build_fixture(os.path.join(tmp, "versions-translation"))
        if init_git_history(translated_root):
            write(os.path.join(translated_root, "plugins", "demo-plugin", "README.zh-TW.md"), "# 示範\n")
            git(translated_root, "add", "-A")
            git(translated_root, "commit", "-q", "-m", "translation")
            ok("real history: a translation alone needs no bump", gate(translated_root) == [])
            write(os.path.join(translated_root, "plugins", "demo-plugin", "README.md"), "# Demo\n")
            git(translated_root, "add", "-A")
            git(translated_root, "commit", "-q", "-m", "readme")
            ok("real history: the plugin's own README still needs one", gate(translated_root) != [])

        print("\nA declined gate must say so (a silent skip reads as a pass):")
        os.environ["ITPR_PUBLISHED_REF"] = "refs/heads/deliberately-absent"
        try:
            check.run(git_root, ["versions"])
            ok("an unresolvable published ref is announced", len(check.NOTES) == 1,
               f"notes were {check.NOTES}")
            ok("the note names the ref it could not resolve",
               any("deliberately-absent" in n for n in check.NOTES))
        finally:
            del os.environ["ITPR_PUBLISHED_REF"]
        check.run(git_root, ["versions"])
        ok("a resolvable ref produces no note", check.NOTES == [], f"notes were {check.NOTES}")

        print("\nmain takes its root from argv[1], else the working directory:")
        defect_root = build_fixture(os.path.join(tmp, "main-defect"),
                                    frontmatter="---\nname: not-the-directory\n"
                                                "description: A demo skill.\n---\n\n# Demo\n")
        clean_root = build_fixture(os.path.join(tmp, "main-clean"))
        prog = "pre_publish_check.py"
        # with argv[1], the working directory is the other fixture,
        # so a wrong choice of root flips the result.
        # without it, the working directory is the intended root,
        # and the pair still discriminates because a wrong fallback gives both the same result.
        invocations = (
            ("argv[1] names a defect fixture", clean_root, [prog, defect_root], 1),
            ("argv[1] names a clean fixture", defect_root, [prog, clean_root], 0),
            ("no argv[1], from a defect fixture", defect_root, [prog], 1),
            ("no argv[1], from a clean fixture", clean_root, [prog], 0),
        )
        cwd = os.getcwd()
        for label, workdir, argv, expected_rc in invocations:
            os.chdir(workdir)
            try:
                rc, printed = captured(check.main, argv)
            finally:
                os.chdir(cwd)
            ok(f"{label}: returns {expected_rc}", rc == expected_rc, f"returned {rc}")
            if expected_rc:
                ok(f"{label}: prints the failing gate",
                   "[FAIL] frontmatter" in printed.splitlines(), f"printed {printed!r}")
            else:
                ok(f"{label}: prints no failing gate", "[FAIL]" not in printed, f"printed {printed!r}")

        print("\nGate coverage:")
        covered = {"json", "frontmatter", "descriptions", "leaks", "versions", "translations"}
        ok("every gate owns at least one known-answer case",
           covered == set(check.GATES), f"uncovered: {sorted(set(check.GATES) - covered)}")

        print("\nReal repository:")
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        results = check.run(repo)
        for name, failures in results.items():
            ok(f"repo passes '{name}'", failures == [], "; ".join(failures[:3]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
