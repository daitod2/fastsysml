#!/bin/sh
# Builds the improved pilot classes (src/fastsysml/java/patched) from the pilot implementation sources:
#   scripts/build_patched.sh <clone of SysML-v2-Pilot-Implementation>
# The clone must be at tag 2026-08 (version 0.62.0, the version of the jar) without other changes, e.g.:
#   git clone --depth 1 --branch 2026-08 https://github.com/Systems-Modeling/SysML-v2-Pilot-Implementation.git
# The patches (java/patches) are applied to it, and the changed and new files are compiled against the jar.
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
pilot=$(cd "$1" && pwd)
jar="${FASTSYSML_JAR:-$(PYTHONPATH="$here/src" python -c 'from fastsysml import _runtime; print(_runtime.ensure_installed() / _runtime.JAR_NAME)')}"
out="$here/src/fastsysml/java/patched"

git -C "$pilot" apply "$here/java/patches/pilot-0.62.0-resolve-perf.patch"
git -C "$pilot" apply "$here/java/patches/pilot-0.62.0-scope-memo.patch"
files=$( (git -C "$pilot" diff --name-only -- '*.java'; git -C "$pilot" ls-files --others --exclude-standard -- '*.java') \
	| sort -u | sed "s|^|$pilot/|")
rm -rf "$out"
# shellcheck disable=SC2086
javac -nowarn --release 21 -cp "$jar" -d "$out" $files
echo "built $out"
