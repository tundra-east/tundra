#!/usr/bin/env sh
# forces UTF-8 from outside the interpreter, because PYTHONUTF8 set inside a script
# is read too late to take effect and the JSON would be decoded as cp1252 on a Windows machine.
set -e

here=$(cd "$(dirname "$0")" && pwd)

script=$1
if [ -z "$script" ]; then
  echo "usage: tempo.sh <script-name> [args...]" >&2
  exit 2
fi
shift

target="$here/$script.py"
# a missing script must say so plainly:
# python's own error for a missing path reads like a broken install.
if [ ! -f "$target" ]; then
  echo "tempo: no such script: $script.py (check the name)" >&2
  exit 2
fi

# python3 first, because on a machine where `python` is Python 2 the UTF-8 flag does not exist.
# being on PATH is not enough: a Python 2 rejects -X utf8 and exits with its own code,
# which the caller would then read against a script's exit table for a run that never
# started. So each candidate is asked to accept the flag before it is used.
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -X utf8 -c "" >/dev/null 2>&1; then
      exec "$candidate" -X utf8 "$target" "$@"
    fi
  fi
done

echo "tempo: no python interpreter found on PATH (tried python3, python, py)" >&2
exit 2
