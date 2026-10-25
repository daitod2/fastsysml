"""Translations of models for display: names and texts shown over the models, which are not changed (their names
are identifiers, referenced by name).

The translations of a language are JSON files in translations/<language>/:
- glossary.json: {"metaclasses": {"PartDefinition": "パート定義", ...}, "edges": {"typing": "型", ...}}
- dictionaries for models (any other file, in any folder):
    {"files": ["sysml/src/examples/Vehicle Example/*.sysml"],   # the models it applies to (the end of their path)
     "names": {"Vehicle": "車両", ...},                          # by declared name (or short name)
     "qualifiedNames": {"P::a": "...", ...},                     # for one element (over names)
     "texts": {"The original text": "訳", ...}}                  # documentation and comments (whitespace ignored)
"""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from .graph import Edge, Node

TRANSLATIONS = Path(__file__).resolve().parent / "translations"


def languages() -> List[str]:
    """The languages with translations."""
    return sorted(path.name for path in TRANSLATIONS.iterdir() if (path / "glossary.json").exists()) \
        if TRANSLATIONS.is_dir() else []


def _normalize(text: str) -> str:
    return " ".join(text.split())


class Dictionary:
    """The translations of the names and texts of some models (one file)."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self.path = path
        self.files: List[str] = data.get("files", [])
        self.names: Dict[str, str] = data.get("names", {})
        self.qualified_names: Dict[str, str] = data.get("qualifiedNames", {})
        self.texts: Dict[str, str] = {_normalize(k): v for k, v in data.get("texts", {}).items()}

    def applies_to(self, file: str) -> bool:
        """Whether it applies to a model file (its path, with /)."""
        return any(fnmatchcase(file, pattern) or fnmatchcase(file, "*/" + pattern) for pattern in self.files)


class Translator:
    """The translations of a language: the glossary, and the dictionaries of the models (by the path of their
    files). Without a translation, the original is shown."""

    def __init__(self, language: str, directory: Optional[Path] = None):
        directory = directory if directory is not None else TRANSLATIONS / language
        glossary = directory / "glossary.json"
        if not glossary.exists():
            raise ValueError(f"no translations for {language!r} (available: {', '.join(languages()) or 'none'})")
        self.language = language
        self.glossary: Dict[str, Dict[str, str]] = json.loads(glossary.read_text(encoding="utf-8"))
        self.dictionaries = [Dictionary(json.loads(path.read_text(encoding="utf-8")), path)
                             for path in sorted(directory.rglob("*.json")) if path != glossary]
        self._by_model: Dict[str, List[Dictionary]] = {}

    def term(self, name: Optional[str]) -> Optional[str]:
        """A metaclass or edge kind."""
        if name is None:
            return None
        return self.glossary.get("metaclasses", {}).get(name) or self.glossary.get("edges", {}).get(name) or name

    def _dictionaries(self, node: Node) -> List[Dictionary]:
        model = node.model_uri
        found = self._by_model.get(model)
        if found is None:
            file = unquote(urlparse(model).path) if ":" in model else model
            found = self._by_model[model] = [d for d in self.dictionaries if d.applies_to(file)]
        return found

    def word(self, node: Node, name: str) -> str:
        """A name in the context of a node (e.g. a segment of a feature chain)."""
        for dictionary in self._dictionaries(node):
            if name in dictionary.names:
                return dictionary.names[name]
        return name

    def name(self, node: Node) -> Optional[str]:
        """The translation of the name of a node, if any."""
        for dictionary in self._dictionaries(node):
            if node.qualified_name and node.qualified_name in dictionary.qualified_names:
                return dictionary.qualified_names[node.qualified_name]
            for name in (node.name, node.short_name):
                if name and name in dictionary.names:
                    return dictionary.names[name]
        return None

    def text(self, node: Node, text: str) -> str:
        """A text (documentation, comment) of a node."""
        key = _normalize(text)
        for dictionary in self._dictionaries(node):
            if key in dictionary.texts:
                return dictionary.texts[key]
        return text

    def path(self, node: Node, path: str) -> str:
        """A feature chain (a.b.c), in the context of a node."""
        return ".".join(self.word(node, segment) for segment in path.split("."))

    def target_label(self, edge: Edge, depth: int = 0) -> str:
        if edge.path:
            return self.path(edge.source, edge.path)
        if edge.target is not None:
            return self.label(edge.target, depth + 1)
        return edge.text or "?"

    def source_label(self, edge: Edge, depth: int = 0) -> str:
        if edge.source_path:
            return self.path(edge.source, edge.source_path)
        return self.label(edge.source, depth + 1)

    def label(self, node: Node, depth: int = 0) -> str:
        """The label of a node (as Node.label), translated."""
        name = self.name(node)
        if name:
            return name
        if node.name or node.short_name or depth > 4:
            return node.label
        for kind, prefix in (("redefinition", ":>> "), ("reference", "::> "), ("typing", ": ")):
            for edge in node.out:
                if edge.kind == kind:
                    return prefix + self.target_label(edge, depth)
        ends = [self.target_label(edge, depth) for edge in node.out if edge.kind == "end"]
        if len(ends) >= 2:
            return " → ".join(ends)
        if node.qualified_name:
            return self.word(node, node.qualified_name.rsplit("::", 1)[-1])
        body = node.attributes.get("body")
        if body:
            line = " ".join(self.text(node, body).split())
            return line if len(line) <= 40 else line[:39] + "…"
        return node.label
