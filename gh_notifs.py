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
from typing import NamedTuple
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
        return datetime.datetime.fromisoformat(self.updated_at_str.rstrip("Z"))

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

    def render(self) -> str:  # noqa: C901
        if self.pr.status == PRStatus.OPEN:
            if self.pr.merge_status == PRMergeStatus.CLEAN:
                status = "\x1b[92m\uf00c\x1b[0m "
            elif self.pr.merge_status == PRMergeStatus.AUTO_MERGE:
                status = "\u23e9"
            else:
                status = ""
        elif self.pr.status == PRStatus.DRAFT:
            status = "\x1b[39;2m"
        elif self.pr.status == PRStatus.MERGED:
            status = "\x1b[35m[M]\x1b[39;2m"
        elif self.pr.status == PRStatus.CLOSED:
            status = "\x1b[31m[C]\x1b[39;2m"
        else:
            raise ValueError(f"{self.pr.status=}")

        if self.pr.base_ref != self.pr.base_default_branch:
            base_ref = f" {self.pr.base_ref}"
        else:
            base_ref = ""

        if self.pr.author == self.user.login:
            author = f"\x1b[33m{self.pr.author}\x1b[0m"
        else:
            author = self.pr.author

        reviewers, n_other_reviewers = [], 0
        for reviewer in self.pr.requested_reviewers:
            if reviewer == self.user.login:
                reviewers.append(f"\x1b[33m{reviewer}\x1b[39m")
            elif reviewer in self.user.teams:
                reviewers.append(f"{reviewer}")
            else:
                n_other_reviewers += 1

        if n_other_reviewers:
            reviewers.append(f"{n_other_reviewers} others")

        return f"""\
{status} \x1b[1m{self.pr.title}\x1b[0m ({self.pr.ref})
    by {author} -- updated {humanize.naturaltime(self.pr.updated_at)} -- ({self.pr.commits} commits, {self.pr.files} files) [\x1b[92m+{self.pr.additions}\x1b[0m \x1b[91m-{self.pr.deletions}\x1b[0m] {base_ref}
    \x1b[2m{', '.join(reviewers)}\x1b[0m
    \x1b[2m{self.url}\x1b[0m
"""  # noqa: E501


# ----------
# GitHub API
# ----------


def _gh_api(*query: str) -> Any:
    data = subprocess.check_output(("gh", "api", *query))
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
        print(stderr, file=sys.stderr)
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


async def amain() -> int:
    user = _gh_user()

    notifs_data = (
        n for n in _gh_api("notifications") if n["subject"]["type"] == "PullRequest"
    )

    notifications = await asyncio.gather(
        *(
            _gh_notif(notif_data["id"], notif_data["subject"]["url"], user)
            for notif_data in notifs_data
        )
    )

    for notif in notifications:
        print(notif.render())

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)

    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
