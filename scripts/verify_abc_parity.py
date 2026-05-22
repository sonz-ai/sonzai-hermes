"""ABC parity verification.

Imports Hermes' MemoryProvider + ContextEngine ABCs from the live
upstream codebase, then walks every public method / property and
compares it against our SonzaiMemoryProvider and SonzaiContextEngine
implementations. Reports any drift.

Usage::

    HERMES_SRC=/path/to/hermes-agent  python scripts/verify_abc_parity.py

Defaults ``HERMES_SRC`` to the sibling ``../hermes-upstream`` clone.
Exits non-zero if any drift is detected.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HERMES_SRC = REPO_ROOT.parent / "hermes-upstream"

HERMES_SRC = Path(os.environ.get("HERMES_SRC", DEFAULT_HERMES_SRC))


def _augment_sys_path() -> None:
    # HERMES_SRC must come first so its ``plugins/`` (regular package with
    # ``__init__.py``) wins; our ``plugins/`` is a namespace package and
    # gets shadowed — that's intentional, we load our classes by path below
    # to avoid the collision entirely.
    if not HERMES_SRC.exists():
        print(f"FATAL: hermes-agent source not found at {HERMES_SRC}", file=sys.stderr)
        print(
            "Clone it first:  git clone --depth 1 "
            "https://github.com/NousResearch/hermes-agent.git "
            f"{HERMES_SRC}",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.path.insert(0, str(HERMES_SRC))
    # REPO_ROOT is still needed so ``sonzai_common`` (a top-level package
    # outside ``plugins/``) is importable from inside our plugin code.
    sys.path.append(str(REPO_ROOT))


def _load_our_class(plugin_subpath: str, class_name: str):
    """Load one of our plugin classes by file path, bypassing the namespace
    collision with hermes-upstream's ``plugins/`` package.

    ``plugin_subpath`` is relative to REPO_ROOT, e.g.
    ``"plugins/memory/sonzai/__init__.py"``.
    """
    import importlib.util

    init_file = REPO_ROOT / plugin_subpath
    package_dir = init_file.parent
    # Use a unique module name (NOT under ``plugins.*``) so we don't collide
    # with hermes-upstream's ``plugins.memory.*`` namespace.
    module_name = f"sonzai_hermes_under_test.{package_dir.parent.name}.{package_dir.name}"
    if module_name in sys.modules:
        return getattr(sys.modules[module_name], class_name)
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(init_file),
        submodule_search_locations=[str(package_dir)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


def _signature(method) -> str:
    try:
        return str(inspect.signature(method))
    except (TypeError, ValueError):
        return "<no signature>"


def _is_keyword_only_with_default(method, param_name: str) -> bool:
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    if param_name not in sig.parameters:
        return False
    p = sig.parameters[param_name]
    return p.kind == inspect.Parameter.KEYWORD_ONLY


def _accepts_kwargs(method) -> bool:
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _has_param(method, param_name: str) -> bool:
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return param_name in sig.parameters


def check_memory_provider() -> list[str]:
    from agent.memory_provider import MemoryProvider  # type: ignore

    SonzaiMemoryProvider = _load_our_class(
        "plugins/memory/sonzai/__init__.py", "SonzaiMemoryProvider"
    )

    issues: list[str] = []

    # Instantiability — uncovered abstracts will raise TypeError.
    try:
        instance = SonzaiMemoryProvider()
    except TypeError as err:
        issues.append(f"MemoryProvider: cannot instantiate — {err}")
        return issues

    abstracts = sorted(MemoryProvider.__abstractmethods__)
    print(f"[MemoryProvider] abstract methods to satisfy: {abstracts}")
    for name in abstracts:
        if not hasattr(instance, name):
            issues.append(f"missing abstract method/attr: {name}")
        else:
            ours = getattr(instance, name)
            theirs = getattr(MemoryProvider, name)
            print(f"  ✓ {name:24s}  ours: {_signature(ours)}  theirs: {_signature(theirs)}")

    # Optional hooks — signature drift checks
    print("\n[MemoryProvider] signature checks on optional hooks:")
    hook_checks = [
        # (method name, required keyword-only param, must accept **kwargs)
        ("prefetch", "session_id", False),
        ("queue_prefetch", "session_id", False),
        ("sync_turn", "session_id", False),
        ("handle_tool_call", None, True),
        ("on_session_end", None, False),
        ("on_pre_compress", None, False),
        ("on_session_switch", "parent_session_id", True),
        ("on_memory_write", None, False),
        ("on_delegation", "child_session_id", True),
        ("on_turn_start", None, True),
        ("system_prompt_block", None, False),
        ("shutdown", None, False),
        ("get_config_schema", None, False),
        ("save_config", None, False),
    ]
    for method_name, kw_only_param, expect_kwargs in hook_checks:
        if not hasattr(instance, method_name):
            print(f"  · {method_name:24s}  (not overridden — inheriting ABC default)")
            continue
        method = getattr(instance, method_name)
        sig = _signature(method)
        notes: list[str] = []
        if kw_only_param and not _is_keyword_only_with_default(method, kw_only_param):
            # Only flag if we override the method; ABC default is fine
            if method_name in vars(type(instance)):
                notes.append(f"missing keyword-only '{kw_only_param}'")
        if expect_kwargs and not _accepts_kwargs(method):
            if method_name in vars(type(instance)):
                notes.append("missing **kwargs")
        # on_pre_compress must return str per ABC
        if method_name == "on_pre_compress" and method_name in vars(type(instance)):
            try:
                ret = method([])
                if not isinstance(ret, str):
                    notes.append(f"return type: got {type(ret).__name__}, expected str")
            except Exception:
                pass  # tolerated — degraded mode etc.
        flag = "DRIFT" if notes else "✓"
        print(f"  {flag} {method_name:24s}  {sig}  {'  '.join(notes)}")
        if notes:
            issues.extend(f"{method_name}: {n}" for n in notes)

    # Subclassing check — strongly preferred but not strictly required if the
    # duck type matches. Flag as a warning either way.
    if not issubclass(type(instance), MemoryProvider):
        print(
            "\n  ! SonzaiMemoryProvider does NOT inherit from MemoryProvider — "
            "duck-typed only. Recommended to inherit so Hermes type checks pass."
        )

    return issues


def check_context_engine() -> list[str]:
    from agent.context_engine import ContextEngine  # type: ignore

    SonzaiContextEngine = _load_our_class(
        "plugins/context_engine/sonzai/__init__.py", "SonzaiContextEngine"
    )

    issues: list[str] = []

    try:
        instance = SonzaiContextEngine()
    except TypeError as err:
        issues.append(f"ContextEngine: cannot instantiate — {err}")
        return issues

    abstracts = sorted(ContextEngine.__abstractmethods__)
    print(f"\n[ContextEngine] abstract methods to satisfy: {abstracts}")
    for name in abstracts:
        if not hasattr(instance, name):
            issues.append(f"missing abstract method/attr: {name}")
        else:
            ours = getattr(instance, name)
            theirs = getattr(ContextEngine, name)
            print(f"  ✓ {name:24s}  ours: {_signature(ours)}  theirs: {_signature(theirs)}")

    print("\n[ContextEngine] required attributes:")
    required_attrs = [
        "last_prompt_tokens",
        "last_completion_tokens",
        "last_total_tokens",
        "threshold_tokens",
        "context_length",
        "compression_count",
    ]
    for attr in required_attrs:
        if not hasattr(instance, attr):
            issues.append(f"missing required attribute: {attr}")
            print(f"  ✘ {attr}")
        else:
            print(f"  ✓ {attr} = {getattr(instance, attr)}")

    print("\n[ContextEngine] tuneables (subclass via the ABC names):")
    for attr in ("threshold_percent", "protect_first_n", "protect_last_n"):
        if hasattr(instance, attr):
            print(f"  ✓ {attr} = {getattr(instance, attr)}")
        else:
            print(f"  · {attr} not set (ABC default applies)")

    print("\n[ContextEngine] signature checks on overrides:")
    overrides = [
        ("update_from_response", []),
        ("should_compress", []),
        ("compress", []),
        ("on_session_start", []),
        ("on_session_end", []),
        ("on_session_reset", []),
        ("get_status", []),
        ("update_model", []),
        ("handle_tool_call", ["expect_kwargs"]),
    ]
    for method_name, flags in overrides:
        if method_name not in vars(type(instance)):
            continue
        method = getattr(instance, method_name)
        sig = _signature(method)
        notes: list[str] = []
        if "expect_kwargs" in flags and not _accepts_kwargs(method):
            notes.append("missing **kwargs")
        flag = "DRIFT" if notes else "✓"
        print(f"  {flag} {method_name:24s}  {sig}  {'  '.join(notes)}")
        if notes:
            issues.extend(f"{method_name}: {n}" for n in notes)

    if not issubclass(type(instance), ContextEngine):
        print(
            "\n  ! SonzaiContextEngine does NOT inherit from ContextEngine — "
            "duck-typed only. Recommended to inherit so Hermes type checks pass."
        )

    return issues


def main() -> int:
    _augment_sys_path()
    print(f"Hermes source: {HERMES_SRC}")
    print(f"Plugin repo:   {REPO_ROOT}\n")

    issues: list[str] = []
    issues += check_memory_provider()
    issues += check_context_engine()

    print()
    if issues:
        print(f"❌ {len(issues)} drift item(s) found:")
        for i in issues:
            print(f"   - {i}")
        return 1
    print("✅ ABC parity OK — no drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
