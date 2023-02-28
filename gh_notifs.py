from __future__ import annotations

import argparse
import base64
import datetime
import json
import subprocess
from enum import Enum
from typing import Any
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


def _gh_user() -> tuple[str, set[str]]:
    login = subprocess.check_output(
        ("gh", "api", "user", "-q", ".login"), text=True
    ).strip()

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
            "-f", f"userLogin={login}",
            "-f", f"query={teams_query}",
        )["data"]["organization"]["teams"]["nodes"]
        # fmt: on
    }

    return login, teams


def _gh_api(*query: str) -> Any:
    data = subprocess.check_output(("gh", "api", *query))
    return json.loads(data)


def _referrer_id(notification_id: str, user_id: str) -> str:
    prefix = b"\x93\x00\xce\x00s3\xa2\xb2"
    token = (
        base64.standard_b64encode(prefix + f"{notification_id}:{user_id}".encode())
        .decode()
        .rstrip("=")
    )
    return f"NT_{token}"


def display_pr(  # noqa: C901
    pr: PR, username: str, teams: set[str], notification_id: str
) -> str:
    if pr.status == Status.OPEN:
        if pr.merge_status == MergeStatus.CLEAN:
            status = "\x1b[92m\uf00c\x1b[0m "
        elif pr.merge_status == MergeStatus.AUTO_MERGE:
            status = "\u23e9"
        else:
            status = ""
    elif pr.status == Status.DRAFT:
        status = "\x1b[39;2m"
    elif pr.status == Status.MERGED:
        status = "\x1b[35m[M]\x1b[39;2m"
    elif pr.status == Status.CLOSED:
        status = "\x1b[31m[C]\x1b[39;2m"
    else:
        raise ValueError(f"{pr.status=}")

    if pr.base_ref != pr.base_default_branch:
        base_ref = f" {pr.base_ref}"
    else:
        base_ref = ""

    url = pr.html_url
    if notification_id:
        referrer_id = _referrer_id(
            notification_id, "7549858"
        )  # TODO: remove hardcoded ID
        url += f"?notification_referrer_id={referrer_id}"

    if pr.author == username:
        author = f"\x1b[33m{pr.author}\x1b[0m"
    else:
        author = pr.author

    reviewers, n_other_reviewers = [], 0
    for reviewer in pr.requested_reviewers:
        if reviewer == username:
            reviewers.append(f"\x1b[33m{reviewer}\x1b[39m")
        elif reviewer in teams:
            reviewers.append(f"{reviewer}")
        else:
            n_other_reviewers += 1

    if n_other_reviewers:
        reviewers.append(f"{n_other_reviewers} others")

    return f"""\
{status} \x1b[1m{pr.title}\x1b[0m ({pr.ref})
    by {author} -- updated {humanize.naturaltime(pr.updated_at)} -- ({pr.commits} commits, {pr.files} files) [\x1b[92m+{pr.additions}\x1b[0m \x1b[91m-{pr.deletions}\x1b[0m] {base_ref}
    \x1b[2m{', '.join(reviewers)}\x1b[0m
    \x1b[2m{url}\x1b[0m
"""  # noqa: E501


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)

    username, teams = _gh_user()

    notifs = _gh_api("notifications")
    notifs = [n for n in notifs if n["subject"]["type"] == "PullRequest"]

    for notif in notifs:
        pr_data = _gh_api(notif["subject"]["url"])
        pr = PR.from_json(pr_data)
        print(display_pr(pr, username, teams, notif["id"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
