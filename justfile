set shell := ["bash", "-cu"]

# List recipes
default:
    @just --list

# Bump patch (x.y.Z+1) from pyproject.toml and deploy.
patch:
    just deploy $(just _next patch)

# Bump minor (x.Y+1.0) from pyproject.toml and deploy.
minor:
    just deploy $(just _next minor)

# Bump major (X+1.0.0) from pyproject.toml and deploy.
major:
    just deploy $(just _next major)

# Full release: preflight, test, bump, build, verify, commit, push, publish, tag, gh release.
# Publishes via twine (reads ~/.pypirc). Requires: uv, twine, gh (authenticated).
# Usage: just deploy 0.1.1
deploy VERSION:
    @just _preflight {{VERSION}}
    @just _test
    @just _bump {{VERSION}}
    @just _build
    @just _verify-wheel {{VERSION}}
    @just _commit {{VERSION}}
    git push origin main
    @just _publish {{VERSION}}
    @just _tag {{VERSION}}
    @just _release {{VERSION}}
    @echo "✓ Released v{{VERSION}}"

_preflight VERSION:
    @just _validate-version {{VERSION}}
    @just _check-clean
    @just _check-main
    @just _check-tag-free {{VERSION}}

_validate-version VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! [[ "{{VERSION}}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "error: VERSION must match X.Y.Z (got: {{VERSION}})" >&2
      exit 1
    fi

_check-clean:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "error: working tree is dirty; commit or stash first" >&2
      git status --short
      exit 1
    fi

_check-main:
    #!/usr/bin/env bash
    set -euo pipefail
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [[ "$branch" != "main" ]]; then
      echo "error: must be on main (current: $branch)" >&2
      exit 1
    fi

_check-tag-free VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    if git rev-parse --verify --quiet "v{{VERSION}}" >/dev/null; then
      echo "error: local tag v{{VERSION}} already exists" >&2
      exit 1
    fi
    git fetch origin --tags --quiet
    if git ls-remote --tags origin "refs/tags/v{{VERSION}}" | grep -q .; then
      echo "error: remote tag v{{VERSION}} already exists on origin" >&2
      exit 1
    fi

# Tests honor pytest's `addopts = -m 'not integration'`, so the live-API
# tests don't gate the release — run them explicitly with `pytest -m integration`.
_test:
    uv run --extra dev pytest

_bump VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    perl -pi -e 's/^version = "[^"]+"/version = "{{VERSION}}"/' pyproject.toml
    echo "bumped to {{VERSION}}"

_build:
    rm -rf dist
    uv build

# Make sure both plugin trees and the CLI shipped in the wheel before we tag.
# Bundled layout: each plugin owns its own ``_common`` subpackage, no
# top-level shared module — verify both ``_common/__init__.py`` files
# made it into the wheel.
_verify-wheel VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    whl="dist/sonzai_hermes-{{VERSION}}-py3-none-any.whl"
    if [[ ! -f "$whl" ]]; then echo "error: $whl not found" >&2; exit 1; fi
    python3 -m zipfile -l "$whl" | grep -q "plugins/memory/sonzai/provider.py"
    python3 -m zipfile -l "$whl" | grep -q "plugins/memory/sonzai/_common/__init__.py"
    python3 -m zipfile -l "$whl" | grep -q "plugins/memory/sonzai/_common/onboarding.py"
    python3 -m zipfile -l "$whl" | grep -q "plugins/context_engine/sonzai/engine.py"
    python3 -m zipfile -l "$whl" | grep -q "plugins/context_engine/sonzai/_common/__init__.py"
    python3 -m zipfile -l "$whl" | grep -q "sonzai_hermes_cli.py"
    echo "✓ wheel contents OK"

_commit VERSION:
    git add pyproject.toml
    git commit -m "release: v{{VERSION}}"

_publish VERSION:
    twine upload --non-interactive dist/sonzai_hermes-{{VERSION}}*

_tag VERSION:
    git tag -a v{{VERSION}} -m "Release v{{VERSION}}"
    git push origin v{{VERSION}}

_release VERSION:
    gh release create v{{VERSION}} --title "v{{VERSION}}" --generate-notes

# Print current version from pyproject.toml.
_current:
    #!/usr/bin/env bash
    set -euo pipefail
    grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/^version = "([^"]+)"/\1/'

# Compute next version from current by bumping patch|minor|major.
_next LEVEL:
    #!/usr/bin/env bash
    set -euo pipefail
    current=$(just _current)
    IFS=. read -r MAJ MIN PAT <<< "$current"
    case "{{LEVEL}}" in
      patch) PAT=$((PAT+1)) ;;
      minor) MIN=$((MIN+1)); PAT=0 ;;
      major) MAJ=$((MAJ+1)); MIN=0; PAT=0 ;;
      *) echo "error: LEVEL must be patch|minor|major (got {{LEVEL}})" >&2; exit 1 ;;
    esac
    echo "${MAJ}.${MIN}.${PAT}"
