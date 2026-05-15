"""``hermes sonzai setup`` wizard.

Mirrors ``src/setup.ts`` + ``src/cli.ts`` from ``sonzai-openclaw``:
- prompt for API key (or detect ``SONZAI_API_KEY``)
- ask for an existing ``agent_id`` or provision one
- ask ``sync``/``async`` memory mode
- write non-secret values via ``save_config``; secret goes to ``.env``

Also exposes ``hermes sonzai health`` → ``GET {base_url}/health``.
"""

from __future__ import annotations

import argparse


def register_cli(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``sonzai`` command group on Hermes' CLI."""
    sonzai = subparsers.add_parser("sonzai", help="Sonzai memory provider commands")
    sub = sonzai.add_subparsers(dest="sonzai_cmd", required=True)

    setup = sub.add_parser("setup", help="Interactive one-time setup")
    setup.set_defaults(func=_cmd_setup)

    health = sub.add_parser("health", help="Hit the Sonzai /health endpoint")
    health.set_defaults(func=_cmd_health)


def _cmd_setup(args: argparse.Namespace) -> int:
    raise NotImplementedError("Task 17 in PLAN.md — setup wizard.")


def _cmd_health(args: argparse.Namespace) -> int:
    raise NotImplementedError("Task 17 in PLAN.md — health subcommand.")
