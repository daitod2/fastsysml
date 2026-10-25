#!/bin/sh
# Compiles the bridge (java/bridge) and the user-side improvements (java/userside) against the pilot
# implementation jar, into src/fastsysml/java/{bridge,userside}.
# The jar is FASTSYSML_JAR, or the one installed by "python -m fastsysml install".
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
jar="${FASTSYSML_JAR:-$(PYTHONPATH="$here/src" python -c 'from fastsysml import _runtime; print(_runtime.ensure_installed() / _runtime.JAR_NAME)')}"
out="$here/src/fastsysml/java"
for part in bridge userside; do
	rm -rf "$out/$part"
	javac -nowarn --release 21 -cp "$jar" -d "$out/$part" $(find "$here/java/$part/src" -name '*.java')
	echo "built $out/$part"
done
