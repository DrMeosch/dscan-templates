#!/usr/bin/env python3
"""CI: fail a pull request whose body has no valid "## Changelog" section.

The section holds user-facing lines, each starting with Added:, Changed:, Fixed:, Removed: or Security: (an optional
list marker is fine), or the single word "none" for an internal-only change. The "one or two lines" guidance is not
enforced. HTML comments, fenced code blocks and blank lines do not count as content; inline code is content. The
section runs to the next heading of level 1 or 2.

The body is read from the file named by the first argument ("-" is stdin); without an argument from the pull
request event file named by GITHUB_EVENT_PATH, and without that from stdin. Exits 1 with an explanation when the
section is missing or invalid. A pull request read from the event file that is exempt (see is_exempt()) passes
without a section.

scripts/changelog_feed.py imports parse_changelog() from here.
Python 3 standard library only, so the file works unchanged in any repository.
"""

import json
import os
import re
import sys

TYPES = ("Added", "Changed", "Fixed", "Removed", "Security")

HEADING = re.compile(r"^(#{1,2})[ \t]+(.*?)[ \t]*#*[ \t]*$")
ENTRY = re.compile(
    r"^(?:[-*+][ \t]+)?(%s):[ \t]*(\S.*)$" % "|".join(TYPES), re.IGNORECASE
)
NONE_LINE = re.compile(r"^(?:[-*+][ \t]+)?none\.?$", re.IGNORECASE)
# Fenced code blocks and HTML comments are dropped, inline code spans are kept verbatim. One left-to-right pass, so
# whichever construct opens first wins: a "<!--" inside a code span or a fence is not a comment, and a backtick
# inside a comment or a fence is not a code span. An unclosed fence or comment runs to the end of the body.
STRIP = re.compile(
    r"(?P<fence>^ {0,3}(?P<f>`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=f)[`~]*[ \t]*$|\Z))"
    r"|(?P<code>(?<!`)(?P<t>`+)(?!`)(?:(?!\n[ \t]*\n).)*?(?<!`)(?P=t)(?!`))"
    r"|(?P<comment><!--.*?(?:-->|\Z))",
    re.DOTALL | re.MULTILINE,
)

# Statuses of parse_changelog()
OK, NONE, MISSING, EMPTY, INVALID = "ok", "none", "missing", "empty", "invalid"

FORMAT_HELP = """\
The pull request body needs a "## Changelog" section with one or two user-facing lines, each starting with
one of Added:, Changed:, Fixed:, Removed:, Security:

## Changelog
Fixed: a scan no longer stays stuck after a worker restart.

For a change nobody outside the project would notice (refactor, tests, CI, docs), write the single word none:

## Changelog
none

HTML comments and blank lines do not count as content.

Edit the pull request body to fix this: re-running the job re-reads the old body, an edit starts a new run."""


def parse_changelog(body):
    """Return (status, entries, errors).

    status is OK (entries holds (type, text) pairs), NONE, MISSING (no Changelog heading), EMPTY or INVALID.
    errors lists the reasons for MISSING, EMPTY and INVALID.
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = STRIP.sub(lambda m: m.group("code") or "", text).split("\n")

    start = None
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and len(m.group(1)) == 2 and m.group(2).lower() == "changelog":
            start = i + 1
            break
    if start is None:
        return MISSING, [], ['no "## Changelog" heading found']

    content = []
    for line in lines[start:]:
        if HEADING.match(line):
            break
        if line.strip():
            content.append(line.strip())

    if not content:
        return EMPTY, [], ['the "## Changelog" section is empty']
    if len(content) == 1 and NONE_LINE.match(content[0]):
        return NONE, [], []

    entries, errors = [], []
    for line in content:
        m = ENTRY.match(line)
        if m:
            entries.append((m.group(1).capitalize(), m.group(2).strip()))
        else:
            errors.append("not a changelog line: %s" % line)
    if errors:
        return INVALID, [], errors
    return OK, entries, []


def is_sync_pr(head_ref, base_ref, same_repo=True):
    """A sync or autosync pull request: the default branch merged back (main or master into another branch),
    sync/*, or the sync-nuclei-templates-* autosync. It carries no Changelog section. A pull request from a fork
    (same_repo False) is never a sync pull request, whatever its branch is called."""
    return same_repo and (
        (head_ref in ("main", "master") and base_ref != head_ref)
        or head_ref.startswith(("sync/", "sync-nuclei-templates-"))
    )


def is_exempt(author_type, head_ref, base_ref, same_repo=True):
    """Bot pull requests and sync pull requests need no Changelog section. Release and hotfix pull requests need one."""
    return author_type == "Bot" or is_sync_pr(head_ref, base_ref, same_repo)


def read_input(argv, environ):
    """Return (body, pull request dict or None); the dict only when read from the event file."""
    if len(argv) > 1 and argv[1] != "-":
        with open(argv[1], encoding="utf-8") as f:
            return f.read(), None
    if len(argv) <= 1 and environ.get("GITHUB_EVENT_PATH"):
        with open(environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            pr = json.load(f).get("pull_request") or {}
        return pr.get("body") or "", pr
    return sys.stdin.read(), None


def main(argv=None, environ=None):
    body, pr = read_input(argv or sys.argv, os.environ if environ is None else environ)
    pr = pr or {}
    head, base = pr.get("head") or {}, pr.get("base") or {}
    if pr and is_exempt(
        (pr.get("user") or {}).get("type", ""),
        head.get("ref", ""),
        base.get("ref", ""),
        (head.get("repo") or {}).get("full_name")
        == (base.get("repo") or {}).get("full_name"),
    ):
        print("Exempt pull request (bot or sync): no Changelog section needed.")
        return 0
    status, entries, errors = parse_changelog(body)
    if status in (OK, NONE):
        print(
            "Changelog section ok (%s)."
            % ("none" if status == NONE else "%d line(s)" % len(entries))
        )
        return 0
    print("::error::Invalid Changelog section: %s" % "; ".join(errors))
    print(FORMAT_HELP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
