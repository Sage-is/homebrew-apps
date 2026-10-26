#!/usr/bin/env bash
# Run every tests/test_*.py module as its own process, side by side, and fail
# if any module fails. A failing module's output is printed; a passing one stays quiet.
#
# Each module builds its own temp dirs and binds only port 0, so they do not collide.
# Keep it so: no fixed ports, no fixed paths outside a TemporaryDirectory.
set -uo pipefail

cd "$(dirname "$0")/../tests" || exit 1
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

modules=$(for f in test_*.py; do [ -e "$f" ] && echo "${f%.py}"; done)
[ -n "$modules" ] || { echo "FAIL: no tests/test_*.py modules found" >&2; exit 1; }

# xargs, not `&`: a background job in a script ignores SIGINT, and a child that
# starts with SIGINT ignored cannot trap it (the interrupted-copy tests send it).
export OUT="$out"
printf '%s\n' $modules | xargs -P "$(printf '%s\n' $modules | wc -l)" -I{} sh -c \
  'python3 -B -m unittest -q "$1" >"$OUT/$1.log" 2>&1; echo $? >"$OUT/$1.rc"' _ {}

failed=0
for m in $modules; do
  # A missing or non-zero status file counts as a failure: a killed module must not pass.
  if [ "$(cat "$out/$m.rc" 2>/dev/null || echo 1)" != 0 ]; then
    echo "FAIL: $m"
    cat "$out/$m.log"
    failed=1
  else
    printf '%s: %s\n' "$m" "$(tail -n 3 "$out/$m.log" | grep -E '^(Ran|OK)' | tr '\n' ' ')"
  fi
done
exit "$failed"
