#!/usr/bin/env python3
"""CI: append the Changelog lines of a merged pull request to the running "Changelog" issue.

Run by .github/workflows/changelog-feed.yml on a closed pull_request_target event. The event file named by
GITHUB_EVENT_PATH supplies only the pull request number and the repository's default branch. The pull request itself
(body, merged state, merge commit, base branch) is read from the API (GET /repos/{repo}/pulls/{number}), so a re-run
after a body fix sees the fixed body, and its body is parsed with check_changelog.parse_changelog(). A pull request
that is not merged, was not merged into the default branch, or whose section is "none" (or missing or invalid) adds
nothing; an invalid or empty section on a merged pull request also logs a ::warning::. Otherwise one comment goes to
the open issue titled "Changelog" that github-actions[bot] created: the merge date, links to the pull request and
to its merge commit, and the lines. The issue is created, and pinned best-effort, on first use.

Environment: GITHUB_EVENT_PATH, GITHUB_TOKEN, GITHUB_REPOSITORY (owner/name); optional GITHUB_API_URL,
GITHUB_GRAPHQL_URL and GITHUB_SERVER_URL for GitHub Enterprise. The token needs issues: write and
pull-requests: read (the pull request read). The script names no branch: the default branch comes from the event,
so it is repo-agnostic.

Re-runs are safe. Each comment starts with the hidden marker line "<!-- changelog-feed:pr-N -->", and a pull
request whose marker is already present is skipped. Only comments by github-actions[bot] count, and only when the
marker is their first line, so nobody else can make the feed skip a pull request. Runs are not serialized (a
concurrency group with a queue is not accepted by the pinned actionlint), so two first runs can each create the
issue; after creating, the script lists the issues again, keeps the oldest and closes the newer duplicates.

Python 3 standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.request

import check_changelog

BOT = "github-actions[bot]"
TITLE = "Changelog"
PER_PAGE = 100
ISSUE_BODY = (
    "Running changelog: one comment per merged pull request that has user-facing lines in its "
    '"## Changelog" section, appended automatically by .github/workflows/changelog-feed.yml. '
    "Do not edit or close this issue."
)
PIN_MUTATION = (
    "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { number } } }"
)


class Api:
    """GitHub REST and GraphQL over urllib. request() returns the parsed JSON ("" for an empty reply)."""

    def __init__(self, token, api_url, graphql_url):
        self.token, self.api_url, self.graphql_url = token, api_url, graphql_url

    def request(self, method, path, body=None):
        url = self.graphql_url if path == "/graphql" else self.api_url + path
        req = urllib.request.Request(
            url,
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "changelog-feed",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("%s %s: HTTP %d %s" % (method, path, e.code, detail))
        return json.loads(raw) if raw else ""


def get_all(api, path):
    """Every item of a paginated list endpoint."""
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        chunk = api.request(
            "GET", "%s%sper_page=%d&page=%d" % (path, sep, PER_PAGE, page)
        )
        items.extend(chunk)
        if len(chunk) < PER_PAGE:
            return items
        page += 1


def feed_issues(api, repo):
    """Open issues titled "Changelog" created by github-actions[bot], oldest first."""
    found = [
        i
        for i in get_all(
            api,
            "/repos/%s/issues?state=open&creator=github-actions%%5Bbot%%5D&sort=created&direction=asc"
            % repo,
        )
        if (i.get("user") or {}).get("login") == BOT
        and i.get("title") == TITLE
        and "pull_request" not in i
    ]
    return sorted(found, key=lambda i: (i["created_at"], i["number"]))


def pin(api, issue):
    """Pin the issue (GraphQL). Best-effort: a failure is logged, never raised."""
    try:
        reply = api.request(
            "POST",
            "/graphql",
            {"query": PIN_MUTATION, "variables": {"id": issue["node_id"]}},
        )
        if reply.get("errors"):
            raise RuntimeError(json.dumps(reply["errors"])[:300])
        print("Pinned issue #%d." % issue["number"])
    except Exception as e:  # best-effort by design
        print("Could not pin issue #%d (continuing): %s" % (issue["number"], e))


def ensure_feed_issue(api, repo):
    """Return the feed issue, creating it when there is none. After creating, keep the oldest of the bot's
    "Changelog" issues and close the newer ones, so two concurrent first runs end up with one issue.
    """
    existing = feed_issues(api, repo)
    if existing:
        return existing[0]
    mine = api.request(
        "POST", "/repos/%s/issues" % repo, {"title": TITLE, "body": ISSUE_BODY}
    )
    print("Created issue #%d." % mine["number"])
    issues = feed_issues(api, repo) or [mine]
    keep = issues[0]
    for dup in issues[1:]:
        api.request(
            "PATCH",
            "/repos/%s/issues/%d" % (repo, dup["number"]),
            {"state": "closed", "state_reason": "not_planned"},
        )
        print("Closed duplicate issue #%d." % dup["number"])
    if keep["number"] == mine["number"]:
        pin(api, mine)
    return keep


def marker(number):
    return "<!-- changelog-feed:pr-%d -->" % number


def has_marker(comments, number):
    """True when github-actions[bot] already posted the marker of this pull request as the first line."""
    return any(
        (c.get("user") or {}).get("login") == BOT
        and ((c.get("body") or "").splitlines() or [""])[0].strip() == marker(number)
        for c in comments
    )


def comment_body(pr, entries, repo, server_url):
    """The marker, then the date, the pull request link, the merge commit link and the lines."""
    base = "%s/%s" % (server_url, repo)
    head = "**%s** - [#%d](%s)" % (
        (pr.get("merged_at") or "")[:10] or "undated",
        pr["number"],
        pr.get("html_url") or "%s/pull/%d" % (base, pr["number"]),
    )
    sha = pr.get("merge_commit_sha")
    if sha:
        head += " ([%s](%s/commit/%s))" % (sha[:7], base, sha)
    lines = "\n".join("- %s: %s" % (kind, text) for kind, text in entries)
    # a zero-width space after @ keeps a Changelog line from notifying anyone
    return "%s\n%s\n\n%s" % (marker(pr["number"]), head, lines.replace("@", "@\u200b"))


def feed(api, repo, number, default_branch, server_url="https://github.com"):
    """Post the comment for one merged pull request. Returns a short description of what happened.

    The pull request is read from the API, not from the event payload: a re-run of the job reuses the original
    payload, so only the API shows a body that was fixed after the merge. Only pull requests merged into the
    default branch are fed; one merged into another branch has not shipped yet."""
    pr = api.request("GET", "/repos/%s/pulls/%d" % (repo, number))
    if not (pr.get("merged") or pr.get("merged_at")):
        return "pull request #%d is not merged: nothing to add" % number
    base = (pr.get("base") or {}).get("ref")
    if base != default_branch:
        return "pull request #%d was merged into %s, not %s: nothing to add" % (
            number,
            base,
            default_branch,
        )
    status, entries, errors = check_changelog.parse_changelog(pr.get("body"))
    if status != check_changelog.OK:
        if status in (check_changelog.INVALID, check_changelog.EMPTY):
            print(
                "::warning::Pull request #%d was merged with an invalid Changelog section "
                "and is not in the feed: %s" % (number, "; ".join(errors))
            )
        return "pull request #%d has no changelog lines (%s): nothing to add" % (
            number,
            status,
        )
    issue = ensure_feed_issue(api, repo)
    path = "/repos/%s/issues/%d/comments" % (repo, issue["number"])
    if has_marker(get_all(api, path), number):
        return "pull request #%d is already in issue #%d: skipped" % (
            number,
            issue["number"],
        )
    api.request("POST", path, {"body": comment_body(pr, entries, repo, server_url)})
    return "added pull request #%d to issue #%d" % (number, issue["number"])


def main(environ=None):
    env = os.environ if environ is None else environ
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    api_url = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    api = Api(
        env["GITHUB_TOKEN"],
        api_url,
        env.get("GITHUB_GRAPHQL_URL", api_url + "/graphql"),
    )
    print(
        feed(
            api,
            env["GITHUB_REPOSITORY"],
            event["pull_request"]["number"],
            event["repository"]["default_branch"],
            env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
