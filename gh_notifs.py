from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import json
import subprocess
import sys
from enum import Enum
from typing import Any
from typing import Collection
from typing import Iterable
from typing import Iterator
from typing import NamedTuple
from typing import Protocol
from typing import Sequence

import humanize

# -------
# Objects
# -------


class User(NamedTuple):
    id: str
    login: str
    teams: Collection[str]


class PRStatus(Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class PRMergeStatus(Enum):
    CLEAN = "CLEAN"  # can be merged
    AUTO_MERGE = "AUTO_MERGE"  # will be merged automatically
    UNKNOWN = "UNKNOWN"


class PR(NamedTuple):
    title: str
    author: str

    state: str
    draft: bool
    merged: bool
    mergeable_state: str
    auto_merge: bool

    owner: str
    repo: str
    base_ref: str
    base_default_branch: str
    number: str
    html_url: str

    updated_at_str: str

    requested_reviewers: list[str]

    commits: int
    files: int
    additions: int
    deletions: int

    @property
    def status(self) -> PRStatus:
        if self.state == "open":
            if self.draft:
                return PRStatus.DRAFT
            else:
                return PRStatus.OPEN
        elif self.state == "closed":
            if self.merged:
                return PRStatus.MERGED
            else:
                return PRStatus.CLOSED
        else:
            raise ValueError(f"Unrecognised state: {self.state}")

    @property
    def merge_status(self) -> PRMergeStatus:
        if self.mergeable_state == "clean":
            return PRMergeStatus.CLEAN
        elif self.auto_merge:
            return PRMergeStatus.AUTO_MERGE
        elif self.mergeable_state in {
            "behind",
            "blocked",
            "dirty",
            "unknown",
            "unstable",
        }:
            return PRMergeStatus.UNKNOWN
        else:
            raise ValueError(f"Unrecognised mergeable state: {self.mergeable_state}")

    @property
    def ref(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def updated_at(self) -> datetime.datetime:
        updated_at_str = self.updated_at_str.replace("Z", "+00:00")
        updated_at = datetime.datetime.fromisoformat(updated_at_str)

        # convert to local time and then remove the timezone info
        return updated_at.astimezone().replace(tzinfo=None)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PR:
        owner = data["base"]["repo"]["owner"]["login"]
        return cls(
            title=data["title"],
            author=data["user"]["login"],
            state=data["state"],
            draft=data["draft"],
            merged=data["merged"],
            mergeable_state=data["mergeable_state"],
            auto_merge=bool(data["auto_merge"]),
            owner=owner,
            repo=data["base"]["repo"]["name"],
            base_ref=data["base"]["ref"],
            base_default_branch=data["base"]["repo"]["default_branch"],
            number=data["number"],
            html_url=data["html_url"],
            updated_at_str=data["updated_at"],
            requested_reviewers=[
                *(reviewer["login"] for reviewer in data["requested_reviewers"]),
                *(f"{owner}/{team['slug']}" for team in data["requested_teams"]),
            ],
            commits=data["commits"],
            files=data["changed_files"],
            additions=data["additions"],
            deletions=data["deletions"],
        )


class Notification(NamedTuple):
    id: str
    user: User
    pr: PR

    @property
    def url(self) -> str:
        prefix = b"\x93\x00\xce\x00s3\xa2\xb2"
        token = (
            base64.standard_b64encode(prefix + f"{self.id}:{self.user.id}".encode())
            .decode()
            .rstrip("=")
        )
        return f"{self.pr.html_url}?notification_referrer_id=NT_{token}"


# ----------
# Formatting
# ----------


class Formatter(Protocol):
    def format(self, notifications: Iterable[Notification]) -> str:
        ...


class ConsoleFormatter:
    @staticmethod
    def _format_notification(notif: Notification) -> str:  # noqa: C901
        if notif.pr.status == PRStatus.OPEN:
            if notif.pr.merge_status == PRMergeStatus.CLEAN:
                status = "\x1b[92m\uf00c\x1b[0m "
            elif notif.pr.merge_status == PRMergeStatus.AUTO_MERGE:
                status = "\u23e9"
            else:
                status = ""
        elif notif.pr.status == PRStatus.DRAFT:
            status = "\x1b[39;2m"
        elif notif.pr.status == PRStatus.MERGED:
            status = "\x1b[35m[M]\x1b[39;2m"
        elif notif.pr.status == PRStatus.CLOSED:
            status = "\x1b[31m[C]\x1b[39;2m"
        else:
            raise ValueError(f"{notif.pr.status=}")

        if notif.pr.base_ref != notif.pr.base_default_branch:
            base_ref = f" {notif.pr.base_ref}"
        else:
            base_ref = ""

        if notif.pr.author == notif.user.login:
            author = f"\x1b[33m{notif.pr.author}\x1b[0m"
        else:
            author = notif.pr.author

        reviewers, n_other_reviewers = [], 0
        for reviewer in notif.pr.requested_reviewers:
            if reviewer == notif.user.login:
                reviewers.append(f"\x1b[33m{reviewer}\x1b[39m")
            elif reviewer in notif.user.teams:
                _org, _, slug = reviewer.partition("/")
                reviewers.append(f"{slug}")
            else:
                n_other_reviewers += 1

        if n_other_reviewers:
            reviewers.append(f"{n_other_reviewers} others")

        return f"""\
{status} \x1b[1m{notif.pr.title}\x1b[0m ({notif.pr.ref})
    by {author} -- updated {humanize.naturaltime(notif.pr.updated_at)} -- ({notif.pr.commits} commits, {notif.pr.files} files) [\x1b[92m+{notif.pr.additions}\x1b[0m \x1b[91m-{notif.pr.deletions}\x1b[0m] {base_ref}
    \x1b[2m{', '.join(reviewers)}\x1b[0m
    \x1b[2m{notif.url}\x1b[0m
"""  # noqa: E501

    def format(self, notifications: Iterable[Notification]) -> str:
        return "\n".join(
            self._format_notification(notification) for notification in notifications
        )


class HtmlFormatter:
    @staticmethod
    def _li_class(notif: Notification) -> str:
        if notif.pr.status == PRStatus.DRAFT:
            return "list-group-item-light"
        elif notif.pr.status == PRStatus.CLOSED:
            return "list-group-item-danger"
        else:
            return ""

    @staticmethod
    def _li_style(notif: Notification) -> str:
        if notif.pr.status == PRStatus.MERGED:
            return "color: #2c1a4d; background-color: #c5b3e6;"
        else:
            return ""

    @staticmethod
    def _icons(notif: Notification) -> Iterator[str]:
        if notif.pr.status == PRStatus.OPEN:
            if notif.pr.merge_status == PRMergeStatus.CLEAN:
                yield (
                    '<i class="bi bi-check-circle-fill text-success fs-3 p-2" '
                    'style="float: right;" title="has been approved"></i>'
                )
            if notif.pr.merge_status == PRMergeStatus.AUTO_MERGE:
                yield (
                    '<i class="bi bi-fast-forward-circle fs-3 p-2" '
                    'style="float: right; color: #6f42c1;" '
                    'title="auto-merge enabled"></i>'
                )
        elif notif.pr.status == PRStatus.DRAFT:
            yield (
                '<i class="bi bi-pencil text-secondary fs-3 p-2" '
                'style="float: right;" title="draft"></i>'
            )
        elif notif.pr.status == PRStatus.CLOSED:
            yield (
                '<i class="bi bi-x-circle text-danger fs-3 p-2" '
                'style="float: right;" title="closed"></i>'
            )
        elif notif.pr.status == PRStatus.MERGED:
            yield (
                '<i class="bi bi-sign-merge-right fs-3 p-2" '
                'style="float: right; color: #6f42c1;" title="merged"></i>'
            )

        if notif.pr.author == notif.user.login:
            yield (
                '<i class="bi bi-person-circle text-warning fs-3 p-2" '
                'style="float: right;" title="i am the author"></i>'
            )

    @staticmethod
    def _target_branch(pr: PR) -> str:
        if pr.base_ref != pr.base_default_branch:
            return f"""\
<span class="text-secondary">
  into <i class="bi bi-git"></i> {pr.base_ref}
</span>
"""
        else:
            return ""

    @staticmethod
    def _reviewer_list_items(notif: Notification) -> Iterator[str]:
        others = []
        for reviewer in notif.pr.requested_reviewers:
            if reviewer == notif.user.login:
                yield (
                    '<li class="list-group-item list-group-item-warning">'
                    f"{reviewer}</li>"
                )
            elif reviewer in notif.user.teams:
                _org, _, slug = reviewer.partition("/")
                yield f'<li class="list-group-item">{slug}</li>'
            else:
                _org, _, slug = reviewer.rpartition("/")
                others.append(slug)

        if others:
            yield (
                '<li class="list-group-item list-group-item-light">'
                f'<span title="{", ".join(others)}">{len(others)} others</span></li>'
            )

    def _render_notification(self, notif: Notification) -> str:
        target_branch = self._target_branch(notif.pr)
        return f"""\
<li class="list-group-item {self._li_class(notif)}" style="{self._li_style(notif)}">
    <p class="mb-1">
      <small class="lh-2">
        <span style="float: right;" title="{notif.pr.updated_at}">
          {humanize.naturaltime(notif.pr.updated_at)}
        </span>
        <a href="{notif.url}" target="_blank">{notif.pr.ref}</a>
        by {notif.pr.author}
        <br/>
        <span>{target_branch}</span>
      </small>
    </p>
  <h5 class="mb-1">
    {notif.pr.title}
  </h5>
  <div>
    <p class="mb-1">
      {"".join(self._icons(notif))}
      <span class="text-success">+{notif.pr.additions}</span>
      <span class="text-danger">−{notif.pr.deletions}</span>
      in {notif.pr.commits} commits, {notif.pr.files} files.
    </p>
    <small>
      <ul class="list-group list-group-horizontal">
        {"".join(self._reviewer_list_items(notif))}
      </ul>
    </small>
  </div>
</li>
"""

    def format(self, notifications: Sequence[Notification]) -> str:
        return f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>GitHub PR notifications</title>

    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-rbsA2VBKQhggwzxH7pPCaAqO46MgnOM80zW1RWuH61DGLwZJEdK2Kadq2F9CUG65" crossorigin="anonymous">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.3/font/bootstrap-icons.css">
  </head>
  <body class="bg-dark">
    <div class="container my-2 lh-1">
      <span class="badge bg-secondary">{len(notifications)} unread notifications</span>
      <ul class="list-group">
        {"".join(self._render_notification(notification) for notification in notifications)}
      </ul>
    </div>

    <script>
      function reload() {{
        location.reload();
      }}
      setInterval(reload, 12000); // refresh every 12s
    </script>
  </body>
</html>
"""  # noqa: E501


# --------
# Printers
# --------


class Printer(Protocol):
    def print(self, value: str) -> None:
        ...


class ConsolePrinter:
    def print(self, value: str) -> None:
        print(value)


class FilePrinter:
    def __init__(self, filepath: str) -> None:
        self.filepath = filepath

    def print(self, value: str) -> None:
        with open(self.filepath, "w") as f:
            f.write(value)

        print(f"{datetime.datetime.now()}: written to {self.filepath}", file=sys.stderr)


# ----------
# GitHub API
# ----------


def _gh_api(*query: str, paginate: bool = False) -> Any:
    if paginate:
        try:
            data = subprocess.check_output(
                ("gh", "api", "--paginate", *query),
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"{datetime.datetime.now()}: {exc}", file=sys.stderr)
            raise SystemExit(exc.returncode) from exc
        else:
            data = data.replace("][", ",")  # join pages
    else:
        try:
            data = subprocess.check_output(("gh", "api", *query), text=True)
        except subprocess.CalledProcessError as exc:
            print(f"{datetime.datetime.now()}: {exc}", file=sys.stderr)
            raise SystemExit(exc.returncode) from exc

    return json.loads(data)


def _gh_user() -> User:
    user = _gh_api("user")
    user_login = user["login"]
    user_id = str(user["id"])

    orgs_query = """\
{
  viewer {
    login
    organizations(first: 100) {
      nodes {
        login
      }
    }
  }
}"""
    organizations = (
        # fmt: off
        node["login"]
        for node in _gh_api(
            "graphql",
            "-f", f"query={orgs_query}"
        )["data"]["viewer"]["organizations"]["nodes"]
        # fmt: on
    )

    teams_query = """\
query($orgName: String!, $userLogin: String!) {
  organization(login: $orgName) {
    teams(userLogins: [$userLogin], first: 100) {
      nodes {
        slug
      }
    }
  }
}"""
    teams = {
        # fmt: off
        f"{organization}/{node['slug']}"
        for organization in organizations
        for node in _gh_api(
            "graphql",
            "-f", f"orgName={organization}",
            "-f", f"userLogin={user_login}",
            "-f", f"query={teams_query}",
        )["data"]["organization"]["teams"]["nodes"]
        # fmt: on
    }

    return User(
        id=user_id,
        login=user_login,
        teams=teams,
    )


async def _gh_api_async(*query: str) -> Any:
    proc = await asyncio.create_subprocess_exec(
        *("gh", "api", *query),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if await proc.wait():
        assert proc.stderr  # we pipe stderr to the Process object
        stderr = await proc.stderr.read()
        print(f"{datetime.datetime.now()}: {stderr.decode()}", file=sys.stderr)
        raise SystemExit(proc.returncode)

    assert proc.stdout  # we pipe stdout to the Process object
    stdout = await proc.stdout.read()
    return json.loads(stdout.decode())


async def _gh_pr(url: str) -> PR:
    pr_data = await _gh_api_async(url)
    return PR.from_json(pr_data)


async def _gh_notif(id: str, pr_url: str, user: User) -> Notification:
    pr = await _gh_pr(pr_url)
    return Notification(id, user, pr)


# -----------
# Application
# -----------


async def amain(formatter: Formatter, printer: Printer) -> int:
    user = _gh_user()

    notifs_data = (
        n
        for n in _gh_api("notifications", paginate=True)
        if n["subject"]["type"] == "PullRequest"
    )

    notifications = await asyncio.gather(
        *(
            _gh_notif(notif_data["id"], notif_data["subject"]["url"], user)
            for notif_data in notifs_data
        )
    )

    printer.print(formatter.format(notifications))

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()

    format_mutex = parser.add_mutually_exclusive_group()
    # fmt: off
    format_mutex.add_argument(
        "-c", "--console",
        action="store_const", dest="formatter", const=ConsoleFormatter,
    )
    format_mutex.add_argument(
        "-H", "--html",
        action="store_const", dest="formatter", const=HtmlFormatter,
    )
    # fmt: on

    print_mutex = parser.add_mutually_exclusive_group()
    # fmt: off
    print_mutex.add_argument(
        "-f", "--filepath",
        type=str, default=None
    )
    # fmt: on

    parser.set_defaults(formatter=ConsoleFormatter)
    args = parser.parse_args(argv)

    formatter: Formatter = args.formatter()
    printer: Printer
    if args.filepath:
        printer = FilePrinter(args.filepath)
    else:
        printer = ConsolePrinter()

    return asyncio.run(amain(formatter, printer))


if __name__ == "__main__":
    raise SystemExit(main())
