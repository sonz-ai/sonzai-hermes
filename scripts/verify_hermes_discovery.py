"""End-to-end Hermes plugin-discovery verification.

This is the most important verification script — it doesn't import our
plugin classes directly. Instead it stages our plugin into the locations
Hermes ACTUALLY scans, then calls Hermes' own discovery + loader code
to confirm:

1. Memory provider discovery from ``$HERMES_HOME/plugins/<name>/`` works
   (the documented user-install path).
2. Memory provider's ``register(ctx)`` fires and returns an instance that
   responds to ``is_available``, ``initialize``, etc.
3. Context engine discovery from the bundled
   ``<hermes-install>/plugins/context_engine/<name>/`` works (the ONLY path
   Hermes' loader supports for engines today — there is no user-install
   fallback).
4. Both plugins, when discovered and instantiated by Hermes' code, are
   instances of the live ABCs (proves the ``isinstance`` check used by
   ``run_agent.py`` will succeed in production).

Run after ``scripts/verify_abc_parity.py`` + ``verify_lifecycle.py`` pass
to confirm the *install path* works end-to-end. No network calls.

Usage::

    HERMES_SRC=/path/to/hermes-agent  python scripts/verify_hermes_discovery.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HERMES_SRC = REPO_ROOT.parent / "hermes-upstream"
HERMES_SRC = Path(os.environ.get("HERMES_SRC", DEFAULT_HERMES_SRC))


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✘"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(f"FAILED: {label}" + (f" — {detail}" if detail else ""))


def _augment_sys_path(hermes_home: Path) -> None:
    if not HERMES_SRC.exists():
        print(f"FATAL: hermes-agent source not found at {HERMES_SRC}", file=sys.stderr)
        sys.exit(2)
    # hermes_home must be on the env so hermes_constants.get_hermes_home picks it up.
    os.environ["HERMES_HOME"] = str(hermes_home)
    sys.path.insert(0, str(HERMES_SRC))
    # ``sonzai_common`` lives in our repo and needs to be importable when the
    # plugin runs — replicate what a pip-install of ``sonzai-hermes`` would
    # do by adding REPO_ROOT to the path.
    sys.path.insert(0, str(REPO_ROOT))


def stage_memory_plugin(hermes_home: Path) -> Path:
    """Copy our memory plugin into the user-install location Hermes scans."""
    user_plugins = hermes_home / "plugins"
    user_plugins.mkdir(parents=True, exist_ok=True)
    target = user_plugins / "sonzai"
    if target.exists():
        shutil.rmtree(target)
    src = REPO_ROOT / "plugins" / "memory" / "sonzai"
    shutil.copytree(src, target)
    return target


def stage_context_engine_plugin() -> Path:
    """Copy our context engine into Hermes' bundled tree.

    Hermes does NOT have a user-install path for context engines —
    ``discover_context_engines`` only scans its bundled directory. To verify
    discovery works at all, drop our plugin where Hermes will see it.

    In production this is what the ``sonzai-hermes install`` command does
    (with an explicit warning that it touches the Hermes install tree).
    """
    bundled = HERMES_SRC / "plugins" / "context_engine"
    target = bundled / "sonzai"
    if target.exists():
        shutil.rmtree(target)
    src = REPO_ROOT / "plugins" / "context_engine" / "sonzai"
    shutil.copytree(src, target)
    return target


def check_memory_discovery() -> None:
    print("\n[memory provider] Hermes discovery + loader:")
    # Import Hermes' actual discovery code.
    from plugins.memory import (  # type: ignore
        discover_memory_providers,
        load_memory_provider,
    )
    from agent.memory_provider import MemoryProvider  # type: ignore

    providers = discover_memory_providers()
    names = {name for name, _, _ in providers}
    _check(
        "discover_memory_providers found 'sonzai' in user-install dir",
        "sonzai" in names,
        f"discovered: {sorted(names)}",
    )

    provider = load_memory_provider("sonzai")
    _check("load_memory_provider('sonzai') returned an instance", provider is not None)
    _check(
        "loaded instance is a MemoryProvider (ABC parity)",
        isinstance(provider, MemoryProvider),
        f"got {type(provider).__name__}",
    )
    _check(
        "instance.name == 'sonzai'",
        getattr(provider, "name", None) == "sonzai",
    )
    _check(
        "is_available() works without crashing",
        provider.is_available() in (True, False),
    )


def check_context_engine_discovery() -> None:
    print("\n[context engine] Hermes discovery + loader:")
    from plugins.context_engine import (  # type: ignore
        discover_context_engines,
        load_context_engine,
    )
    from agent.context_engine import ContextEngine  # type: ignore

    engines = discover_context_engines()
    names = {name for name, _, _ in engines}
    _check(
        "discover_context_engines found 'sonzai' in bundled tree",
        "sonzai" in names,
        f"discovered: {sorted(names)}",
    )

    engine = load_context_engine("sonzai")
    _check("load_context_engine('sonzai') returned an instance", engine is not None)
    _check(
        "loaded instance is a ContextEngine (ABC parity)",
        isinstance(engine, ContextEngine),
        f"got {type(engine).__name__}",
    )
    _check(
        "instance.name == 'sonzai'",
        getattr(engine, "name", None) == "sonzai",
    )
    # Required attributes per the ABC contract.
    for attr in (
        "last_prompt_tokens",
        "threshold_tokens",
        "context_length",
        "compression_count",
        "threshold_percent",
        "protect_first_n",
        "protect_last_n",
    ):
        _check(f"has required attr '{attr}'", hasattr(engine, attr))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        hermes_home = Path(td)
        _augment_sys_path(hermes_home)

        print(f"Hermes source:  {HERMES_SRC}")
        print(f"$HERMES_HOME:   {hermes_home}")
        print(f"Plugin repo:    {REPO_ROOT}")

        mem_dir = stage_memory_plugin(hermes_home)
        ctx_dir = stage_context_engine_plugin()
        print("\nStaged:")
        print(f"  memory plugin  → {mem_dir}")
        print(f"  context engine → {ctx_dir}")

        try:
            check_memory_discovery()
            check_context_engine_discovery()
        except AssertionError as err:
            print(f"\n❌ {err}")
            # Clean up the bundled-tree mutation regardless.
            if ctx_dir.exists():
                shutil.rmtree(ctx_dir)
            return 1
        finally:
            # ALWAYS clean up the bundled-tree mutation; user's clone shouldn't
            # be left with our plugin staged in it.
            if ctx_dir.exists():
                shutil.rmtree(ctx_dir)

    print("\n✅ Hermes discovery + loader OK — both plugins load through "
          "real Hermes code paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
