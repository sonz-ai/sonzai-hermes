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
import getpass
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from sonzai_common import (
    DEFAULT_AGENT_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_MEMORY_MODE,
    SonzaiConfig,
    build_client,
    load_config,
    resolve_agent_id,
    save_config,
)

# Indirection lets tests stub interactive prompts.
PromptFn = Callable[[str], str]
SecretPromptFn = Callable[[str], str]


def register_cli(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``sonzai`` command group on Hermes' CLI."""
    sonzai = subparsers.add_parser("sonzai", help="Sonzai memory provider commands")
    sub = sonzai.add_subparsers(dest="sonzai_cmd", required=True)

    setup = sub.add_parser("setup", help="Interactive one-time setup")
    setup.set_defaults(func=_cmd_setup)

    health = sub.add_parser("health", help="Hit the Sonzai /health endpoint")
    health.set_defaults(func=_cmd_health)


def _cmd_setup(args: argparse.Namespace) -> int:
    return run_setup(
        hermes_home=getattr(args, "hermes_home", None),
        prompt=input,
        secret_prompt=getpass.getpass,
        out=sys.stdout.write,
    )


def _cmd_health(args: argparse.Namespace) -> int:
    return run_health(
        hermes_home=getattr(args, "hermes_home", None),
        out=sys.stdout.write,
    )


# ─── reusable, test-friendly entry points ──────────────────────────────────


def run_setup(
    hermes_home: str | os.PathLike[str] | None,
    *,
    prompt: PromptFn = input,
    secret_prompt: SecretPromptFn = getpass.getpass,
    out: Callable[[str], int] = sys.stdout.write,
) -> int:
    """Drive the interactive wizard. Returns shell exit code."""
    if hermes_home is None:
        hermes_home = Path.home() / ".hermes"
    home = Path(hermes_home)
    home.mkdir(parents=True, exist_ok=True)

    out("Sonzai Mind Layer — Hermes setup\n")
    out("--------------------------------\n")

    existing = load_config(home)

    api_key = os.environ.get("SONZAI_API_KEY") or existing.api_key
    if not api_key:
        api_key = secret_prompt("Sonzai API key (from https://sonz.ai/dashboard): ").strip()
    if not api_key:
        out("error: no API key provided.\n")
        return 1

    agent_name = (
        prompt(f"Agent name [{existing.agent_name or DEFAULT_AGENT_NAME}]: ").strip()
        or existing.agent_name
        or DEFAULT_AGENT_NAME
    )

    agent_id = prompt("Existing agent id [auto-provision]: ").strip() or None

    base_url = (
        prompt(f"Sonzai base URL [{existing.base_url or DEFAULT_BASE_URL}]: ").strip()
        or existing.base_url
        or DEFAULT_BASE_URL
    )

    memory_mode = (
        prompt(f"Memory mode (sync/async) [{existing.memory_mode or DEFAULT_MEMORY_MODE}]: ").strip()
        or existing.memory_mode
        or DEFAULT_MEMORY_MODE
    )
    if memory_mode not in ("sync", "async"):
        out(f"warning: unknown memory_mode '{memory_mode}', defaulting to sync.\n")
        memory_mode = "sync"

    if not agent_id:
        # Provision via idempotent create.
        try:
            config = SonzaiConfig(
                api_key=api_key,
                agent_name=agent_name,
                base_url=base_url,
                memory_mode=memory_mode,
            )
            client = build_client(config)
            agent_id = resolve_agent_id(client, config)
            client.close()
            out(f"✓ provisioned/resolved agent id: {agent_id}\n")
        except Exception as err:
            out(f"warning: agent provisioning failed ({err}). You can set SONZAI_AGENT_ID later.\n")
            agent_id = None

    save_values: dict[str, object] = {
        "agent_name": agent_name,
        "base_url": base_url,
        "memory_mode": memory_mode,
    }
    if agent_id:
        save_values["agent_id"] = agent_id

    save_config(save_values, home)
    out(f"✓ wrote {home / 'sonzai.json'}\n")

    if not os.environ.get("SONZAI_API_KEY"):
        out("\nNext step — add the API key to Hermes' .env:\n")
        out(f"  echo 'SONZAI_API_KEY={api_key}' >> {home / '.env'}\n")

    return 0


def run_health(
    hermes_home: str | os.PathLike[str] | None,
    *,
    out: Callable[[str], int] = sys.stdout.write,
) -> int:
    """Hit ``GET {base_url}/health`` and print the status code."""
    config = load_config(hermes_home)
    url = config.base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            out(f"{resp.status} {resp.reason} {url}\n")
            return 0 if resp.status == 200 else 1
    except urllib.error.HTTPError as err:
        out(f"{err.code} {err.reason} {url}\n")
        return 1
    except (urllib.error.URLError, OSError) as err:
        out(f"connection error: {err} ({url})\n")
        return 2
