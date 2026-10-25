"""Search over the nodes of a graph, and SQL access to them, with an in-memory SQLite database (standard library).

Tables (for Workspace.sql):
  nodes(key, metaclass, name, short_name, qualified_name, owner, model, line, end_line, doc, metadata, value,
        multiplicity, stub, name_tr, doc_tr): name_tr and doc_tr are the translations (see translation.py)
  edges(kind, source, target, via, path, text)
  metaclasses(metaclass, general): every metaclass with each of its supertypes and itself.
"""

from __future__ import annotations

import re
import shlex
import sqlite3
import threading
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from .graph import Graph, Node
from .translation import Translator

_SCHEMA = """
CREATE TABLE nodes (key TEXT PRIMARY KEY, metaclass TEXT, name TEXT, short_name TEXT, qualified_name TEXT,
                    owner TEXT, model TEXT, line INTEGER, end_line INTEGER, doc TEXT, metadata TEXT, value TEXT,
                    multiplicity TEXT, stub INTEGER, name_tr TEXT, doc_tr TEXT);
CREATE INDEX nodes_metaclass ON nodes (metaclass);
CREATE INDEX nodes_owner ON nodes (owner);
CREATE TABLE edges (kind TEXT, source TEXT, target TEXT, via TEXT, path TEXT, text TEXT);
CREATE INDEX edges_source ON edges (source);
CREATE INDEX edges_target ON edges (target);
CREATE TABLE metaclasses (metaclass TEXT, general TEXT);
CREATE INDEX metaclasses_general ON metaclasses (general);
"""
# The searched text of each node (substrings, with the trigram tokenizer).
_FTS = "CREATE VIRTUAL TABLE nodes_text USING fts5(key UNINDEXED, name, qualified_name, doc, metadata, value, " \
       "name_tr, doc_tr, tokenize = 'trigram')"
# The columns searched for a word (without the index).
_TEXT_COLUMNS = ("name", "short_name", "qualified_name", "doc", "metadata", "value", "name_tr", "doc_tr")

# Fields of a query (field:value); other words are searched in the text.
FIELDS = ("type", "metaclass", "name", "in", "meta", "file")


class SearchIndex:
    """The nodes and edges of a graph in SQLite, kept up to date with update(). With a translator, the
    translations of the names and texts are searched too."""

    def __init__(self, graph: Graph, translator: Optional[Translator] = None):
        self.graph = graph
        self.translator = translator
        self._lock = threading.Lock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.executescript(_SCHEMA)
        try:
            self._db.execute(_FTS)
            self.has_fts = True
        except sqlite3.OperationalError:  # SQLite without FTS5 or the trigram tokenizer (before 3.34).
            self.has_fts = False
        # The kinds of the edges (also fields of a query).
        self.kinds: Set[str] = set()
        self._db.executemany("INSERT INTO metaclasses VALUES (?, ?)",
                             [(name, general) for name, generals in graph._supertypes.items()
                              for general in generals])

    def update(self, nodes: Iterable[Node], removed: Iterable[str] = ()) -> None:
        """Adds or updates nodes, removes the keys removed, and replaces all the edges."""
        tr = self.translator
        rows = [(n.key, n.metaclass, n.name, n.short_name, n.qualified_name,
                 n.owner.key if n.owner is not None else None, n.model_uri, n.line, n.end_line,
                 "\n".join(n.doc) or None, " ".join(n.metadata) or None, n.value, n.multiplicity, int(n.stub),
                 tr.name(n) if tr is not None else None,
                 ("\n".join(tr.text(n, doc) for doc in n.doc) or None) if tr is not None else None)
                for n in nodes]
        keys = [(row[0],) for row in rows] + [(key,) for key in removed]
        with self._lock, self._db:
            self._db.executemany("DELETE FROM nodes WHERE key = ?", keys)
            self._db.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            if self.has_fts:
                self._db.executemany("DELETE FROM nodes_text WHERE key = ?", keys)
                self._db.executemany("INSERT INTO nodes_text VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                     [(row[0], row[2], row[4], row[9], row[10], row[11], row[14], row[15])
                                      for row in rows])
            self.kinds = {e.kind for e in self.graph.edges}
            self._db.execute("DELETE FROM edges")
            self._db.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)",
                                 [(e.kind, e.source.key, e.target.key if e.target is not None else None, e.via,
                                   e.path, e.text) for e in self.graph.edges])

    def sql(self, query: str, params: Sequence[Any] = ()) -> List[Tuple[Any, ...]]:
        with self._lock:
            return self._db.execute(query, params).fetchall()

    def search(self, query: str, limit: Optional[int] = 100, offset: int = 0, stubs: bool = False) -> List[str]:
        """The keys of the nodes matching a query, best first. A query is words (all of them must be in the name,
        qualified name, documentation, metadata, value or their translations; * is a wildcard) and fields:
          type:PartUsage   of the metaclass or a subclass (metaclass:PartUsage: exactly)
          name:wheel       named so, or translated so (case-insensitive; * is a wildcard)
          in:Vehicle::Parts   inside the namespace with this qualified name (or ending with it)
          meta:Safety      with this metadata
          file:Vehicle     in a file whose URI contains this
          typing:P::Engine with an edge of this kind (any kind of edge) to the node with this qualified name
                           (or name; case-insensitive), e.g. the usages of P::Engine
        Stub nodes (referenced but not loaded) are excluded unless stubs=True."""
        conditions: List[str] = []
        params: List[Any] = []
        name_terms: List[str] = []
        for token in _split(query):
            field, _, value = token.partition(":")
            if field in self.kinds and value and not value.startswith(":"):
                conditions.append("key IN (SELECT e.source FROM edges e JOIN nodes t ON t.key = e.target "
                                  "WHERE e.kind = ? AND (t.qualified_name = ? OR lower(t.name) = lower(?)))")
                params += [field, value, value]
                continue
            if field in FIELDS and value and not value.startswith(":"):
                if field == "type":
                    conditions.append("metaclass IN (SELECT metaclass FROM metaclasses WHERE general = ?)")
                    params.append(value)
                elif field == "metaclass":
                    conditions.append("metaclass = ?")
                    params.append(value)
                elif field == "name":
                    conditions.append("(lower(name) GLOB ? OR lower(short_name) GLOB ? OR lower(name_tr) GLOB ?)")
                    params += [_glob(value.lower())] * 3
                elif field == "in":
                    value = value.rstrip(":")
                    conditions.append("(qualified_name LIKE ? ESCAPE '\\' OR qualified_name LIKE ? ESCAPE '\\')")
                    params += [_like(value) + "::%", "%::" + _like(value) + "::%"]
                elif field == "meta":
                    conditions.append("(' ' || lower(metadata) || ' ') LIKE ? ESCAPE '\\'")
                    params.append("% " + _like(value.lower()) + " %")
                elif field == "file":
                    conditions.append("model LIKE ? ESCAPE '\\'")
                    params.append("%" + _like(value) + "%")
                continue
            name_terms.append(token)
            if self.has_fts and "*" not in token and len(token) >= 3:
                conditions.append("key IN (SELECT key FROM nodes_text WHERE nodes_text MATCH ?)")
                params.append('"' + token.replace('"', '""') + '"')
            else:
                pattern = _like(token).replace("*", "%")
                conditions.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in _TEXT_COLUMNS) + ")")
                params += ["%" + pattern + "%"] * len(_TEXT_COLUMNS)
        if not stubs:
            conditions.append("stub = 0")
        # Best first: the name is the first word searched, starts with it, contains it; then the shortest name and
        # qualified name.
        order = []
        for term in name_terms[:1]:
            term = term.replace("*", "").lower()
            order.append("CASE WHEN lower(name) = ? OR name_tr = ? THEN 0 "
                         "WHEN lower(name) LIKE ? ESCAPE '\\' OR name_tr LIKE ? ESCAPE '\\' THEN 1 "
                         "WHEN lower(name) LIKE ? ESCAPE '\\' OR name_tr LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END")
            for pattern in (term, _like(term) + "%", "%" + _like(term) + "%"):
                params += [pattern, pattern]
        order.append("name IS NULL, length(name), length(qualified_name), qualified_name, key")
        sql = "SELECT key FROM nodes"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY " + ", ".join(order)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return [row[0] for row in self.sql(sql, params)]

    def close(self) -> None:
        self._db.close()


def _split(query: str) -> List[str]:
    try:
        return shlex.split(query)
    except ValueError:  # An unbalanced quote.
        return query.split()


def _like(text: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", text)


def _glob(text: str) -> str:
    return re.sub(r"([\[\]?])", r"[\1]", text)
