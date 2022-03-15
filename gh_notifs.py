from __future__ import annotations

import argparse
import base64
import datetime
import json
import urllib.request
from enum import Enum
from typing import Any
from typing import NamedTuple
from typing import Sequence

import humanize

NOTIFS_URL = "https://api.github.com/notifications"


class Status(Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class PR(NamedTuple):
    title: str
    author: str

    state: str
    draft: bool
    merged: bool

    owner: str
    repo: str
    number: str
    html_url: str

    updated_at_str: str

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
    def ref(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def updated_at(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.updated_at_str.rstrip("Z"))

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PR:
        return cls(
            title=data["title"],
            author=data["user"]["login"],
            state=data["state"],
            draft=data["draft"],
            merged=data["merged"],
            owner=data["base"]["repo"]["owner"]["login"],
            repo=data["base"]["repo"]["name"],
            number=data["number"],
            html_url=data["html_url"],
            updated_at_str=data["updated_at"],
            commits=data["commits"],
            files=data["changed_files"],
            additions=data["additions"],
            deletions=data["deletions"],
        )


def get_data(url: str, basic_auth: str) -> Any:
    headers = {
        "authorization": "Basic " + base64.b64encode(basic_auth.encode()).decode()
    }
    req = urllib.request.Request(url, headers=headers)
    data = urllib.request.urlopen(req).read().decode()
    return json.loads(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user",
        metavar="<user:password>",
        required=True,
        dest="basic_auth",
        help="Basic auth user and password",
    )
    parser.add_argument(
        "--referrer-id",
        dest="referrer_id",
        help="Notification referrer ID",
    )
    args = parser.parse_args(argv)

    notifs = get_data(NOTIFS_URL, args.basic_auth)
    notifs = [n for n in notifs if n["subject"]["type"] == "PullRequest"]

    for notif in notifs:
        pr_data = get_data(notif["subject"]["url"], args.basic_auth)
        pr = PR.from_json(pr_data)

        if pr.status == Status.OPEN:
            status = ""
        elif pr.status == Status.DRAFT:
            status = " \x1b[2m[D]\x1b[0m"
        elif pr.status == Status.MERGED:
            status = " \x1b[35m[M]\x1b[39;2m"
        elif pr.status == Status.CLOSED:
            status = " \x1b[31m[C]\x1b[39;2m"
        else:
            raise ValueError(f"{pr.status=}")

        url = pr.html_url
        if args.referrer_id:
            url += f"?notification_referrer_id={args.referrer_id}"

        print(f"{status} \x1b[1m{pr.title}\x1b[0m ({pr.ref})")
        print(
            "    "
            f"by {pr.author} "
            f"-- updated {humanize.naturaltime(pr.updated_at)} "
            f"-- ({pr.commits} commits, {pr.files} files) "
            f"[\x1b[92m+{pr.additions}\x1b[0m \x1b[91m-{pr.deletions}\x1b[0m] "
        )
        print(f"    \x1b[2m{url}\x1b[0m")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
