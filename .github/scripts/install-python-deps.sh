#!/usr/bin/env bash
# Install the project and its development tooling for CI.
#
# Owned by platform (Claude). The *contents* of pyproject.toml are owned by
# application engineering (Codex); this script only consumes them. Contract:
#   * the project is installable from the repository root (pip install .);
#   * development tools (ruff, mypy, pytest, ...) are declared EITHER as a PEP 735
#     dependency group named "dev" ([dependency-groups] dev = [...]) OR as an
#     optional-dependency extra named "dev" ([project.optional-dependencies] dev).
# See docs/platform/ci.md.
set -euo pipefail

python -m pip install --upgrade pip

mode="$(python - <<'PY'
import tomllib
with open("pyproject.toml", "rb") as fh:
    data = tomllib.load(fh)
if "dev" in data.get("dependency-groups", {}):
    print("group")
elif "dev" in data.get("project", {}).get("optional-dependencies", {}):
    print("extra")
else:
    print("none")
PY
)"

case "$mode" in
  group)
    # PEP 735 groups need pip >= 25.1.
    python -m pip install -e . --group dev
    ;;
  extra)
    python -m pip install -e ".[dev]"
    ;;
  *)
    echo "::error file=pyproject.toml::No 'dev' dependency group or extra found. See docs/platform/ci.md for the CI contract."
    exit 1
    ;;
esac

python -m pip list --format=freeze
