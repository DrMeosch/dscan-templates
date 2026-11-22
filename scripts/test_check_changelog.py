"""Unit tests for check_changelog.py. Run: PYTHONPATH=scripts python3 -m unittest test_check_changelog"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
import unittest.mock

import check_changelog as cl


def status(body):
    return cl.parse_changelog(body)[0]


class ParseChangelogTest(unittest.TestCase):
    def test_missing_section(self):
        self.assertEqual(status("## Summary\nstuff"), cl.MISSING)
        self.assertEqual(status(""), cl.MISSING)
        self.assertEqual(status(None), cl.MISSING)

    def test_heading_must_be_level_two(self):
        self.assertEqual(status("# Changelog\nnone"), cl.MISSING)
        self.assertEqual(status("### Changelog\nnone"), cl.MISSING)

    def test_heading_is_case_insensitive(self):
        self.assertEqual(status("## changelog\nnone"), cl.NONE)
        self.assertEqual(status("##  CHANGELOG  \nnone"), cl.NONE)

    def test_empty_section(self):
        self.assertEqual(status("## Changelog\n\n\n"), cl.EMPTY)
        self.assertEqual(status("## Changelog"), cl.EMPTY)

    def test_comment_only_section(self):
        self.assertEqual(status("## Changelog\n<!-- Fixed: example -->\n"), cl.EMPTY)
        self.assertEqual(
            status("## Changelog\n<!--\nFixed: example\nnone\n-->\n"), cl.EMPTY
        )
        self.assertEqual(
            status("## Changelog\n<!-- unterminated\nFixed: example"), cl.EMPTY
        )

    def test_comment_marker_inside_inline_code_is_not_a_comment(self):
        body = (
            "## Changelog\nFixed: templates with a `<!--` comment no longer break parsing\n"
            "Added: second entry\n\n## Validation\nok"
        )
        self.assertEqual(
            cl.parse_changelog(body),
            (
                cl.OK,
                [
                    (
                        "Fixed",
                        "templates with a `<!--` comment no longer break parsing",
                    ),
                    ("Added", "second entry"),
                ],
                [],
            ),
        )
        self.assertEqual(
            status(
                "## Summary\nThe template uses `<!--` comments.\n\n## Changelog\nnone"
            ),
            cl.NONE,
        )
        self.assertEqual(
            status("```\n<!--\n```\n\n## Changelog\nnone"),
            cl.NONE,
        )

    def test_whichever_construct_opens_first_wins(self):
        # a backtick inside a comment or a fence does not start a code span
        self.assertEqual(
            status("<!-- a ` b -->\n## Changelog\nnone <!-- ` -->"), cl.NONE
        )
        self.assertEqual(
            status("```\n`\n```\n## Changelog\nnone\n```\n`\n```"), cl.NONE
        )
        # a comment opened inside a code span stays code; the rest of the line is content
        self.assertEqual(
            cl.parse_changelog("## Changelog\nFixed: `<!--` and <!-- hidden --> text")[
                1
            ],
            [("Fixed", "`<!--` and  text")],
        )
        # an unclosed comment or fence runs to the end of the body
        self.assertEqual(status("## Changelog\nnone\n<!-- unclosed\n## Other"), cl.NONE)
        self.assertEqual(status("```\n## Changelog\nnone"), cl.MISSING)

    def test_pathological_backtick_runs_parse_fast(self):
        # increasing runs of backticks used to make every position rescan to the paragraph end (about a minute)
        for sep in (" ", ""):  # the unspaced body is one unclosed fence
            body = sep.join("`" * i for i in range(1, 360))[:65000]
            started = time.monotonic()
            status(body + "\n\n## Changelog\nnone")
            self.assertLess(time.monotonic() - started, 5)

    def test_comment_hides_a_heading(self):
        self.assertEqual(status("<!--\n## Changelog\nnone\n-->"), cl.MISSING)

    def test_fenced_code_block_hides_a_heading(self):
        fenced = (
            "## Summary\ns\n\n```text\n## Changelog\nFixed: x\n\nnot an entry\n```\n"
        )
        self.assertEqual(status(fenced), cl.MISSING)
        self.assertEqual(status(fenced.replace("```", "~~~")), cl.MISSING)
        self.assertEqual(status(fenced + "\n## Changelog\nnone\n"), cl.NONE)

    def test_fenced_code_block_inside_the_section_is_ignored(self):
        body = "## Changelog\nFixed: x\n```\nlog output\n```\n"
        self.assertEqual(cl.parse_changelog(body), (cl.OK, [("Fixed", "x")], []))

    def test_none(self):
        self.assertEqual(cl.parse_changelog("## Changelog\nnone"), (cl.NONE, [], []))
        self.assertEqual(status("## Changelog\n\n  None \n\n"), cl.NONE)
        self.assertEqual(status("## Changelog\n<!-- c -->\nNONE"), cl.NONE)

    def test_none_variants(self):
        for line in (
            "none",
            "None",
            "NONE",
            "none.",
            "None.",
            "  None.  ",
            "- none",
            "* None.",
            "- none.",
        ):
            self.assertEqual(
                cl.parse_changelog("## Changelog\n" + line), (cl.NONE, [], []), line
            )
            self.assertEqual(
                status("## Changelog\r\n\r\n%s\r\n\r\n## Next\r\nx" % line),
                cl.NONE,
                line,
            )

    def test_none_lookalikes_are_invalid(self):
        for line in (
            "none..",
            "nonesuch",
            "no",
            "n/a",
            "- none - tidy",
            "-none",
            "none!",
            "- ",
        ):
            self.assertNotEqual(status("## Changelog\n" + line), cl.NONE, line)

    def test_none_with_other_lines_is_invalid(self):
        self.assertEqual(status("## Changelog\nnone\nFixed: a thing"), cl.INVALID)
        self.assertEqual(status("## Changelog\nnone needed"), cl.INVALID)

    def test_single_line(self):
        self.assertEqual(
            cl.parse_changelog("## Changelog\nFixed: scans no longer stall."),
            (cl.OK, [("Fixed", "scans no longer stall.")], []),
        )

    def test_two_lines_and_bullets(self):
        body = "## Changelog\n- Added: an endpoint\n* Security: tokens are hashed\n"
        self.assertEqual(
            cl.parse_changelog(body)[1],
            [("Added", "an endpoint"), ("Security", "tokens are hashed")],
        )

    def test_every_type(self):
        for kind in cl.TYPES:
            self.assertEqual(
                cl.parse_changelog("## Changelog\n%s: x" % kind)[1], [(kind, "x")]
            )

    def test_prefix_is_case_insensitive_and_normalized(self):
        self.assertEqual(
            cl.parse_changelog("## Changelog\nfixed: x")[1], [("Fixed", "x")]
        )

    def test_bad_prefix(self):
        for line in (
            "Improved: x",
            "Fix: x",
            "Fixed x",
            "Fixed:",
            "Fixed:   ",
            "- Added:  ",
            "* Security:",
            "Just prose",
        ):
            self.assertEqual(status("## Changelog\n" + line), cl.INVALID, line)

    def test_one_bad_line_fails_the_section(self):
        self.assertEqual(status("## Changelog\nFixed: x\nnonsense"), cl.INVALID)

    def test_section_followed_by_other_headings(self):
        body = "## Summary\ns\n\n## Changelog\nAdded: a\n\n## Validation\nran tests\n\n# Top\nmore"
        self.assertEqual(cl.parse_changelog(body), (cl.OK, [("Added", "a")], []))

    def test_section_in_the_middle_is_empty_when_next_heading_follows(self):
        self.assertEqual(status("## Changelog\n## Validation\nFixed: x"), cl.EMPTY)

    def test_level_three_heading_stays_in_the_section(self):
        self.assertEqual(status("## Changelog\n### Details\nFixed: x"), cl.INVALID)

    def test_crlf_body(self):
        body = "## Summary\r\ns\r\n\r\n## Changelog\r\n<!-- c\r\n-->\r\nFixed: x\r\nAdded: y\r\n\r\n## Validation\r\nok\r\n"
        self.assertEqual(cl.parse_changelog(body)[1], [("Fixed", "x"), ("Added", "y")])
        self.assertEqual(status("## Changelog\r\nnone\r\n"), cl.NONE)

    def test_pull_request_template_is_not_valid_as_is(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", ".github", "pull_request_template.md"
        )
        with open(path, encoding="utf-8") as f:
            self.assertEqual(status(f.read()), cl.EMPTY)


class ExemptTest(unittest.TestCase):
    def test_sync_pull_requests_are_exempt(self):
        for head, base in (
            ("main", "develop"),
            ("master", "develop"),
            ("sync/main-to-develop", "develop"),
            ("sync-nuclei-templates-1a2b", "develop"),
            ("sync/x", "main"),
        ):
            self.assertTrue(cl.is_exempt("User", head, base), (head, base))

    def test_fork_pull_requests_are_never_sync_pull_requests(self):
        for head, base in (("main", "develop"), ("sync/x", "develop")):
            self.assertFalse(cl.is_exempt("User", head, base, same_repo=False))
        self.assertTrue(cl.is_exempt("Bot", "main", "develop", same_repo=False))

    def test_bots_are_exempt(self):
        self.assertTrue(cl.is_exempt("Bot", "dependabot/pip/x", "develop"))

    def test_other_pull_requests_are_not_exempt(self):
        for head, base in (
            ("feat/x", "develop"),
            ("develop", "main"),
            ("release/v1.0.0", "main"),
            ("hotfix/v1.0.1", "main"),
            ("fm/sync", "develop"),
            ("feat/sync/x", "develop"),
            ("mainline", "develop"),
            ("main", "main"),
        ):
            self.assertFalse(cl.is_exempt("User", head, base), (head, base))


class MainTest(unittest.TestCase):
    def run_main(self, argv, environ=None, stdin=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if stdin is not None:
                with unittest.mock.patch("sys.stdin", io.StringIO(stdin)):
                    code = cl.main(["check_changelog.py"] + argv, environ or {})
            else:
                code = cl.main(["check_changelog.py"] + argv, environ or {})
        return code, out.getvalue()

    def test_file_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = os.path.join(tmp, "good.md"), os.path.join(tmp, "bad.md")
            for path, text in ((good, "## Changelog\nnone"), (bad, "## Summary\nx")):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            self.assertEqual(self.run_main([good])[0], 0)
            code, out = self.run_main([bad])
            self.assertEqual(code, 1)
            self.assertIn("::error::", out)
            self.assertIn("Added:, Changed:, Fixed:, Removed:, Security:", out)

    def test_stdin(self):
        self.assertEqual(self.run_main(["-"], stdin="## Changelog\nAdded: x")[0], 0)
        self.assertEqual(self.run_main([], stdin="no section")[0], 1)

    def test_untouched_pull_request_template_fails_the_check(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", ".github", "pull_request_template.md"
        )
        code, out = self.run_main([path])
        self.assertEqual(code, 1)
        self.assertIn("empty", out)

    def test_exempt_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = os.path.join(tmp, "event.json")
            for user, head, base, expected in (
                ("User", "main", "develop", 0),
                ("User", "sync/x", "develop", 0),
                ("User", "sync-nuclei-templates-1", "develop", 0),
                ("Bot", "dependabot/x", "develop", 0),
                ("User", "feat/x", "develop", 1),
                ("User", "release/v1.0.0", "main", 1),
                ("User", "hotfix/v1.0.1", "main", 1),
            ):
                with open(event, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "pull_request": {
                                "body": "",
                                "user": {"type": user},
                                "head": {"ref": head},
                                "base": {"ref": base},
                            }
                        },
                        f,
                    )
                self.assertEqual(
                    self.run_main([], {"GITHUB_EVENT_PATH": event})[0],
                    expected,
                    (user, head, base),
                )

    def test_fork_event_is_not_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = os.path.join(tmp, "event.json")
            for head_repo, expected in (
                ("o/r", 0),
                ("someone/r", 1),
                (None, 1),
            ):
                pull_request = {
                    "body": "",
                    "user": {"type": "User"},
                    "head": {
                        "ref": "main",
                        "repo": head_repo and {"full_name": head_repo},
                    },
                    "base": {"ref": "develop", "repo": {"full_name": "o/r"}},
                }
                with open(event, "w", encoding="utf-8") as f:
                    json.dump({"pull_request": pull_request}, f)
                self.assertEqual(
                    self.run_main([], {"GITHUB_EVENT_PATH": event})[0],
                    expected,
                    head_repo,
                )

    def test_error_says_to_edit_the_body(self):
        self.assertIn("Edit the pull request body", self.run_main(["-"], stdin="x")[1])

    def test_event_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = os.path.join(tmp, "event.json")
            for body, expected in (("## Changelog\nnone", 0), ("x", 1), (None, 1)):
                with open(event, "w", encoding="utf-8") as f:
                    json.dump({"pull_request": {"body": body}}, f)
                self.assertEqual(
                    self.run_main([], {"GITHUB_EVENT_PATH": event})[0], expected
                )
