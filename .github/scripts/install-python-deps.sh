#!/usr/bin/env bash
# Create .venv and install the project with its development tooling, for CI.
#
# Owned by platform (Claude). The *contents* of pyproject.toml are owned by
# application engineering (Codex); this script only consumes them, following
# docs/platform-interface.md:
#   python -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
# The virtual environment lives at .venv because [tool.pyright] and the pre-commit
# hooks expect it there. A PEP 735 "dev" dependency group is also supported.
# See docs/platform/ci.md.
set -euo pipefail

python -m venv .venv
.venv/bin/python -m pip install --upgrade pip

mode="$(.venv/bin/python - <<'PY'
import tomllib
with open("pyproject.toml", "rb") as fh:
    data = tomllib.load(fh)
if "dev" in data.get("project", {}).get("optional-dependencies", {}):
    print("extra")
elif "dev" in data.get("dependency-groups", {}):
    print("group")
else:
    print("none")
PY
)"

case "$mode" in
  extra)
    .venv/bin/python -m pip install -e ".[dev]"
    ;;
  group)
    # PEP 735 groups need pip >= 25.1.
    .venv/bin/python -m pip install -e . --group dev
    ;;
  *)
    echo "::error file=pyproject.toml::No 'dev' extra or dependency group found. See docs/platform/ci.md for the CI contract."
    exit 1
    ;;
esac

.venv/bin/python -m pip list --format=freeze
