"""A workspace: models loaded for browsing, as a graph (graph.py) with search (search.py), getting the elements of
the library from the Java bridge when they are needed."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from .. import SysMLParser
from ..model import Issue, Model
from .graph import SPECIALIZATION_KINDS, Edge, Graph, Node
from .search import SearchIndex
from .translation import Translator

# Derived properties got for each element: its effective name (e.g. of `:>> mass`) and qualified name.
DERIVED = ("name", "qualifiedName")

Key = Union[str, Node]


class Workspace:
    """Models loaded (load, load_text) and the library elements they reference (got with fetch), as nodes and
    edges, with search and traversal.

        with Workspace() as ws:
            ws.load(["Vehicle.sysml"])
            [wheel] = ws.search("type:PartDefinition name:Wheel")
            for usage in ws.usages(wheel):
                print(usage.qualified_name)
            for edge in wheel.into:
                print(edge.kind, edge.source.label)
    """

    def __init__(self, parser: Optional[SysMLParser] = None, language: Optional[str] = None,
                 **parser_options: Any):
        """language: show the translations of this language (see translation.py), e.g. "ja"."""
        self._owns_parser = parser is None
        self.translator = Translator(language) if language else None
        self.parser = parser if parser is not None else SysMLParser(**parser_options)
        self.graph = Graph(self.parser.metaclasses())
        self.index = SearchIndex(self.graph, self.translator)
        self.models: List[Model] = []
        self.paths: List[str] = []

    # Loading

    def load(self, paths: Iterable[Union[str, os.PathLike]], validate: bool = True) -> List[Model]:
        """Loads files (together, so that they can reference each other, and the ones loaded before)."""
        paths = [os.fspath(path) for path in paths]
        models = self.parser.parse_files(paths, resolve="lazy", validate=validate, derived=DERIVED, keep=True,
                                         uris=True)
        self.paths += paths
        self._add(models)
        return models

    def load_text(self, text: str, name: str = "input.sysml", validate: bool = True) -> Model:
        [model] = self.parser.parse_many([{"text": text, "name": name}], resolve="lazy", validate=validate,
                                         derived=DERIVED, keep=True, uris=True)
        self._add([model])
        return model

    def open(self, paths: Iterable[Union[str, os.PathLike]], validate: bool = True) -> List[Model]:
        """Loads files instead of those loaded (and of the models loaded from text)."""
        paths = [os.fspath(path) for path in paths]
        self.parser.unload()
        self.graph = Graph(self.parser.metaclasses())
        self.index.close()
        self.index = SearchIndex(self.graph, self.translator)
        self.models, self.paths = [], []
        return self.load(paths, validate=validate) if paths else []

    def reload(self, validate: bool = True) -> List[Model]:
        """Loads the files again (after they changed). The models loaded from text are dropped."""
        return self.open(self.paths, validate=validate)

    def _add(self, models: Sequence[Model]) -> List[Node]:
        added: List[Node] = []
        for model in models:
            self.models.append(model)
            added += self.graph.add_model(model)
        self._sync(added)
        return added

    def _sync(self, added: List[Node]) -> None:
        created, removed = self.graph.drain()
        nodes = {id(node): node for node in added + created if node.key in self.graph.nodes}
        self.index.update(nodes.values(), removed)

    def fetch(self, key: Key) -> Optional[Node]:
        """Gets an element not loaded (a stub node, e.g. of the library) with all it owns. Returns its node, or
        None if it is not found."""
        key = self.graph.resolve(key.key if isinstance(key, Node) else key)
        [model] = self.parser.get([key], subtree=True, derived=DERIVED)
        if model is None:
            return None
        self.models.append(model)
        self._sync(self.graph.add_model(model))
        return self.graph.node(key)

    # Nodes

    def node(self, key: Key, fetch: bool = True) -> Optional[Node]:
        """The node of a key (an element URI); with fetch, gets it first if it is a stub."""
        node = key if isinstance(key, Node) else self.graph.node(key)
        if node is not None and node.stub and fetch:
            node = self.fetch(node.key) or node
        return node

    @property
    def roots(self) -> List[Node]:
        """The root nodes of the models loaded (one per file)."""
        roots = []
        for model in self.models:
            if model.owner is None:
                for root in model.roots:
                    node = self.graph.node(root.uri)
                    if node is not None:
                        roots.append(node)
        return roots

    @property
    def issues(self) -> List[Issue]:
        return [issue for model in self.models for issue in model.issues]

    def search(self, query: str, limit: Optional[int] = 100, offset: int = 0, stubs: bool = False) -> List[Node]:
        """The nodes matching a query (see SearchIndex.search), best first."""
        return [self.graph.nodes[key] for key in self.index.search(query, limit, offset, stubs)]

    def of_type(self, metaclass: str, subclasses: bool = True, stubs: bool = False) -> List[Node]:
        """The nodes of a metaclass (e.g. "PartUsage"), with subclasses of it."""
        metaclasses = self.graph.subclasses(metaclass) if subclasses else {metaclass}
        return [node for node in self.graph.nodes.values()
                if node.metaclass in metaclasses and (stubs or not node.stub)]

    def sql(self, query: str, *params: Any) -> List[Tuple[Any, ...]]:
        """Runs a query on the tables of the nodes and edges (see search.py)."""
        return self.index.sql(query, params)

    # Traversal

    def generals(self, node: Key, transitive: bool = True, kinds: Sequence[str] = SPECIALIZATION_KINDS
                 ) -> List[Node]:
        """What a node specializes (its types, superclassifiers, redefined and subsetted features, ...)."""
        return self._closure(self._node(node), lambda n: n.targets(kinds), transitive)

    def specializations(self, node: Key, transitive: bool = True,
                        kinds: Sequence[str] = ("subclassification", "redefinition", "subsetting",
                                                "specialization")) -> List[Node]:
        """What specializes a node (subclassifiers, redefining and subsetting features, ...)."""
        return self._closure(self._node(node), lambda n: n.sources(kinds), transitive)

    def usages(self, definition: Key, specializations: bool = False) -> List[Node]:
        """The features typed by a definition (also by its specializations, with specializations=True)."""
        definition = self._node(definition)
        types = [definition] + (self.specializations(definition, kinds=("subclassification", "specialization"))
                                if specializations else [])
        seen: Set[str] = set()
        result = []
        for type_ in types:
            for usage in type_.sources("typing"):
                if usage.key not in seen:
                    seen.add(usage.key)
                    result.append(usage)
        return result

    def neighborhood(self, node: Key, depth: int = 1, kinds: Optional[Sequence[str]] = None,
                     direction: str = "both", ownership: bool = False) -> Tuple[List[Node], List[Edge]]:
        """The nodes within depth edges of a node (of the given kinds; direction "out", "in" or "both"), and the
        edges between them. With ownership, owning is followed too (as edges of kind "owns")."""
        start = self._node(node)
        nodes: Dict[str, Node] = {start.key: start}
        edges: Dict[int, Edge] = {}
        frontier = [start]
        for _ in range(depth):
            next_frontier = []
            for current in frontier:
                for edge in self._edges(current, kinds, direction, ownership):
                    edges[id(edge)] = edge
                    for other in (edge.source, edge.target):
                        if other is not None and other.key not in nodes:
                            nodes[other.key] = other
                            next_frontier.append(other)
            frontier = next_frontier
        # The edges between the nodes found (also those not followed from the last ones).
        for current in nodes.values():
            for edge in self._edges(current, kinds, "out", ownership):
                if edge.target is not None and edge.target.key in nodes:
                    edges[id(edge)] = edge
        return list(nodes.values()), list(edges.values())

    def _edges(self, node: Node, kinds: Optional[Sequence[str]], direction: str, ownership: bool) -> List[Edge]:
        edges = []
        if direction in ("out", "both"):
            edges += [edge for edge in node.out if kinds is None or edge.kind in kinds]
        if direction in ("in", "both"):
            edges += [edge for edge in node.into if kinds is None or edge.kind in kinds]
        if ownership:
            if direction in ("out", "both"):
                edges += [Edge("owns", node, child, child.key) for child in node.children]
            if direction in ("in", "both") and node.owner is not None:
                edges.append(Edge("owns", node.owner, node, node.key))
        return edges

    def _node(self, key: Key) -> Node:
        node = self.node(key, fetch=False)
        if node is None:
            raise KeyError(key)
        return node

    @staticmethod
    def _closure(start: Node, step: Any, transitive: bool) -> List[Node]:
        result: List[Node] = []
        seen = {start.key}
        frontier = [start]
        while frontier:
            next_frontier = []
            for node in frontier:
                for other in step(node):
                    if other.key not in seen:
                        seen.add(other.key)
                        result.append(other)
                        next_frontier.append(other)
            frontier = next_frontier if transitive else []
        return result

    # Lifecycle

    def close(self) -> None:
        self.index.close()
        if self._owns_parser:
            self.parser.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
