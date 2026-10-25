"""The Java bridge process: a long-running JVM with the SysML v2 Pilot Implementation, which receives requests
and returns models as JSON, one object per line on stdin/stdout."""

from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import _runtime

# The compiled Java classes: the bridge, and the improved pilot classes ("patched", put ahead of the jar on the
# classpath) or the user-side improvements ("userside", using the unchanged jar). See java/ for their sources.
JAVA_DIR = Path(__file__).resolve().parent / "java"

MODES = ("patched", "userside", "plain")


class BridgeError(RuntimeError):
    """An error reported by the Java bridge, or the bridge process failing."""

    def __init__(self, message: str, trace: Sequence[str] = (), stderr: str = ""):
        details = "".join(f"\n    at {line}" for line in trace)
        if stderr:
            details += "\n--- bridge stderr (last lines) ---\n" + stderr
        super().__init__(message + details)
        self.trace = list(trace)
        self.stderr = stderr


def _path(value: Optional[os.PathLike], env: str) -> Optional[Path]:
    if value is not None:
        return Path(value)
    if os.environ.get(env):
        return Path(os.environ[env])
    return None


def find_java(java: Optional[os.PathLike] = None) -> str:
    if java is not None:
        return str(java)
    if os.environ.get("FASTSYSML_JAVA"):
        return os.environ["FASTSYSML_JAVA"]
    if os.environ.get("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("java")
    if found is None:
        raise BridgeError("java not found: install a JDK/JRE 21 or later, or set FASTSYSML_JAVA or JAVA_HOME")
    return found


class Bridge:
    """Starts and talks to the bridge process. Requests are serialized (one at a time)."""

    def __init__(self, library: Optional[os.PathLike] = None, mode: str = "patched", tables: bool = False,
                 preload: bool = False, preresolve: bool = False, java: Optional[os.PathLike] = None,
                 jar: Optional[os.PathLike] = None, classes: Optional[os.PathLike] = None,
                 jvm_args: Sequence[str] = ("-Xss8m", "-Xmx4g")):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        jar = _path(jar, "FASTSYSML_JAR")
        library = _path(library, "FASTSYSML_LIBRARY")
        if jar is None or library is None:
            # The pilot implementation jar and the library (with .index.json), downloaded on first use.
            runtime = _runtime.ensure_installed()
            jar = jar or runtime / _runtime.JAR_NAME
            library = library or runtime / _runtime.LIBRARY_NAME
        if not jar.exists():
            raise BridgeError(f"pilot implementation jar not found: {jar}")
        bridge_classes = JAVA_DIR / "bridge"
        if not (bridge_classes / "org" / "sysmlperf" / "bridge" / "Bridge.class").exists():
            raise BridgeError(f"bridge classes not found: {bridge_classes} (see scripts/build_java.sh)")
        classpath: List[Path] = [bridge_classes]
        if mode != "plain":
            extra = _path(classes, "FASTSYSML_CLASSES") or JAVA_DIR / mode
            if not extra.exists():
                raise BridgeError(f"{mode} classes not found: {extra}")
            classpath.append(extra)
        classpath.append(jar)

        command = [find_java(java), *jvm_args, "-cp", os.pathsep.join(str(p) for p in classpath),
                   "org.sysmlperf.bridge.Bridge", "--library", str(library), "--mode", mode]
        if tables:
            command.append("--tables")
        if preresolve:
            command.append("--preresolve")
        elif preload:
            command.append("--preload")

        self.mode = mode
        self.library = library
        self._lock = threading.Lock()
        self._next_id = 0
        self._stderr: collections.deque = collections.deque(maxlen=200)
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        ready = self._read()
        if not ready.get("ready"):
            raise BridgeError(f"unexpected bridge startup message: {ready}")
        self.startup = ready

    def _drain_stderr(self) -> None:
        with self._process.stderr:
            for line in self._process.stderr:
                self._stderr.append(line.decode("utf-8", "replace").rstrip())

    def _stderr_tail(self) -> str:
        return "\n".join(list(self._stderr)[-30:])

    def _read(self) -> Dict[str, Any]:
        line = self._process.stdout.readline()
        if not line:
            self._process.wait()
            raise BridgeError(f"bridge process exited (code {self._process.returncode})", stderr=self._stderr_tail())
        return json.loads(line)

    def request(self, op: str, **params: Any) -> Dict[str, Any]:
        with self._lock:
            if self._process.poll() is not None:
                raise BridgeError("bridge process is not running", stderr=self._stderr_tail())
            self._next_id += 1
            message = dict(params, op=op, id=self._next_id)
            self._process.stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            response = self._read()
        if not response.get("ok"):
            raise BridgeError(response.get("error", "bridge error"), response.get("trace", ()))
        return response

    def close(self) -> None:
        with self._lock:
            if self._process.poll() is None:
                try:
                    self._process.stdin.write(b'{"op": "shutdown"}\n')
                    self._process.stdin.flush()
                    self._process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    self._process.kill()
                    self._process.wait()
            for stream in (self._process.stdin, self._process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
