#!/usr/bin/env bash
# Decide whether the Python CI jobs are mandatory for the checked-out revision.
#
# Prints "python=true" or "python=false" (append to $GITHUB_OUTPUT).
#
# Rules (fail closed):
#   * pyproject.toml present in the tested revision        -> python=true (mandatory).
#   * absent here but present in the parent revision       -> ERROR: the project file was
#     removed or renamed, and the jobs must not silently skip.
#   * parent revision cannot be resolved                   -> ERROR: state is unknown.
#   * absent here and absent in the parent (bootstrap only) -> python=false, with a warning.
#
# The parent (HEAD^1) is the base branch tip for pull_request (GitHub tests the merge
# ref refs/pull/N/merge) and merge_group commits, and the previous main commit for a push
# to main (squash merges only). The checkout must use fetch-depth >= 2.
set -euo pipefail

if [ -f pyproject.toml ]; then
  echo "python=true"
  exit 0
fi

if ! parent="$(git rev-parse -q --verify 'HEAD^1^{commit}')"; then
  echo "::error::Cannot resolve the parent revision, so whether Python CI is required is unknown. Refusing to skip it (is fetch-depth >= 2?)." >&2
  exit 1
fi

if git cat-file -e "${parent}:pyproject.toml" 2>/dev/null; then
  echo "::error::pyproject.toml exists on the base revision (${parent:0:12}) but is missing here. Python CI is mandatory and cannot be skipped." >&2
  exit 1
fi

echo "::warning::Bootstrap state: pyproject.toml is absent here and on the base revision; Python jobs are skipped. See docs/platform/ci.md." >&2
echo "python=false"
