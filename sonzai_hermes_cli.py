"""Stand-alone CLI for installing Sonzai's Hermes plugins.

Exposed as the ``sonzai-hermes`` console script via pyproject.toml.

Subcommands:
- ``install``  — drop the memory plugin into ``$HERMES_HOME/plugins/sonzai/``
                 (Hermes' supported user-install path) and, optionally,
                 drop the context engine into Hermes' bundled
                 ``plugins/context_engine/sonzai/`` (the only path Hermes
                 currently scans for engines).
- ``uninstall``— reverse the install.
- ``status``   — show which plugins are currently visible to Hermes.
- ``setup``    — re-entry for the interactive wizard (same as
                 ``hermes sonzai setup`` once Hermes is running).
- ``health``   — hit ``GET <base_url>/health``.

Why this exists: Hermes discovers memory providers from
``$HERMES_HOME/plugins/<name>/`` but context engines ONLY from its own
bundled ``plugins/context_engine/<name>/`` tree. The CLI handles both,
warns clearly about the context-engine placement touching the Hermes
install, and is idempotent (re-runs replace the staged copy in place).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from plugins.memory.sonzai.cli import run_health, run_setup


def _resolve_hermes_home(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".hermes"


def _resolve_hermes_install(arg: str | None) -> Path | None:
    """Locate the Hermes install directory (where its ``plugins/`` lives).

    Returns ``None`` if Hermes isn't importable and the user didn't pass
    ``--hermes-src``. Order:
    1. Explicit ``--hermes-src``
    2. Try ``import agent.context_engine`` and walk up to the repo root.
    """
    if arg:
        return Path(arg).expanduser().resolve()
    try:
        import agent.context_engine  # type: ignore
        return Path(agent.context_engine.__file__).resolve().parent.parent
    except ImportError:
        return None


def _plugin_source_dirs() -> tuple[Path, Path]:
    """Return (memory_src, context_engine_src) — directories shipped in the wheel."""
    # When pip-installed, these resolve under site-packages/plugins/.
    # When run from a checkout, they resolve under the repo's plugins/.
    here = Path(__file__).resolve().parent
    return (
        here / "plugins" / "memory" / "sonzai",
        here / "plugins" / "context_engine" / "sonzai",
    )


def _copy_plugin(src: Path, dst: Path, *, symlink: bool) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        dst.symlink_to(src, target_is_directory=True)
    else:
        shutil.copytree(src, dst)


def cmd_install(args: argparse.Namespace) -> int:
    hermes_home = _resolve_hermes_home(args.hermes_home)
    mem_src, ce_src = _plugin_source_dirs()

    if not mem_src.exists():
        print(f"error: memory plugin source not found at {mem_src}", file=sys.stderr)
        return 1

    targets_done: list[str] = []

    if not args.engine_only:
        # Memory: $HERMES_HOME/plugins/sonzai/  (user-install path)
        mem_dst = hermes_home / "plugins" / "sonzai"
        print(f"Installing memory plugin → {mem_dst}")
        _copy_plugin(mem_src, mem_dst, symlink=args.symlink)
        targets_done.append(f"memory:    {mem_dst}")

    if not args.memory_only:
        # Context engine: only loadable from Hermes' bundled tree.
        hermes_install = _resolve_hermes_install(args.hermes_src)
        if hermes_install is None:
            print(
                "warning: cannot locate Hermes install (set --hermes-src=/path/to/hermes-agent\n"
                "         or `pip install hermes-agent` so its packages are importable).\n"
                "         Context engine NOT installed; memory plugin install proceeded if requested.",
                file=sys.stderr,
            )
        else:
            ce_dst = hermes_install / "plugins" / "context_engine" / "sonzai"
            print(f"Installing context engine → {ce_dst}")
            print(
                "  ! This drops a file into the Hermes install tree because "
                "Hermes' loader does not scan a user dir for context engines. "
                "A `pip install --upgrade hermes-agent` will overwrite it; "
                "re-run `sonzai-hermes install` after upgrades."
            )
            _copy_plugin(ce_src, ce_dst, symlink=args.symlink)
            targets_done.append(f"engine:    {ce_dst}")

    if not targets_done:
        print("nothing installed.")
        return 1

    print("\n✓ installed:")
    for t in targets_done:
        print(f"  {t}")
    print()
    print("Next steps:")
    print("  1. export SONZAI_API_KEY=sk_...")
    print("  2. Activate in `~/.hermes/config.yaml`:")
    print("       memory:   { provider: sonzai }")
    print("       context:  { engine:   sonzai }")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    hermes_home = _resolve_hermes_home(args.hermes_home)
    removed = 0

    mem_dst = hermes_home / "plugins" / "sonzai"
    if mem_dst.exists() or mem_dst.is_symlink():
        if mem_dst.is_symlink() or mem_dst.is_file():
            mem_dst.unlink()
        else:
            shutil.rmtree(mem_dst)
        print(f"removed: {mem_dst}")
        removed += 1

    hermes_install = _resolve_hermes_install(args.hermes_src)
    if hermes_install is not None:
        ce_dst = hermes_install / "plugins" / "context_engine" / "sonzai"
        if ce_dst.exists() or ce_dst.is_symlink():
            if ce_dst.is_symlink() or ce_dst.is_file():
                ce_dst.unlink()
            else:
                shutil.rmtree(ce_dst)
            print(f"removed: {ce_dst}")
            removed += 1

    if removed == 0:
        print("nothing to remove.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    hermes_home = _resolve_hermes_home(args.hermes_home)
    mem_dst = hermes_home / "plugins" / "sonzai"
    print(f"$HERMES_HOME:      {hermes_home}")
    print(f"memory plugin:     {'installed at ' + str(mem_dst) if mem_dst.exists() else 'NOT installed'}")

    hermes_install = _resolve_hermes_install(args.hermes_src)
    if hermes_install is None:
        print("context engine:    (cannot locate Hermes install — pass --hermes-src or pip install hermes-agent)")
    else:
        ce_dst = hermes_install / "plugins" / "context_engine" / "sonzai"
        print(f"hermes install:    {hermes_install}")
        print(f"context engine:    {'installed at ' + str(ce_dst) if ce_dst.exists() else 'NOT installed'}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    return run_setup(hermes_home=_resolve_hermes_home(args.hermes_home))


def cmd_health(args: argparse.Namespace) -> int:
    return run_health(hermes_home=_resolve_hermes_home(args.hermes_home))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sonzai-hermes",
        description="Install + manage the Sonzai memory & context-engine plugins for Hermes.",
    )
    p.add_argument(
        "--hermes-home",
        help="Override $HERMES_HOME (default: env var or ~/.hermes).",
    )
    p.add_argument(
        "--hermes-src",
        help="Path to the Hermes install (where its plugins/ tree lives). "
             "If omitted, auto-detect via `import agent.context_engine`.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    inst = sub.add_parser("install", help="Install Sonzai plugins into Hermes' discovery paths.")
    inst.add_argument("--memory-only", action="store_true", help="Skip the context engine.")
    inst.add_argument("--engine-only", action="store_true", help="Skip the memory provider.")
    inst.add_argument(
        "--symlink",
        action="store_true",
        help="Symlink instead of copying — best for editable/dev installs.",
    )
    inst.set_defaults(func=cmd_install)

    uninst = sub.add_parser("uninstall", help="Reverse install.")
    uninst.set_defaults(func=cmd_uninstall)

    status = sub.add_parser("status", help="Show what's currently installed.")
    status.set_defaults(func=cmd_status)

    setup = sub.add_parser("setup", help="Interactive setup wizard.")
    setup.set_defaults(func=cmd_setup)

    health = sub.add_parser("health", help="Hit the Sonzai /health endpoint.")
    health.set_defaults(func=cmd_health)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
