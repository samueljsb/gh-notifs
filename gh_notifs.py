from __future__ import annotations

import argparse
import base64
import datetime
import json
import subprocess
from enum import Enum
from typing import Any
from typing import Collection
from typing import NamedTuple
from typing import Sequence

import humanize


class Status(Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class MergeStatus(Enum):
    CLEAN = "CLEAN"  # can be merged
    AUTO_MERGE = "AUTO_MERGE"  # will be merged automatically
    UNKNOWN = "UNKNOWN"


class User(NamedTuple):
    id: str
    login: str
    teams: Collection[str]


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
    def status(self) -> Status:
        if self.state == "open":
            if self.draft:
                return Status.DRAFT
            else:
                return Status.OPEN
        elif self.state == "closed":
            if self.merged:
                return Status.MERGED
            else:
                return Status.CLOSED
        else:
            raise ValueError(f"Unrecognised state: {self.state}")

    @property
    def merge_status(self) -> MergeStatus:
        if self.mergeable_state == "clean":
            return MergeStatus.CLEAN
        elif self.auto_merge:
            return MergeStatus.AUTO_MERGE
        elif self.mergeable_state in {
            "behind",
            "blocked",
            "dirty",
            "unknown",
            "unstable",
        }:
            return MergeStatus.UNKNOWN
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
        if self.pr.status == Status.OPEN:
            if self.pr.merge_status == MergeStatus.CLEAN:
                status = "\x1b[92m\uf00c\x1b[0m "
            elif self.pr.merge_status == MergeStatus.AUTO_MERGE:
                status = "\u23e9"
            else:
                status = ""
        elif self.pr.status == Status.DRAFT:
            status = "\x1b[39;2m"
        elif self.pr.status == Status.MERGED:
            status = "\x1b[35m[M]\x1b[39;2m"
        elif self.pr.status == Status.CLOSED:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)

    user = _gh_user()

    notifs_data = (
        n for n in _gh_api("notifications") if n["subject"]["type"] == "PullRequest"
    )

    for notif_data in notifs_data:
        pr_data = _gh_api(notif_data["subject"]["url"])
        pr = PR.from_json(pr_data)

        notif = Notification(notif_data["id"], user, pr)
        print(notif.render())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
