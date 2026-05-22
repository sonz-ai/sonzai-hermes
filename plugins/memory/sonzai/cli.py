"""``hermes sonzai setup`` wizard.

Mirrors ``src/setup.ts`` + ``src/cli.ts`` from ``sonzai-openclaw``:
- prompt for API key (or detect ``SONZAI_API_KEY``)
- if no key, **auto-trial** via ``POST /onboarding/trial`` so the user
  is up and running in one command
- ask for an existing ``agent_id`` or provision one
- ask ``sync``/``async`` memory mode
- write non-secret values via ``save_config``; secret goes to ``.env``

Also exposes ``hermes sonzai health`` and ``hermes sonzai claim``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from sonzai_common import (
    DEFAULT_AGENT_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_MEMORY_MODE,
    SonzaiConfig,
    TrialCapReachedError,
    build_client,
    generate_blurb,
    load_config,
    request_claim_link,
    request_trial_key,
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

    setup = sub.add_parser("setup", help="Interactive one-time setup (auto-trial if no key)")
    setup.set_defaults(func=_cmd_setup)

    health = sub.add_parser("health", help="Hit the Sonzai /health endpoint")
    health.set_defaults(func=_cmd_health)

    claim = sub.add_parser("claim", help="Print a fresh claim URL for the configured trial")
    claim.set_defaults(func=_cmd_claim)


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


def _cmd_claim(args: argparse.Namespace) -> int:
    return run_claim(
        hermes_home=getattr(args, "hermes_home", None),
        out=sys.stdout.write,
        open_browser=True,
    )


# ─── reusable, test-friendly entry points ──────────────────────────────────


def run_setup(
    hermes_home: str | os.PathLike[str] | None,
    *,
    prompt: PromptFn = input,
    secret_prompt: SecretPromptFn = getpass.getpass,
    out: Callable[[str], int] = sys.stdout.write,
    open_browser: bool = True,
    request_trial_key_fn: Callable[..., object] = request_trial_key,
) -> int:
    """Drive the interactive wizard. Returns shell exit code.

    Onboarding flow when no API key is set:
    1. Prompt: "No API key — provision a 14-day trial? [Y/n]"
    2. Yes (default): call ``POST /onboarding/trial`` — get key + agent_id +
       claim_url. Save trial metadata to ``sonzai.json``. Open claim URL in
       browser so the user can convert the trial to a permanent account.
    3. No / on trial-cap (429): fall back to manual paste.
    """
    if hermes_home is None:
        hermes_home = Path.home() / ".hermes"
    home = Path(hermes_home)
    home.mkdir(parents=True, exist_ok=True)

    out("Sonzai Mind Layer — Hermes setup\n")
    out("--------------------------------\n")

    existing = load_config(home)

    base_url = (
        prompt(f"Sonzai base URL [{existing.base_url or DEFAULT_BASE_URL}]: ").strip()
        or existing.base_url
        or DEFAULT_BASE_URL
    )

    agent_name = (
        prompt(f"Agent name [{existing.agent_name or DEFAULT_AGENT_NAME}]: ").strip()
        or existing.agent_name
        or DEFAULT_AGENT_NAME
    )

    memory_mode = (
        prompt(
            f"Memory mode (sync/async) [{existing.memory_mode or DEFAULT_MEMORY_MODE}]: "
        ).strip()
        or existing.memory_mode
        or DEFAULT_MEMORY_MODE
    )
    if memory_mode not in ("sync", "async"):
        out(f"warning: unknown memory_mode '{memory_mode}', defaulting to sync.\n")
        memory_mode = "sync"

    api_key = os.environ.get("SONZAI_API_KEY") or existing.api_key
    agent_id: str | None = None
    trial_meta: dict[str, str] | None = None

    if not api_key:
        out("\nNo Sonzai API key detected.\n")
        choice = prompt("Provision a 14-day trial now? [Y/n] ").strip().lower()
        if choice in ("", "y", "yes"):
            try:
                result = request_trial_key_fn(  # type: ignore[misc]
                    base_url,
                    agent_name=agent_name,
                    blurb=generate_blurb(agent_name),
                )
                api_key = result.api_key
                agent_id = result.agent_id
                trial_meta = {
                    "expires_at": result.trial_expires_at,
                    "claim_url": result.claim_url,
                    "tenant_id": result.tenant_id,
                }
                out(
                    f"\n✓ 14-day trial active (expires {result.trial_expires_at}). "
                    "Memory is live.\n"
                )
                out("  Claim before then to keep it permanent:\n")
                out(f"    {result.claim_url}\n")
                if open_browser:
                    try:
                        webbrowser.open(result.claim_url)
                    except Exception:
                        pass
            except TrialCapReachedError:
                out(
                    "\n⚠️  Daily trial pool exhausted. Falling back to manual paste — "
                    "or rerun tomorrow.\n"
                )
            except Exception as err:
                out(f"\n⚠️  Auto-onboarding failed ({err}). Falling back to manual paste.\n")

    if not api_key:
        api_key = secret_prompt(
            "Sonzai API key (from https://sonz.ai/dashboard, or press Enter to abort): "
        ).strip()
    if not api_key:
        out("error: no API key provided.\n")
        return 1

    if not agent_id:
        agent_id = prompt("Existing agent id [auto-provision]: ").strip() or None

    if not agent_id:
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
    if trial_meta is not None:
        save_values["_trial"] = trial_meta

    save_config(save_values, home)
    out(f"✓ wrote {home / 'sonzai.json'}\n")

    env_file = home / ".env"
    _write_env_key(env_file, "SONZAI_API_KEY", api_key)
    out(f"✓ wrote API key to {env_file} (chmod 600)\n")

    return 0


def _write_env_key(env_file: Path, key: str, value: str) -> None:
    """Idempotently set ``KEY=value`` in a dotenv-style file with 0600 perms.

    Removes any prior ``KEY=...`` line and appends the new one. Safe against
    accidental commit (chmod 600).
    """
    lines: list[str] = []
    if env_file.exists():
        try:
            lines = env_file.read_text().splitlines()
        except OSError:
            lines = []
    prefix = key + "="
    out_lines = [ln for ln in lines if not ln.strip().startswith(prefix)]
    out_lines.append(f"{key}={value}")
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(out_lines) + "\n")
    try:
        env_file.chmod(0o600)
    except OSError:
        pass


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


def run_claim(
    hermes_home: str | os.PathLike[str] | None,
    *,
    out: Callable[[str], int] = sys.stdout.write,
    open_browser: bool = False,
    request_claim_link_fn: Callable[..., object] = request_claim_link,
) -> int:
    """Print a fresh claim URL for the configured trial.

    Reads the API key from ``$SONZAI_API_KEY`` or the saved ``.env``; reads
    the base URL from saved config. Requires the key to be a trial key
    (regular keys will get a 400 from the server).
    """
    config = load_config(hermes_home)
    api_key = os.environ.get("SONZAI_API_KEY") or config.api_key
    # Also peek into <hermes_home>/.env so users don't have to source it.
    if not api_key and hermes_home is not None:
        env_file = Path(hermes_home) / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("SONZAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        out("error: no SONZAI_API_KEY found in env or .env. Run `sonzai-hermes setup` first.\n")
        return 1

    # Also surface the previously-issued claim URL if we have it cached.
    if hermes_home is not None:
        cfg_path = Path(hermes_home) / "sonzai.json"
        if cfg_path.exists():
            try:
                cached = json.loads(cfg_path.read_text()).get("_trial", {})
                if cached.get("claim_url"):
                    out(f"Cached claim URL (from setup):\n  {cached['claim_url']}\n")
            except (OSError, json.JSONDecodeError):
                pass

    try:
        result = request_claim_link_fn(config.base_url, api_key)  # type: ignore[misc]
    except Exception as err:
        out(f"error: claim-link request failed: {err}\n")
        return 1

    claim_url = getattr(result, "claim_url", None) or (
        result.get("claim_url") if isinstance(result, dict) else None
    )
    expires_at = getattr(result, "expires_at", None) or (
        result.get("expires_at") if isinstance(result, dict) else None
    )
    out("\nOpen this link to claim your trial into a real account:\n")
    out(f"  {claim_url}\n")
    if expires_at:
        out(f"  (link valid until {expires_at})\n")
    if open_browser and claim_url:
        try:
            webbrowser.open(claim_url)
        except Exception:
            pass
    return 0
