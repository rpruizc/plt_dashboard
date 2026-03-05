#!/usr/bin/env python3
"""Temporary script: run only Step 2 (URL candidate discovery) on random accounts."""

from __future__ import annotations

import argparse
import random
import sys

import app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Step 2 URL discovery for random companies in ACCOUNTS."
    )
    parser.add_argument("--count", type=int, default=10, help="How many random companies to test.")
    parser.add_argument("--seed", type=int, default=None, help="Optional random seed for reproducibility.")
    parser.add_argument(
        "--include-brave",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include Brave search candidates (default: true).",
    )
    parser.add_argument(
        "--include-deep-fallback",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include deep fallback extraction (default: false).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    app.init_db()
    accounts_map = app.build_accounts()
    accounts = list(accounts_map.values())

    if not accounts:
        print("No accounts found.")
        return 1

    count = min(max(args.count, 1), len(accounts))
    rng = random.Random(args.seed)
    sample = rng.sample(accounts, count)

    print(
        f"Step 2 run: {count} random companies "
        f"(include_brave={args.include_brave}, include_deep_fallback={args.include_deep_fallback}, seed={args.seed})"
    )
    print()
    print("idx\tbp_id\tcompany\tcandidate_url\tconfidence\tsource\tcandidate_count")

    for idx, account in enumerate(sample, start=1):
        bp_id = account.get("bp_id")
        name = (account.get("company_name") or "").replace("\t", " ")

        try:
            candidates, _metrics = app.generate_url_candidates(
                account,
                include_brave=args.include_brave,
                include_deep_fallback=args.include_deep_fallback,
            )
        except Exception as exc:
            print(f"{idx}\t{bp_id}\t{name}\t\t0.0\terror:{type(exc).__name__}\t0")
            continue

        top = candidates[0] if candidates else {}
        candidate_url = (top.get("candidate_url") or "").replace("\t", " ")
        confidence = float(top.get("confidence") or 0.0)
        source = (top.get("source") or "none").replace("\t", " ")
        candidate_count = len(candidates)

        print(
            f"{idx}\t{bp_id}\t{name}\t{candidate_url}\t{confidence:.1f}\t{source}\t{candidate_count}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
