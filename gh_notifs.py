from __future__ import annotations

import argparse
import datetime
import json
from typing import Any
from typing import Sequence

import humanize
import urllib3

http = urllib3.PoolManager()

NOTIFS_URL = "https://api.github.com/notifications"


def get_data(url: str) -> Any:
    resp = http.request("GET", url)
    return json.loads(resp.data)


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
    parser.add_argument(
        "--hide-closed",
        action="store_true",
        default=False,
        help="Only show open PRs",
    )
    args = parser.parse_args(argv)

    http.headers = urllib3.make_headers(basic_auth=args.basic_auth)

    notifs = get_data(NOTIFS_URL)
    notifs = [n for n in notifs if n["subject"]["type"] == "PullRequest"]

    for notif in notifs:
        pr = get_data(notif["subject"]["url"])

        if args.hide_closed and pr["state"] == "closed":
            continue

        if pr["state"] == "open":
            if pr["draft"]:
                status = " \x1b[2m[D]\x1b[0m"
            else:
                status = ""
        elif pr["state"] == "closed":
            if pr["merged"]:
                status = " \x1b[35m[M]\x1b[39;2m"
            else:
                status = " \x1b[31m[C]\x1b[39;2m"
        else:
            status = f" [{pr['state'].upper()}]"

        updated_at = pr["updated_at"]
        if updated_at.endswith("Z"):
            updated_at = updated_at[:-1]
        updated_at = datetime.datetime.fromisoformat(updated_at)

        html_url = pr["html_url"]
        if args.referrer_id:
            html_url += f"?notification_referrer_id={args.referrer_id}"

        ref = (
            pr["base"]["repo"]["owner"]["login"]
            + "/"
            + pr["base"]["repo"]["name"]
            + "#"
            + str(pr["number"])
        )

        print(f"{status} \x1b[1m{pr['title']}\x1b[0m ({ref})")
        print(
            "    "
            f"by {pr['user']['login']} "
            f"-- updated {humanize.naturaltime(updated_at)} "
            f"-- ({pr['commits']} commits, {pr['changed_files']} files) "
            f"[\x1b[92m+{pr['additions']}\x1b[0m \x1b[91m-{pr['deletions']}\x1b[0m] "
        )
        print(f"    \x1b[2m{html_url}\x1b[0m")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
