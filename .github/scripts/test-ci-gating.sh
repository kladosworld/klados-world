#!/usr/bin/env bash
# Scenario tests for ci-detect.sh and ci-gate.py: the fail-closed Python CI gating.
# Run by the workflow-lint job. Needs git and python3 only; uses throwaway repositories.
# ok/bad only print and count, so "A && ok || bad" is a safe if-then-else here.
# shellcheck disable=SC2015
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$(mktemp -d)"
trap 'rm -rf -- "$W"' EXIT
pass=0; fail=0
ok()  { echo "PASS  $*"; pass=$((pass+1)); }
bad() { echo "FAIL  $*"; fail=$((fail+1)); }
g() { git -c user.email=t@t -c user.name=t "$@"; }

# Build an origin repo whose PR merge commits mimic refs/pull/N/merge (parent 1 = base).
mkrepo() {  # $1 name, $2 base-has-pyproject (0/1), $3 head-has-pyproject (0/1)
  local o="$W/$1-origin"
  mkdir -p "$o" && cd "$o" && g init -q -b main && echo r > README.md && g add . && g commit -qm root
  echo x > a.txt && g add . && g commit -qm second
  if [ "$2" = 1 ]; then echo '[project]' > pyproject.toml && g add . && g commit -qm "base py"; fi
  g checkout -qb feature
  if [ "$3" = 1 ] && [ ! -f pyproject.toml ]; then echo '[project]' > pyproject.toml && g add . && g commit -qm "add py"; fi
  if [ "$3" = 0 ] && [ -f pyproject.toml ]; then g rm -q pyproject.toml && g commit -qm "remove py"; fi
  echo f > f.txt && g add . && g commit -qm feat
  g checkout -q main && g merge -q --no-ff --no-edit feature -m "merge ref" && g update-ref refs/pull/1/merge HEAD
  g reset -q --hard HEAD^1
  # Shallow clone at depth 2 of the merge ref, as actions/checkout does.
  local c="$W/$1-clone"; g init -q "$c" && cd "$c" && g fetch -q --depth=2 "file://$o" refs/pull/1/merge && g checkout -q FETCH_HEAD
}

run_detect() { (cd "$W/$1-clone" && bash "$SRC/ci-detect.sh" 2>/dev/null); }

mkrepo bootstrap 0 0
out=$(run_detect bootstrap); rc=$?
[ $rc = 0 ] && [ "$out" = "python=false" ] && ok "bootstrap (no pyproject on base or head) -> skip allowed" || bad "bootstrap rc=$rc out=$out"

mkrepo initial 0 1
out=$(run_detect initial); rc=$?
[ $rc = 0 ] && [ "$out" = "python=true" ] && ok "PR adding pyproject -> mandatory" || bad "initial rc=$rc out=$out"

mkrepo steady 1 1
out=$(run_detect steady); rc=$?
[ $rc = 0 ] && [ "$out" = "python=true" ] && ok "base and head have pyproject -> mandatory" || bad "steady rc=$rc out=$out"

mkrepo removed 1 0
out=$(run_detect removed); rc=$?
[ $rc != 0 ] && ok "PR removing pyproject -> detect FAILS (no silent skip)" || bad "removed rc=$rc out=$out"

# Parent unresolvable (depth=1 clone of a merge commit whose tree lacks pyproject).
c="$W/depth1"; g init -q "$c" && cd "$c" && g fetch -q --depth=1 "file://$W/bootstrap-origin" refs/pull/1/merge && g checkout -q FETCH_HEAD
out=$(bash "$SRC/ci-detect.sh" 2>/dev/null); rc=$?
[ $rc != 0 ] && ok "parent revision unknown -> detect FAILS closed" || bad "depth1 rc=$rc out=$out"

# Push to main after the application landed (squash commit removing pyproject).
o="$W/push"; mkdir -p "$o" && cd "$o" && g init -q -b main && echo '[project]' > pyproject.toml && g add . && g commit -qm a && g rm -q pyproject.toml && g commit -qm "squash: remove"
out=$(bash "$SRC/ci-detect.sh" 2>/dev/null); rc=$?
[ $rc != 0 ] && ok "push to main removing pyproject -> detect FAILS" || bad "push rc=$rc out=$out"

# Gate scenarios.
gate() { echo "$1" | PYTHON_REQUIRED="$2" python3 "$SRC/ci-gate.py" >/dev/null; }
n() { printf '{"detect":{"result":"%s"},"lint":{"result":"%s"},"typecheck":{"result":"%s"},"test":{"result":"%s"},"package":{"result":"%s"}}' "$@"; }
gate "$(n success success success success success)" true  && ok "gate: required + all success -> pass" || bad "gate all success"
gate "$(n success skipped skipped skipped skipped)" false && ok "gate: bootstrap + all skipped -> pass" || bad "gate bootstrap"
gate "$(n success skipped skipped skipped skipped)" true  && bad "gate required+skipped passed" || ok "gate: required + jobs skipped -> FAIL"
gate "$(n success success skipped success success)" true  && bad "gate one skipped passed" || ok "gate: required + one job skipped -> FAIL"
gate "$(n success success success cancelled success)" true && bad "gate cancelled passed" || ok "gate: required + cancelled -> FAIL"
gate "$(n success success failure success success)" true  && bad "gate failure passed" || ok "gate: required + failure -> FAIL"
gate "$(n failure skipped skipped skipped skipped)" ""    && bad "gate detect failure passed" || ok "gate: detect failed (empty output) -> FAIL"
gate "$(n success success success success success)" false && bad "gate bootstrap+ran passed" || ok "gate: bootstrap but jobs ran -> FAIL (inconsistent)"
gate '{"detect":{"result":"success"},"lint":{"result":"success"},"typecheck":{"result":"success"},"test":{"result":"success"}}' true && bad "gate missing job passed" || ok "gate: a Python job missing from needs -> FAIL"

echo; echo "RESULT: $pass passed, $fail failed"
[ "$fail" = 0 ]
