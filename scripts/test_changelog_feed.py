"""Unit tests for changelog_feed.py against a fake GitHub API. Run: PYTHONPATH=scripts python3 -m unittest test_changelog_feed"""

import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.parse

import changelog_feed as cf
import check_changelog

REPO = "o/r"


class FakeApi:
    """In-memory issues and comments. Records every request as (method, path-without-query)."""

    def __init__(self):
        self.issues, self.comments, self.calls = [], {}, []
        self.pin_error = None
        self.prs = {}
        self.on_create = (
            None  # called after our issue is created, to inject a racing run
        )
        self.clock = 0

    def add_issue(self, login="github-actions[bot]", title="Changelog", **extra):
        self.clock += 1
        number = len(self.issues) + 1
        issue = {
            "number": number,
            "node_id": "node%d" % number,
            "title": title,
            "state": "open",
            "user": {"login": login},
            "created_at": "2026-10-30T10:00:%02dZ" % self.clock,
        }
        issue.update(extra)
        self.issues.append(issue)
        self.comments[number] = []
        return issue

    def add_comment(self, number, body, login="github-actions[bot]"):
        self.comments[number].append({"user": {"login": login}, "body": body})

    def request(self, method, path, body=None):
        url = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(url.query)
        self.calls.append((method, url.path))
        parts = url.path.split("/")
        if url.path == "/graphql":
            if self.pin_error:
                return {"errors": [{"message": self.pin_error}]}
            return {"data": {"pinIssue": {"issue": {"number": 1}}}}
        if method == "GET" and parts[-2] == "pulls":
            return self.prs[int(parts[-1])]
        if method == "GET" and url.path == "/repos/%s/issues" % REPO:
            rows = [i for i in self.issues if i["state"] == query["state"][0]]
            return self.page(rows, query)
        if method == "POST" and url.path == "/repos/%s/issues" % REPO:
            issue = self.add_issue(title=body["title"], body=body["body"])
            if self.on_create:
                self.on_create()
            return issue
        if method == "PATCH" and parts[-2] == "issues":
            issue = self.issues[int(parts[-1]) - 1]
            issue["state"] = body["state"]
            return issue
        if parts[-1] == "comments":
            number = int(parts[-2])
            if method == "GET":
                return self.page(self.comments[number], query)
            self.add_comment(number, body["body"])
            return {}
        raise AssertionError((method, path))

    def page(self, rows, query):
        size, page = int(query["per_page"][0]), int(query["page"][0])
        return rows[(page - 1) * size : page * size]

    def posted(self, number):
        return [c["body"] for c in self.comments[number]]


def merged_pr(body="## Changelog\nFixed: scans no longer stall.", number=7, **extra):
    pr = {
        "number": number,
        "html_url": "https://github.com/o/r/pull/%d" % number,
        "merged": True,
        "base": {"ref": "main"},
        "merged_at": "2026-10-30T12:34:56Z",
        "merge_commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "body": body,
    }
    pr.update(extra)
    return pr


def run_feed(api, pr, default_branch="main", server_url="https://github.com"):
    """Feed a pull request that the fake API serves for GET /pulls/N."""
    api.prs[pr["number"]] = pr
    return cf.feed(api, REPO, pr["number"], default_branch, server_url)


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        out = contextlib.redirect_stdout(io.StringIO())
        out.__enter__()
        self.addCleanup(out.__exit__, None, None, None)

    def test_comment_has_marker_date_links_and_lines(self):
        pr = merged_pr("## Changelog\n- Fixed: a\nAdded: b")
        self.api.add_issue()
        run_feed(self.api, pr)
        self.assertEqual(
            self.api.posted(1),
            [
                "<!-- changelog-feed:pr-7 -->\n"
                "**2026-10-30** - [#7](https://github.com/o/r/pull/7) "
                "([0123456](https://github.com/o/r/commit/0123456789abcdef0123456789abcdef01234567))\n\n"
                "- Fixed: a\n- Added: b"
            ],
        )

    def test_server_url_is_used_for_the_commit_link(self):
        self.api.add_issue()
        run_feed(self.api, merged_pr(), server_url="https://ghe.example")
        self.assertIn("(https://ghe.example/o/r/commit/0123456", self.api.posted(1)[0])

    def test_the_api_body_wins_over_the_event_body(self):
        # the job is re-run after the body was fixed: the event still carries the old payload
        self.api.add_issue()
        fixed = merged_pr("## Changelog\nFixed: scans no longer stall.")
        api_pr = dict(fixed)
        self.api.prs[7] = api_pr
        stale_event_pr = dict(fixed, body="Fixed stuff")
        self.assertEqual(
            check_changelog.parse_changelog(stale_event_pr["body"])[0], "missing"
        )
        self.assertIn(
            "added", cf.feed(self.api, REPO, stale_event_pr["number"], "main")
        )
        self.assertIn("Fixed: scans no longer stall.", self.api.posted(1)[0])
        self.assertIn(("GET", "/repos/o/r/pulls/7"), self.api.calls)

    def test_other_pull_request_fields_come_from_the_api(self):
        self.api.add_issue()
        run_feed(
            self.api,
            merged_pr(
                merged_at="2026-01-02T00:00:00Z", merge_commit_sha="abcdef1234567"
            ),
        )
        self.assertIn("**2026-01-02**", self.api.posted(1)[0])
        self.assertIn("[abcdef1]", self.api.posted(1)[0])

    def test_only_the_default_branch_is_fed(self):
        for base in ("develop", "feature/x", "main2"):
            out = run_feed(self.api, merged_pr(base={"ref": base}))
            self.assertIn("nothing to add", out)
            self.assertIn(base, out)
        self.assertEqual(
            [c for c in self.api.calls if c[0] != "GET"], []
        )  # no API writes
        self.assertIn(
            "added", run_feed(self.api, merged_pr(base={"ref": "master"}), "master")
        )

    def test_invalid_section_logs_a_warning(self):
        for body in ("## Changelog\nFixed stuff", "## Changelog\n"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                run_feed(self.api, merged_pr(body))
            self.assertIn(
                "::warning::Pull request #7 was merged with an invalid", out.getvalue()
            )
        for body in ("## Changelog\nnone", "no section"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                run_feed(self.api, merged_pr(body))
            self.assertNotIn("::warning::", out.getvalue())

    def test_none_variants_add_nothing(self):
        for body in (
            "## Changelog\nnone",
            "## Changelog\nNone.",
            "## Changelog\n- none",
        ):
            self.assertIn("no changelog lines", run_feed(self.api, merged_pr(body)))
        self.assertEqual([c for c in self.api.calls if c[0] != "GET"], [])

    def test_missing_empty_and_invalid_sections_add_nothing(self):
        for body in (None, "", "## Changelog", "## Changelog\nprose", "## Summary\nx"):
            run_feed(self.api, merged_pr(body))
        self.assertEqual([c for c in self.api.calls if c[0] != "GET"], [])

    def test_unmerged_pull_request_adds_nothing(self):
        pr = merged_pr(merged=False, merged_at=None)
        self.assertIn("not merged", run_feed(self.api, pr))
        self.assertEqual([c for c in self.api.calls if c[0] != "GET"], [])

    def test_rerun_is_idempotent(self):
        self.api.add_issue()
        pr = merged_pr()
        run_feed(self.api, pr)
        self.assertIn("skipped", run_feed(self.api, pr))
        self.assertEqual(len(self.api.comments[1]), 1)
        run_feed(self.api, merged_pr(number=8))
        self.assertEqual(len(self.api.comments[1]), 2)

    def test_marker_from_a_stranger_does_not_skip(self):
        self.api.add_issue()
        self.api.add_comment(1, cf.marker(7), login="mallory")
        run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.comments[1]), 2)
        self.assertEqual(
            self.api.comments[1][1]["user"]["login"], "github-actions[bot]"
        )

    def test_marker_not_on_the_first_line_does_not_skip(self):
        self.api.add_issue()
        self.api.add_comment(1, "quoting:\n" + cf.marker(7))
        self.api.add_comment(1, "see " + cf.marker(7))
        run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.comments[1]), 3)

    def test_marker_must_be_for_the_same_pull_request(self):
        self.api.add_issue()
        self.api.add_comment(1, cf.marker(70) + "\nrest")
        self.api.add_comment(1, cf.marker(7) + "\nrest")
        self.assertIn("skipped", run_feed(self.api, merged_pr()))
        self.assertIn("added", run_feed(self.api, merged_pr(number=700)))

    def test_markers_are_found_across_comment_pages(self):
        self.api.add_issue()
        for n in range(1, 6):
            self.api.add_comment(1, cf.marker(n))
        with unittest.mock.patch.object(cf, "PER_PAGE", 2):
            self.assertIn("skipped", run_feed(self.api, merged_pr(number=5)))

    def test_mentions_are_defused(self):
        self.api.add_issue()
        run_feed(self.api, merged_pr("## Changelog\nFixed: thanks @someone"))
        self.assertNotIn("@someone", self.api.posted(1)[0])
        self.assertIn("thanks @\u200bsomeone", self.api.posted(1)[0])


class IssueTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        out = contextlib.redirect_stdout(io.StringIO())
        out.__enter__()
        self.addCleanup(out.__exit__, None, None, None)

    def test_first_run_creates_and_pins_the_issue(self):
        run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.issues), 1)
        self.assertEqual(self.api.issues[0]["title"], "Changelog")
        self.assertIn(("POST", "/graphql"), self.api.calls)
        self.assertEqual(len(self.api.posted(1)), 1)

    def test_second_run_reuses_the_issue_and_does_not_pin_again(self):
        run_feed(self.api, merged_pr())
        run_feed(self.api, merged_pr(number=8))
        self.assertEqual(len(self.api.issues), 1)
        self.assertEqual(self.api.calls.count(("POST", "/graphql")), 1)
        self.assertEqual(len(self.api.posted(1)), 2)

    def test_pin_failure_is_logged_and_does_not_fail_the_run(self):
        self.api.pin_error = "Resource not accessible by integration"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIn("added", run_feed(self.api, merged_pr()))
        self.assertIn("Could not pin issue #1", out.getvalue())
        self.assertIn("Resource not accessible", out.getvalue())

    def test_issues_not_made_by_the_bot_are_ignored(self):
        self.api.add_issue(login="mallory")
        self.api.add_issue(title="Changelog notes")
        self.api.add_issue(pull_request={"url": "x"})
        self.api.add_issue(state="closed")
        run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.issues), 5)
        self.assertEqual(len(self.api.posted(5)), 1)
        for n in range(1, 5):
            self.assertEqual(self.api.posted(n), [])

    def test_lookup_picks_the_oldest_bot_issue(self):
        self.api.add_issue()
        self.api.add_issue()
        run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.posted(1)), 1)
        self.assertEqual(self.api.posted(2), [])

    def test_lookup_walks_all_pages(self):
        for _ in range(3):
            self.api.add_issue(login="mallory")
        self.api.add_issue()
        with unittest.mock.patch.object(cf, "PER_PAGE", 2):
            run_feed(self.api, merged_pr())
        self.assertEqual(len(self.api.issues), 4)
        self.assertEqual(len(self.api.posted(4)), 1)

    def test_racing_first_run_that_lost_closes_its_own_issue(self):
        # a second run created an older issue between our lookup and our create
        def racer():
            older = self.api.add_issue()
            older["created_at"] = "2026-10-30T09:00:00Z"

        self.api.on_create = racer
        run_feed(self.api, merged_pr())
        mine, theirs = self.api.issues[0], self.api.issues[1]
        self.assertEqual(mine["state"], "closed")
        self.assertEqual(theirs["state"], "open")
        self.assertEqual(self.api.posted(1), [])
        self.assertEqual(len(self.api.posted(2)), 1)
        self.assertNotIn(("POST", "/graphql"), self.api.calls)

    def test_racing_first_run_that_won_closes_the_newer_duplicate(self):
        self.api.on_create = self.api.add_issue
        run_feed(self.api, merged_pr())
        self.assertEqual([i["state"] for i in self.api.issues], ["open", "closed"])
        self.assertEqual(len(self.api.posted(1)), 1)
        self.assertEqual(self.api.posted(2), [])
        self.assertIn(("POST", "/graphql"), self.api.calls)


class MainTest(unittest.TestCase):
    def test_main_reads_the_event_and_calls_the_api(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as tmp:
            event = os.path.join(tmp, "event.json")
            with open(event, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "pull_request": {"number": 7},
                        "repository": {"default_branch": "main"},
                    },
                    f,
                )
            api.prs[7] = merged_pr()
            env = {
                "GITHUB_EVENT_PATH": event,
                "GITHUB_TOKEN": "t0ken",
                "GITHUB_REPOSITORY": REPO,
            }
            out = io.StringIO()
            with unittest.mock.patch.object(cf, "Api", return_value=api) as ctor:
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cf.main(env), 0)
        ctor.assert_called_once_with(
            "t0ken", "https://api.github.com", "https://api.github.com/graphql"
        )
        self.assertEqual(len(api.posted(1)), 1)
        self.assertNotIn("t0ken", out.getvalue())


class ApiTest(unittest.TestCase):
    def test_request_sends_token_and_json_and_parses_the_reply(self):
        resp = unittest.mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"ok": 1}'
        with unittest.mock.patch("urllib.request.urlopen", return_value=resp) as op:
            api = cf.Api("t0ken", "https://api.example", "https://gql.example")
            self.assertEqual(api.request("POST", "/x", {"a": 1}), {"ok": 1})
            self.assertEqual(api.request("POST", "/graphql", {}), {"ok": 1})
        req = op.call_args_list[0].args[0]
        self.assertEqual(req.full_url, "https://api.example/x")
        self.assertEqual(req.get_header("Authorization"), "Bearer t0ken")
        self.assertEqual(json.loads(req.data), {"a": 1})
        self.assertEqual(op.call_args_list[1].args[0].full_url, "https://gql.example")

    def test_http_error_names_the_request_but_not_the_token(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"nope"))
        self.addCleanup(err.close)
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            api = cf.Api("t0ken", "https://api.example", "https://gql.example")
            with self.assertRaises(RuntimeError) as ctx:
                api.request("GET", "/x")
        self.assertIn("GET /x: HTTP 403 nope", str(ctx.exception))
        self.assertNotIn("t0ken", str(ctx.exception))
