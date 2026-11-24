#!/usr/bin/env python3
"""
Mint a bearer token for the team API (/api/team/*).

Run with the same SECRET_KEY the Cloud Run service uses:

    SECRET_KEY=... python mint_team_token.py --sub alice@example.com --role user --days 90

Paste the printed token into the extension's Team Dashboard when prompted.
"""

import argparse
import os
import sys

from app.api.team_auth import TEAM_ROLES, TeamAuthNotConfigured, issue_token


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sub", required=True, help="User id recorded as the owner of forms this user syncs")
    parser.add_argument("--role", choices=TEAM_ROLES, default="user", help="'admin' may delete and re-analyze forms")
    parser.add_argument("--days", type=int, default=90, help="Token lifetime in days")
    args = parser.parse_args()

    try:
        print(issue_token(args.sub, args.role, os.getenv("SECRET_KEY"), args.days * 86400))
    except TeamAuthNotConfigured:
        sys.exit("SECRET_KEY must be set to the service's secret (at least 16 characters).")


if __name__ == "__main__":
    main()
