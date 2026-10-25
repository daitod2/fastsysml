"""The semantic graph of models, for browsing: the elements a modeler sees (nodes), with the relationships between
them as edges, instead of the abstract syntax, where relationships are elements too.

In the abstract syntax, `part a : A;` is a PartUsage owned through an OwningMembership, owning a FeatureTyping
whose type is A. Here it is a node `a`, owned by its namespace, with an edge `typing` from a to A. Elements that
only carry structure (memberships, specializations, feature chains, expressions, multiplicities, documentation,
anonymous connector ends, ...) are hidden: their information goes to edges and to the attributes of the nodes. All
the elements stay available as they are (Graph.elements).

Nodes and edges are keyed by element URI (Element.uri, External.uri), so that models referencing each other, and
elements of the library got later, link up. A target that is not loaded is a stub node (Node.stub) with the
qualified name only.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

from ..model import Element, External, Model, Unresolved

# Specializations: the kind of edge, and the references to the general type (the first one present is used).
_SPECIALIZATION_KINDS = (
    ("FeatureTyping", "typing"),
    ("Subclassification", "subclassification"),
    ("Redefinition", "redefinition"),
    ("ReferenceSubsetting", "reference"),
    ("CrossSubsetting", "crossSubsetting"),
    ("Subsetting", "subsetting"),
    ("Specialization", "specialization"),
)
_GENERAL_REFS = ("type", "superclassifier", "redefinedFeature", "referencedFeature", "crossedFeature",
                 "subsettedFeature", "general")
# Connectors: the kind of the edges between their ends.
_CONNECTOR_KINDS = (
    ("InterfaceUsage", "interface"),
    ("AllocationUsage", "allocation"),
    ("Flow", "flow"),
    ("Succession", "succession"),
    ("BindingConnector", "binding"),
    ("Connector", "connection"),
)
# Usages referring to another one (perform a.b, satisfy r by x, ...): the kind of the edge from their owner (for
# satisfy, from the subject) to it.
_REFERRING_KINDS = (
    ("SatisfyRequirementUsage", "satisfy"),
    ("IncludeUseCaseUsage", "include"),
    ("ExhibitStateUsage", "exhibit"),
    ("PerformActionUsage", "perform"),
    ("AssertConstraintUsage", "assert"),
)

SPECIALIZATION_KINDS = tuple(kind for _, kind in _SPECIALIZATION_KINDS)
CONNECTOR_KINDS = tuple(kind for _, kind in _CONNECTOR_KINDS)
# The kinds of edge (relationships of other metaclasses get the name of their metaclass, e.g. "disjoining").
EDGE_KINDS = SPECIALIZATION_KINDS + CONNECTOR_KINDS + tuple(kind for _, kind in _REFERRING_KINDS) + (
    "conjugation", "end", "transition", "verify", "dependency", "import", "membership", "annotation", "value")


Kinds = Union[str, Sequence[str], None]


def _matches(edge: "Edge", kind: Kinds) -> bool:
    return kind is None or (edge.kind == kind if isinstance(kind, str) else edge.kind in kind)


class Node:
    """An element as a modeler sees it: its name, metaclass, owner (skipping memberships), and edges."""

    __slots__ = ("key", "metaclass", "name", "short_name", "qualified_name", "owner", "membership", "children",
                 "element", "attributes", "doc", "metadata", "value", "multiplicity", "out", "into", "stub")

    def __init__(self, key: str):
        self.key = key
        self.metaclass: Optional[str] = None
        self.name: Optional[str] = None  # The effective name (e.g. of `:>> mass`, mass).
        self.short_name: Optional[str] = None
        self.qualified_name: Optional[str] = None
        self.owner: Optional[Node] = None
        # The metaclass of the membership through which its owner owns it (e.g. "FeatureMembership").
        self.membership: Optional[str] = None
        self.children: List[Node] = []
        self.element: Optional[Element] = None
        # Non-derived attributes of the element (e.g. isAbstract, direction) and of its membership (visibility).
        self.attributes: Dict[str, Any] = {}
        self.doc: List[str] = []
        # Names of the metadata applied to it (e.g. ["Refinement"]).
        self.metadata: List[str] = []
        # The source text of its value expression (`= ...`) and of its multiplicity (`[...]`).
        self.value: Optional[str] = None
        self.multiplicity: Optional[str] = None
        self.out: List[Edge] = []
        self.into: List[Edge] = []
        # Only referenced, not loaded: it has its key and qualified name only.
        self.stub = True

    @property
    def label(self) -> str:
        """The name; for an anonymous element, what it redefines, references or is typed by."""
        if self.name:
            return self.name
        if self.short_name:
            return f"<{self.short_name}>"
        for kind, prefix in (("redefinition", ":>> "), ("reference", "::> "), ("typing", ": ")):
            for edge in self.out:
                if edge.kind == kind:
                    return prefix + edge.target_label
        ends = [edge.target_label for edge in self.out if edge.kind == "end"]
        if len(ends) >= 2:
            return " to ".join(ends)  # A connector or transition.
        if self.qualified_name:
            return self.qualified_name.rsplit("::", 1)[-1]
        if self.owner is None and self.metaclass == "Namespace":
            return self.model_uri.rsplit("/", 1)[-1]  # The root of a file.
        text = (self.attributes.get("body") or self.text or "").strip()
        if text:
            line = " ".join(text.split())
            return line if len(line) <= 40 else line[:39] + "…"
        return f"({self.metaclass or 'element'})"

    @property
    def model_uri(self) -> str:
        """The URI of the model (file) of the element."""
        return self.key.split("#", 1)[0]

    @property
    def line(self) -> Optional[int]:
        return self.element.line if self.element is not None else None

    @property
    def end_line(self) -> Optional[int]:
        return self.element.end_line if self.element is not None else None

    @property
    def text(self) -> Optional[str]:
        """The source text of the element."""
        return self.element.text if self.element is not None else None

    def edges_out(self, kind: Kinds = None) -> List["Edge"]:
        """The edges from this node (of a kind, or of kinds; all of them if None)."""
        return [edge for edge in self.out if _matches(edge, kind)]

    def edges_in(self, kind: Kinds = None) -> List["Edge"]:
        """The edges to this node."""
        return [edge for edge in self.into if _matches(edge, kind)]

    def targets(self, kind: Kinds = None) -> List["Node"]:
        return [edge.target for edge in self.edges_out(kind) if edge.target is not None]

    def sources(self, kind: Kinds = None) -> List["Node"]:
        return [edge.source for edge in self.edges_in(kind)]

    def walk(self) -> Iterator["Node"]:
        """This node and all the nodes it owns, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "metaclass": self.metaclass, "name": self.name, "label": self.label,
                "shortName": self.short_name, "qualifiedName": self.qualified_name,
                "owner": self.owner.key if self.owner is not None else None, "membership": self.membership,
                "attributes": self.attributes, "doc": self.doc, "metadata": self.metadata, "value": self.value,
                "multiplicity": self.multiplicity, "line": self.line, "endLine": self.end_line, "stub": self.stub}

    def __repr__(self) -> str:
        return f"<Node {self.metaclass or 'stub'} {self.qualified_name or self.label}>"


class Edge:
    """A relationship between nodes: its kind (EDGE_KINDS), source and target; via, the key of the element it
    comes from (the relationship, connector or usage, which may be hidden); and path, for a target given as a
    feature chain (e.g. "rearAxle.leftHalfAxle"), and source_path, likewise for the source (between the ends of
    a connector). An unresolved target is None, with text as written."""

    __slots__ = ("kind", "source", "target", "via", "path", "text", "source_path")

    def __init__(self, kind: str, source: Node, target: Optional[Node], via: str, path: Optional[str] = None,
                 text: Optional[str] = None, source_path: Optional[str] = None):
        self.kind = kind
        self.source = source
        self.target = target
        self.via = via
        self.path = path
        self.text = text
        self.source_path = source_path

    @property
    def source_label(self) -> str:
        return self.source_path or self.source.label

    @property
    def target_label(self) -> str:
        if self.path:
            return self.path
        if self.target is not None:
            return self.target.label
        return self.text or "?"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "source": self.source.key,
                "target": self.target.key if self.target is not None else None, "via": self.via,
                "path": self.path, "text": self.text, "sourcePath": self.source_path}

    def __repr__(self) -> str:
        return f"<Edge {self.source_label} -{self.kind}-> {self.target_label}>"


# A reference target as a node: (its key, or None if unresolved; the path of a feature chain; the text if
# unresolved).
_Target = Tuple[Optional[str], Optional[str], Optional[str]]


class Graph:
    """The nodes and edges of the models added (add_model), by key."""

    def __init__(self, metaclasses: Dict[str, Dict[str, Any]]):
        # {metaclass: itself and all its supertypes}
        self._supertypes: Dict[str, Set[str]] = {name: {name, *info["supertypes"]}
                                                 for name, info in metaclasses.items()}
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        # All the elements (including the hidden ones), by key.
        self.elements: Dict[str, Element] = {}
        # Hidden elements: the key of the node shown instead (their nearest visible owner, or the member of a
        # membership).
        self.redirects: Dict[str, str] = {}
        self.models: List[Model] = []
        # Nodes created and keys removed (stubs merged into the node of a redirect) since the last drain().
        self._created: List[Node] = []
        self._removed: List[str] = []

    def is_a(self, metaclass: Optional[str], general: str) -> bool:
        """Whether the metaclass is general or a subclass of it."""
        return metaclass is not None and general in self._supertypes.get(metaclass, ())

    def subclasses(self, general: str) -> Set[str]:
        """The metaclasses that are general or subclasses of it."""
        return {name for name, supertypes in self._supertypes.items() if general in supertypes}

    def node(self, key: str) -> Optional[Node]:
        return self.nodes.get(self.resolve(key))

    def resolve(self, key: str) -> str:
        """The key of the node shown for a key (itself, or for a hidden element, the node shown instead)."""
        seen = 0
        while key in self.redirects and seen < 100:
            key = self.redirects[key]
            seen += 1
        return key

    def drain(self) -> Tuple[List[Node], List[str]]:
        """The nodes created and the keys removed since the last call."""
        created, removed = self._created, self._removed
        self._created, self._removed = [], []
        return created, removed

    def add_model(self, model: Model) -> List[Node]:
        """Adds the elements of a model parsed with uris=True (or got by URI). The elements already added (e.g.
        of a smaller subtree got before) are replaced. Returns the nodes added or loaded."""
        if model.elements and model.elements[0].fragment is None:
            raise ValueError("the model has no element URIs: parse it with uris=True")
        keys = {e.uri for e in model.elements}
        if any(key in self.elements for key in keys):
            self._remove_edges(lambda edge: edge.via in keys)
        self.models.append(model)
        added = _Builder(self, model).build()
        for key in keys:
            self._merge_stub(key)
        return added

    def _node(self, key: str) -> Node:
        key = self.resolve(key)
        node = self.nodes.get(key)
        if node is None:
            node = self.nodes[key] = Node(key)
            self._created.append(node)
        return node

    def _merge_stub(self, key: str) -> None:
        """Moves the edges to a stub whose key is now redirected (e.g. a membership) to the node shown instead."""
        stub = self.nodes.get(key)
        target_key = self.resolve(key)
        if stub is None or not stub.stub or target_key == key:
            return
        target = self._node(target_key)
        for edge in stub.into:
            edge.target = target
            target.into.append(edge)
        for edge in stub.out:
            edge.source = target
            target.out.append(edge)
        for child in stub.children:
            child.owner = target
            target.children.append(child)
        del self.nodes[key]
        self._removed.append(key)

    def _add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        edge.source.out.append(edge)
        if edge.target is not None:
            edge.target.into.append(edge)

    def _remove_edges(self, predicate: Any) -> None:
        removed = [edge for edge in self.edges if predicate(edge)]
        if not removed:
            return
        ids = {id(edge) for edge in removed}
        self.edges = [edge for edge in self.edges if id(edge) not in ids]
        for edge in removed:
            edge.source.out = [e for e in edge.source.out if id(e) not in ids]
            if edge.target is not None:
                edge.target.into = [e for e in edge.target.into if id(e) not in ids]


def _name_stub(node: Node, qualified_name: Optional[str]) -> None:
    """Names a stub node from the qualified name of a reference to it."""
    if node.stub and node.qualified_name is None and qualified_name:
        node.qualified_name = qualified_name
        node.name = qualified_name.rsplit("::", 1)[-1].strip("'")


class _Builder:
    """Builds the nodes and edges of one model."""

    def __init__(self, graph: Graph, model: Model):
        self.graph = graph
        self.model = model
        self.is_a = graph.is_a
        # Elements that are not nodes: memberships and internal ones.
        self.hidden: Set[int] = set()
        # Elements that only carry structure, with all they own (e.g. a typing, an expression).
        self.internal: Set[int] = set()
        # Relationships handled by a rule of their owner (not to get the generic edges).
        self.handled: Set[int] = set()
        self.added: List[Node] = []

    # Visibility

    def _is_membership(self, e: Element) -> bool:
        return self.is_a(e.type, "Membership") and not self.is_a(e.type, "Type")

    def _is_internal(self, e: Element) -> bool:
        """Whether an element (with all it owns) only carries structure, and is not shown as a node."""
        t = e.type
        owner = e.owner
        named = bool(e.derived.get("name") or e.name or e.short_name)
        if t == "FeatureValue":
            return True  # (An owning membership of the value expression.)
        if self.is_a(t, "Relationship") and not self.is_a(t, "Type") and not self._is_membership(e):
            return t != "Dependency"  # Specializations, chainings, annotations, imports, ...
        if self.is_a(t, "Multiplicity") or t == "Documentation":
            return True
        if not named and (t == "Feature" or self.is_a(t, "Expression") and not self.is_a(t, "Usage")):
            return True
        if owner is not None and not (e.name or e.short_name):
            # Ends and parameters not declared: of connectors (connect a.b to c.d, referencing what they connect),
            # and implicit ones (e.g. the payload of a send, the source of a transition, named from the library).
            if self.is_a(owner.type, "EndFeatureMembership") or self.is_a(owner.type, "ParameterMembership") or \
                    owner.owner is not None and owner.owner.type == "TransitionUsage":
                specializations = [c.type for c in e.children if self.is_a(c.type, "Specialization")]
                if "ReferenceSubsetting" in specializations or not specializations:
                    return True
            # The succession of a transition (to its target state), handled with the transition.
            if t == "SuccessionAsUsage" and owner.owner is not None and owner.owner.type == "TransitionUsage":
                return True
        return False

    # Building

    def build(self) -> List[Node]:
        for root in self.model.roots:
            self._walk(root, self._outer_owner(), None, False)
        for e in self.model.elements:
            if e.id not in self.hidden:
                self._node_rules(e)
        for e in self.model.elements:
            if e.id not in self.handled and self.is_a(e.type, "Relationship") and not self.is_a(e.type, "Type"):
                self._relationship_rules(e)
        return self.added

    def _outer_owner(self) -> Optional[Node]:
        """For an element got by URI, its owner (skipping a membership), maybe a stub."""
        owner = self.model.owner
        if isinstance(owner, External):
            node = self.graph._node(owner.uri)
            _name_stub(node, owner.qualified_name)
            return node
        return None

    def _walk(self, e: Element, owner: Optional[Node], membership: Optional[Element], internal: bool) -> None:
        self.graph.elements[e.uri] = e
        internal = internal or self._is_internal(e)
        is_membership = self._is_membership(e)
        if internal:
            self.internal.add(e.id)
        node = None
        if internal or is_membership:
            self.hidden.add(e.id)
            members = [c for c in e.children if c.role == "ownedRelatedElement"]
            if is_membership and members:
                self.graph.redirects[e.uri] = members[0].uri  # (Or what is shown for it.)
            elif is_membership and e.ref("memberElement") is not None and not internal:
                key, _, _ = self._target(e.ref("memberElement"))
                if key is not None:
                    self.graph.redirects[e.uri] = key
            elif owner is not None:
                self.graph.redirects[e.uri] = owner.key
            if owner is not None:
                if internal:
                    self._attach(e, owner)
        else:
            node = self._fill(e, owner, membership)
        for child in e.children:
            self._walk(child, node or owner, e if is_membership else None, internal)

    def _fill(self, e: Element, owner: Optional[Node], membership: Optional[Element]) -> Node:
        self.graph.redirects.pop(e.uri, None)
        node = self.graph._node(e.uri)
        if node.owner is not None and node in node.owner.children:
            node.owner.children.remove(node)
        node.children = []
        node.stub = False
        node.element = e
        node.metaclass = e.type
        node.name = e.derived.get("name") or e.name
        node.short_name = e.short_name
        node.qualified_name = e.derived.get("qualifiedName")
        if node.qualified_name is None and node.name and owner is not None and owner.qualified_name:
            node.qualified_name = f"{owner.qualified_name}::{node.name}"
        node.attributes = {k: v for k, v in e.attrs.items() if k not in ("declaredName", "declaredShortName")}
        node.doc = []
        node.metadata = []
        node.value = node.multiplicity = None
        node.membership = membership.type if membership is not None else \
            self.model.membership if e.owner is None else None
        if membership is not None:
            node.attributes.update(membership.attrs)
        node.owner = owner
        if owner is not None:
            owner.children.append(node)
        self.added.append(node)
        return node

    def _attach(self, e: Element, owner: Node) -> None:
        """Puts the information of an internal element into its (visible) owner."""
        parent = e.owner
        if e.type == "Documentation" and e.attrs.get("body"):
            owner.doc.append(e.attrs["body"].strip())
        elif self.is_a(e.type, "Multiplicity") and parent is not None and self._is_membership(parent) and \
                parent.owner is owner.element:
            owner.multiplicity = e.text
        elif e.type == "FeatureValue" and parent is owner.element and e.children:
            owner.value = e.children[0].text
        elif e.type == "MetadataUsage":
            self._add_metadata(e, owner)

    def _add_metadata(self, e: Element, owner: Node) -> None:
        for typing in e.children:
            if self.is_a(typing.type, "FeatureTyping"):
                key, _, _ = self._target(typing.ref("type"))
                if key is not None:
                    owner.metadata.append(self.graph._node(key).label)

    # Targets

    def _target(self, target: Any) -> _Target:
        """A reference target (an element of the model, External or Unresolved) as a node key."""
        if isinstance(target, Element):
            if target.type == "Feature" and any(c.type == "FeatureChaining" for c in target.children):
                return self._chain(target)
            if self._is_membership(target):
                return self._target(target.ref("memberElement"))  # An imported membership: its member.
            if target.id in self.hidden:
                owner = self._visible(target)
                return (owner.key if owner is not None else None), None, None
            return target.uri, None, None
        if isinstance(target, External):
            if target.member_uri is not None and target.member_uri != target.uri:
                # A membership: its member (as for one of this model).
                self.graph.redirects.setdefault(target.uri, target.member_uri)
            node = self.graph._node(target.uri)
            _name_stub(node, target.qualified_name)
            return node.key, None, None
        if isinstance(target, Unresolved):
            return None, None, target.text
        return None, None, None

    def _chain(self, feature: Element) -> _Target:
        """A feature chain (a.b.c): its last feature, with the path."""
        key: Optional[str] = None
        names: List[str] = []
        for chaining in feature.children:
            if chaining.type == "FeatureChaining":
                key, _, text = self._target(chaining.ref("chainingFeature"))
                names.append(self.graph._node(key).label if key is not None else text or "?")
        path = ".".join(names)
        return (key, path, None) if key is not None else (None, None, path)

    def _visible(self, e: Optional[Element]) -> Optional[Node]:
        """The node of an element, or of its nearest visible owner."""
        while e is not None and e.id in self.hidden:
            e = e.owner
        if e is None:
            return self._outer_owner()
        return self.graph.nodes.get(e.uri)

    def _edge(self, kind: str, source: Optional[Node], target: Optional[_Target], via: Element,
              source_path: Optional[str] = None) -> None:
        if source is None or target is None:
            return
        key, path, text = target
        if key is None and text is None:
            return
        self.graph._add_edge(Edge(kind, source, self.graph._node(key) if key is not None else None, via.uri, path,
                                  text, source_path))

    def _referenced(self, feature: Element) -> Optional[_Target]:
        """What a feature references (::>), e.g. the end of a connector, or a performed action."""
        for child in feature.children:
            if child.type == "ReferenceSubsetting":
                return self._target(child.ref("referencedFeature") or child.ref("general"))
        return None

    def _value_target(self, feature: Element) -> Optional[_Target]:
        """The feature that the value of a feature refers to, if it is just a reference (= x or = a.b)."""
        for value in feature.children:
            if value.type != "FeatureValue":
                continue
            for expression in value.children:
                if expression.type != "FeatureReferenceExpression":
                    continue
                for membership in expression.children:
                    if membership.type == "Membership":
                        return self._target(membership.ref("memberElement"))
                    if membership.type == "OwningMembership" and membership.children:
                        return self._target(membership.children[0])
        return None

    # Rules for the nodes

    def _node_rules(self, e: Element) -> None:
        node = self.graph.nodes[e.uri]
        t = e.type
        if self.is_a(t, "Connector"):
            self._connector(e, node)
        if t == "TransitionUsage":
            self._transition(e, node)
        if t == "MetadataUsage" and node.owner is not None:
            self._add_metadata(e, node.owner)
        for metaclass, kind in _REFERRING_KINDS:
            if self.is_a(t, metaclass):
                self._referring(e, node, kind)
                break
        if self.is_a(t, "RequirementUsage") and e.owner is not None and \
                e.owner.type == "RequirementVerificationMembership":
            self._verify(e, node)

    def _connector(self, e: Element, node: Node) -> None:
        """Edges from a connector to its ends, and between its ends (from the first one to the others)."""
        kind = next(kind for metaclass, kind in _CONNECTOR_KINDS if self.is_a(e.type, metaclass))
        ends: List[_Target] = []
        for membership in e.children:
            if not self.is_a(membership.type, "EndFeatureMembership"):
                continue
            for end in membership.children:
                target = self._referenced(end)
                if target is None:
                    if end.id in self.hidden:
                        continue
                    target = (end.uri, None, None)  # A named end without a reference (end x : T;).
                if end.type == "FlowEnd" and target[0] is not None:
                    # flow p1.torque to ...: the end is p1, then the item feature (torque) it redefines.
                    item = self._flow_item(end)
                    if item is not None:
                        target = (target[0], f"{target[1] or self.graph._node(target[0]).label}.{item}", None)
                ends.append(target)
                self._edge("end", node, target, e)
        resolved = [end for end in ends if end[0] is not None]
        if len(resolved) >= 2:
            first = self.graph._node(resolved[0][0])
            for other in resolved[1:]:
                self._edge(kind, first, other, e, resolved[0][1])

    def _flow_item(self, end: Element) -> Optional[str]:
        for membership in end.children:
            for feature in membership.children if self.is_a(membership.type, "FeatureMembership") else ():
                for redefinition in feature.children:
                    if redefinition.type == "Redefinition":
                        key, _, text = self._target(redefinition.ref("redefinedFeature"))
                        return self.graph._node(key).label if key is not None else text
        return None

    def _transition(self, e: Element, node: Node) -> None:
        """Edges from a transition to its source and target, and from the source to the target."""
        source: Optional[_Target] = None
        target: Optional[_Target] = None
        for child in e.children:
            if child.type == "Membership" and source is None:
                source = self._target(child.ref("memberElement"))
                self.handled.add(child.id)
            for succession in child.children:
                if succession.type == "SuccessionAsUsage":
                    ends = [end for membership in succession.children
                            if self.is_a(membership.type, "EndFeatureMembership") for end in membership.children]
                    if len(ends) >= 2:
                        target = self._referenced(ends[-1])
        self._edge("end", node, source, e)
        self._edge("end", node, target, e)
        if source is not None and source[0] is not None:
            self._edge("transition", self.graph._node(source[0]), target, e)

    def _referring(self, e: Element, node: Node, kind: str) -> None:
        target = self._referenced(e)
        if target is None:
            return  # Not referring (e.g. perform action a { ... }, which declares a).
        source = node.owner
        if kind == "satisfy":
            # satisfy r by x: from x, the value of the subject.
            for membership in e.children:
                if membership.type == "SubjectMembership":
                    for subject in membership.children:
                        subject_target = self._value_target(subject)
                        if subject_target is not None and subject_target[0] is not None:
                            source = self.graph._node(subject_target[0])
        self._edge(kind, source, target, e)

    def _verify(self, e: Element, node: Node) -> None:
        """verify r (in the objective of a verification case): an edge from the case to r."""
        case = node.owner
        while case is not None and not (self.is_a(case.metaclass, "VerificationCaseUsage") or
                                        self.is_a(case.metaclass, "VerificationCaseDefinition")):
            case = case.owner
        self._edge("verify", case, self._referenced(e), e)

    # Rules for the relationships (that are not types: connectors are nodes)

    def _relationship_rules(self, e: Element) -> None:
        t = e.type
        if e.owner is not None and e.owner.id in self.internal:
            return  # Inside an internal element (an expression, a chain, ...).
        source = self._visible(e.owner)
        if t == "FeatureValue":
            if e.owner is not None:
                self._edge("value", source, self._value_target(e.owner), e)
        elif self._is_membership(e):
            if not self.is_a(t, "OwningMembership"):
                self._edge("membership", source, self._target(e.ref("memberElement")), e)  # e.g. an alias
        elif self.is_a(t, "Specialization"):
            kind = next(kind for metaclass, kind in _SPECIALIZATION_KINDS if self.is_a(t, metaclass))
            specific = self._target(e.ref("specific"))[0] if e.ref("specific") is not None else None
            if specific is not None:
                source = self.graph._node(specific)  # Also for one not owned by its specific type.
            for name in _GENERAL_REFS:
                if e.ref(name) is not None:
                    self._edge(kind, source, self._target(e.ref(name)), e)
                    break
        elif self.is_a(t, "Conjugation"):
            self._edge("conjugation", source, self._target(e.ref("originalType")), e)
        elif t == "Dependency":
            for client in e.refs.get("client", ()):
                key, _, _ = self._target(client)
                if key is not None:
                    for supplier in e.refs.get("supplier", ()):
                        self._edge("dependency", self.graph._node(key), self._target(supplier), e)
        elif self.is_a(t, "Import"):
            for name in ("importedNamespace", "importedMembership"):
                self._edge("import", source, self._target(e.ref(name)) if e.ref(name) is not None else None, e)
        elif t == "Annotation":
            target = self._target(e.ref("annotatedElement"))
            if source is not None and target[0] != source.key:  # Not just annotating its owner.
                self._edge("annotation", source, target, e)
        elif t != "FeatureChaining":
            kind = t[0].lower() + t[1:]
            for values in e.refs.values():
                for value in values:
                    target = self._target(value)
                    if source is not None and target[0] != source.key:
                        self._edge(kind, source, target, e)
