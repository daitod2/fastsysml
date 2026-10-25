"""Python objects for the models returned by the Java bridge."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Union


class External:
    """A reference to an element of another model (for example, of the standard library)."""

    __slots__ = ("uri", "qualified_name")

    def __init__(self, uri: str, qualified_name: Optional[str]):
        self.uri = uri
        self.qualified_name = qualified_name

    def __repr__(self) -> str:
        return f"External({self.qualified_name or self.uri!r})"


class Unresolved:
    """A reference that is not resolved (not resolved yet, or not found), with its text in the source."""

    __slots__ = ("text",)

    def __init__(self, text: Optional[str]):
        self.text = text

    def __repr__(self) -> str:
        return f"Unresolved({self.text!r})"


Target = Union["Element", External, Unresolved, Any]


class Element:
    """An element of a model: its metaclass (type), owner, attributes, references and position."""

    __slots__ = ("model", "id", "type", "owner_id", "role", "line", "end_line", "offset", "length",
                 "attrs", "_refs", "_derived", "children")

    def __init__(self, model: "Model", data: Dict[str, Any]):
        self.model = model
        self.id: int = data["id"]
        self.type: str = data["type"]
        self.owner_id: Optional[int] = data.get("owner")
        self.role: Optional[str] = data.get("role")
        self.line: Optional[int] = data.get("line")
        self.end_line: Optional[int] = data.get("endLine")
        self.offset: Optional[int] = data.get("offset")
        self.length: Optional[int] = data.get("length")
        self.attrs: Dict[str, Any] = {k: v for k, v in data.items() if k not in _NON_ATTRIBUTES}
        self._refs = data.get("refs", {})
        self._derived = data.get("derived", {})
        self.children: List[Element] = []

    @property
    def owner(self) -> Optional["Element"]:
        return None if self.owner_id is None else self.model.elements[self.owner_id]

    @property
    def name(self) -> Optional[str]:
        """The declared name (not the effective name, which needs resolution: see derived)."""
        return self.attrs.get("declaredName")

    @property
    def short_name(self) -> Optional[str]:
        return self.attrs.get("declaredShortName")

    @property
    def refs(self) -> Dict[str, List[Target]]:
        """The (non-derived) references, as Elements of this model, External or Unresolved."""
        return {name: [self.model._target(value) for value in values] for name, values in self._refs.items()}

    def ref(self, name: str) -> Optional[Target]:
        """The first target of the given reference, if any."""
        values = self._refs.get(name)
        return self.model._target(values[0]) if values else None

    @property
    def derived(self) -> Dict[str, Any]:
        """The derived properties requested with parse(..., derived=[...])."""
        return {name: self.model._value(value) for name, value in self._derived.items()}

    @property
    def text(self) -> Optional[str]:
        """The source text of the element, if the source is known."""
        if self.model.source is None or self.offset is None or self.length is None:
            return None
        return self.model.source[self.offset:self.offset + self.length]

    def walk(self) -> Iterator["Element"]:
        """This element and all the elements it owns, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def __repr__(self) -> str:
        name = self.name
        return f"<{self.type}{' ' + name if name else ''} #{self.id}>"


_NON_ATTRIBUTES = {"id", "type", "owner", "role", "line", "endLine", "offset", "length", "refs", "derived"}


class Issue:
    """A syntax, linking or validation issue."""

    __slots__ = ("model", "severity", "message", "line", "column", "code", "element_id")

    def __init__(self, model: "Model", data: Dict[str, Any]):
        self.model = model
        self.severity: str = data["severity"]
        self.message: str = data["message"]
        self.line: Optional[int] = data.get("line")
        self.column: Optional[int] = data.get("column")
        self.code: Optional[str] = data.get("code")
        self.element_id: Optional[int] = data.get("element")

    @property
    def element(self) -> Optional[Element]:
        return None if self.element_id is None else self.model.elements[self.element_id]

    def __repr__(self) -> str:
        return f"Issue({self.severity} {self.line}:{self.column} {self.message!r})"


class Model:
    """A parsed (and possibly resolved and validated) SysML or KerML model."""

    def __init__(self, data: Dict[str, Any], source: Optional[str] = None, timing: Optional[Dict[str, float]] = None):
        self.uri: str = data["uri"]
        self.source = source
        self.timing = timing or {}
        self.elements: List[Element] = [Element(self, element) for element in data["elements"]]
        for element in self.elements:
            if element.owner_id is not None:
                self.elements[element.owner_id].children.append(element)
        self.roots: List[Element] = [self.elements[i] for i in data["roots"]]
        self.issues: List[Issue] = [Issue(self, issue) for issue in data["issues"]]

    @property
    def errors(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def find(self, type: Optional[str] = None, name: Optional[str] = None) -> List[Element]:
        """The elements of the given metaclass (exact name, e.g. "PartUsage") and/or declared name."""
        return [e for e in self.elements if (type is None or e.type == type) and (name is None or e.name == name)]

    def __iter__(self) -> Iterator[Element]:
        return iter(self.elements)

    def __len__(self) -> int:
        return len(self.elements)

    def __repr__(self) -> str:
        return f"<Model {self.uri} elements={len(self.elements)} issues={len(self.issues)}>"

    def _target(self, value: Any) -> Target:
        if isinstance(value, int):
            return self.elements[value]
        if isinstance(value, dict):
            if "unresolved" in value:
                return Unresolved(value["unresolved"])
            if "uri" in value:
                return External(value["uri"], value.get("qualifiedName"))
        return value

    def _value(self, value: Any) -> Any:
        # In derived values (unlike refs), an element of this model is {"id": n}.
        if isinstance(value, list):
            return [self._value(v) for v in value]
        if isinstance(value, dict):
            return self.elements[value["id"]] if "id" in value else self._target(value)
        return value
