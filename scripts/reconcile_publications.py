"""Inspect and resolve publication attempts after manual verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import redirect_stdout
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--tenant", required=True)
    unsent = actions.add_parser("verified-unsent")
    unsent.add_argument("--tenant", required=True)
    unsent.add_argument("--record", required=True)
    delivered = actions.add_parser("verified-delivered")
    delivered.add_argument("--tenant", required=True)
    delivered.add_argument("--record", required=True)
    delivered.add_argument("--chat-id", required=True)
    delivered.add_argument("--message-id", required=True, type=int)
    return parser


async def run_action(args: argparse.Namespace, store: object) -> object:
    from job_ftch.application.publication_reconciliation import (
        list_uncertain_publications,
        mark_verified_delivered,
        mark_verified_unsent,
    )

    if args.action == "verified-unsent":
        await mark_verified_unsent(store, args.record)
        return None
    if args.action == "verified-delivered":
        await mark_verified_delivered(
            store, args.record, chat_id=args.chat_id, message_id=args.message_id
        )
        return None
    return await list_uncertain_publications(store)


async def execute(args: argparse.Namespace, runner: Any) -> object:
    try:
        return await run_action(args, runner.get_runtime(args.tenant).store)
    finally:
        await runner.close()


def load_runner() -> Any:
    from job_ftch.cli import _load_tenant_runner
    from job_ftch.config import get_settings

    return _load_tenant_runner(get_settings())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with redirect_stdout(sys.stderr):
        result = asyncio.run(execute(args, load_runner()))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
