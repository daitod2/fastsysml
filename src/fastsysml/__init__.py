"""Fast SysML v2 / KerML parsing from Python, using the SysML v2 Pilot Implementation (Java) with the
performance improvements of java-baseline/ (patched classes, name resolution memoization and the library
name index), running in a long-lived JVM.

    from fastsysml import SysMLParser

    with SysMLParser() as parser:
        model = parser.parse("package P { part def A; part a : A; }")
        for part in model.find(type="PartUsage"):
            print(part.name, part.line)

        model = parser.parse_file("Vehicle.sysml", resolve="lazy", validate=True)
        print(model.errors)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from ._bridge import Bridge, BridgeError
from .model import Element, External, Issue, Model, Unresolved

__all__ = ["SysMLParser", "Model", "Element", "Issue", "External", "Unresolved", "BridgeError"]

RESOLVE_LEVELS = ("none", "lazy", "all")

Input = Union[str, os.PathLike, Dict[str, str]]


def _file_path(uri: str) -> Path:
    """The path of a file: URI (as written by EMF)."""
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(uri).path)
    if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return Path(path)


class SysMLParser:
    """A SysML/KerML parser backed by a long-lived Java process (start it once and reuse it).

    resolve (per call):
      "none": parse only (no library needed; fastest).
      "lazy": also resolve the references of the model (what the linker needs), against the library.
      "all":  also evaluate all derived properties while resolving (EcoreUtil.resolveAll, as the pilot
              implementation's own tools do).
    The library is loaded on the first call that needs it (or at startup with preload=True); with
    preresolve=True, it is also resolved at startup (slow, but required for the name tables to apply).

    mode: "patched" (improved pilot classes; default), "userside" (unchanged jar with the user-side
    improvements) or "plain" (unchanged pilot implementation).
    """

    def __init__(self, library: Optional[os.PathLike] = None, mode: str = "patched", tables: bool = False,
                 preload: bool = False, preresolve: bool = False, java: Optional[os.PathLike] = None,
                 jar: Optional[os.PathLike] = None, classes: Optional[os.PathLike] = None,
                 jvm_args: Sequence[str] = ("-Xss8m", "-Xmx4g")):
        self._bridge = Bridge(library=library, mode=mode, tables=tables, preload=preload, preresolve=preresolve,
                              java=java, jar=jar, classes=classes, jvm_args=jvm_args)

    @property
    def startup(self) -> Dict[str, Any]:
        """Startup information (mode and times in ms)."""
        return self._bridge.startup

    @property
    def library(self) -> Path:
        """The directory of the library used."""
        return Path(self._bridge.library)

    def parse(self, text: str, name: str = "input.sysml", **options: Any) -> Model:
        """Parses text. The extension of name (.sysml or .kerml) selects the language."""
        return self.parse_many([{"text": text, "name": name}], **options)[0]

    def parse_file(self, path: Union[str, os.PathLike], **options: Any) -> Model:
        """Parses a .sysml or .kerml file."""
        return self.parse_many([path], **options)[0]

    def parse_files(self, paths: Iterable[Union[str, os.PathLike]], **options: Any) -> List[Model]:
        """Parses files together, so that they can reference each other (one model per file)."""
        return self.parse_many(list(paths), **options)

    def parse_many(self, inputs: Sequence[Input], resolve: str = "none", validate: bool = False,
                   derived: Sequence[str] = (), positions: bool = True, keep: bool = False,
                   uris: bool = False) -> List[Model]:
        """Parses inputs (paths, or {"text": ..., "name": ...}) together.

        validate: also run the validation checks (implies loading the library).
        derived: names of derived properties to include for each element (e.g. ["qualifiedName", "type"]).
        positions: include line numbers and offsets.
        keep: keep the inputs loaded after the call (so that later calls can reference them), until unload().
        uris: include the URI of each element (Element.uri), to match references from other models to it.
        """
        if resolve not in RESOLVE_LEVELS:
            raise ValueError(f"resolve must be one of {RESOLVE_LEVELS}")
        requests: List[Dict[str, str]] = []
        sources: List[Optional[str]] = []
        for item in inputs:
            if isinstance(item, dict):
                requests.append({"text": item["text"], "name": item.get("name", "input.sysml")})
                sources.append(item["text"])
            else:
                path = Path(item)
                requests.append({"path": str(path)})
                try:
                    sources.append(path.read_text(encoding="utf-8"))
                except OSError:
                    sources.append(None)
        response = self._bridge.request("parse", inputs=requests, resolve=resolve, validate=validate,
                                        derived=list(derived), positions=positions, keep=keep, uris=uris)
        timing = response.get("timing", {})
        return [Model(data, source, timing) for data, source in zip(response["models"], sources)]

    def get(self, uris: Sequence[str], subtree: bool = False, derived: Sequence[str] = (),
            positions: bool = True) -> List[Optional[Model]]:
        """Gets elements by URI (External.uri or Element.uri), for example of the library or of inputs kept with
        keep=True (not of other files), with their references resolved. Each one is a Model whose root is the element (with subtree,
        also all the elements it owns), with its owner as Model.owner; None if it is not found."""
        response = self._bridge.request("get", uris=list(uris), subtree=subtree, derived=list(derived),
                                        positions=positions)
        models: List[Optional[Model]] = []
        for data in response["models"]:
            if data.get("missing"):
                models.append(None)
                continue
            source = None
            if data["uri"].startswith("file:"):
                try:
                    source = _file_path(data["uri"]).read_text(encoding="utf-8")
                except OSError:
                    pass
            models.append(Model(data, source))
        return models

    def metaclasses(self) -> Dict[str, Dict[str, Any]]:
        """The metaclasses, as {name: {"abstract": bool, "supertypes": [names, from the most general]}}."""
        return self._bridge.request("metaclasses")["metaclasses"]

    def load_library(self, resolve: bool = False) -> float:
        """Loads (and with resolve=True, resolves) the library now. Returns the time taken in ms."""
        return self._bridge.request("load_library", resolve=resolve)["ms"]

    def unload(self) -> int:
        """Unloads the inputs kept with keep=True. Returns their number."""
        return self._bridge.request("unload")["unloaded"]

    def stats(self) -> Dict[str, Any]:
        return self._bridge.request("stats")

    def close(self) -> None:
        self._bridge.close()

    def __enter__(self) -> "SysMLParser":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
