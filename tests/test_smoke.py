"""Smoke tests — ensure all packages and the Sonzai SDK import cleanly."""

import importlib


def test_sonzai_common_importable():
    importlib.import_module("plugins.memory.sonzai._common")


def test_memory_plugin_importable():
    mod = importlib.import_module("plugins.memory.sonzai")
    assert hasattr(mod, "SonzaiMemoryProvider")
    assert hasattr(mod, "register")


def test_context_engine_plugin_importable():
    mod = importlib.import_module("plugins.context_engine.sonzai")
    assert hasattr(mod, "SonzaiContextEngine")
    assert hasattr(mod, "register")


def test_sonzai_sdk_importable():
    importlib.import_module("sonzai")
