"""A local web server for browsing a workspace: a JSON API (below) and the page in static/ (standard library only).

    python -m fastsysml browse Vehicle.sysml [--port 8765]

API (GET, except reload):
  /api/info                 the files loaded, counts and issues
  /api/types                the metaclasses of the nodes, with counts
  /api/search?q=...         the rows of the nodes matching a query (see SearchIndex.search), at most `limit`
  /api/search.csv?q=...     the same rows as CSV
  /api/node?key=...         a node with its owners, children, edges, source text and issues (got if a stub:
                            then "fetched" is true)
  /api/children?key=...     the children of a node (without key: the roots of the files)
  /api/issues               all the issues, with their nodes
  POST /api/reload          loads the files again
It listens on localhost only.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .graph import Edge, Node
from .workspace import Workspace

STATIC = Path(__file__).resolve().parent / "static"
_STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/app.css": "app.css"}

# Groups of metaclasses, for the list of metaclasses (the first one that a metaclass is a subclass of).
GROUPS = ("Package", "Definition", "Usage", "Relationship", "AnnotatingElement", "Element")
# The columns of the rows (in this order); the page shows those with values.
COLUMNS = ("label", "metaclass", "owner", "typing", "subclassification", "redefinition", "subsetting",
           "reference", "usages", "multiplicity", "value", "doc", "file", "line")
MAX_ROWS = 5000
MAX_CHILDREN = 1000


class BrowserApp:
    """Answers the requests of the API from a workspace (one request at a time)."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.lock = threading.RLock()

    # Requests

    def get(self, path: str, params: Dict[str, str]) -> Tuple[int, str, bytes]:
        with self.lock:
            if path == "/api/info":
                return _json(self.info())
            if path == "/api/types":
                return _json(self.types())
            if path in ("/api/search", "/api/search.csv"):
                query = params.get("q", "")
                limit = int(params.get("limit", MAX_ROWS))
                nodes, total = self.search(query, min(limit, MAX_ROWS))
                if path.endswith(".csv"):
                    return HTTPStatus.OK, "text/csv; charset=utf-8", _csv([self.row(node) for node in nodes])
                return _json({"query": query, "total": total, "rows": [self.row(node) for node in nodes]})
            if path == "/api/node":
                before = self.ws.node(params.get("key", ""), fetch=False)
                was_stub = before is not None and before.stub
                node = self.ws.node(params.get("key", ""))
                if node is None:
                    return _json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                # fetched: got from the bridge now (the counts of the workspace changed).
                return _json(dict(self.detail(node), fetched=was_stub and not node.stub))
            if path == "/api/children":
                if not params.get("key"):
                    return _json([self.summary(node, children=True) for node in self.ws.roots])
                node = self.ws.node(params["key"], fetch=False)
                if node is None:
                    return _json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return _json([self.summary(child, children=True) for child in node.children[:MAX_CHILDREN]])
            if path == "/api/issues":
                return _json(self.issues())
        return _json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def post(self, path: str) -> Tuple[int, str, bytes]:
        with self.lock:
            if path == "/api/reload":
                self.ws.reload()
                return _json(self.info())
        return _json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    # Answers

    def info(self) -> Dict[str, Any]:
        nodes = [node for node in self.ws.graph.nodes.values() if not node.stub]
        issues = self.ws.issues
        return {"files": [_file_name(root.model_uri) for root in self.ws.roots], "nodes": len(nodes),
                "stubs": len(self.ws.graph.nodes) - len(nodes), "edges": len(self.ws.graph.edges),
                "errors": sum(1 for issue in issues if issue.severity == "error"), "issues": len(issues)}

    def types(self) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for node in self.ws.graph.nodes.values():
            if not node.stub and node.metaclass is not None:
                counts[node.metaclass] = counts.get(node.metaclass, 0) + 1
        is_a = self.ws.graph.is_a
        return [{"metaclass": metaclass, "count": count,
                 "group": next((group for group in GROUPS if is_a(metaclass, group)), "Element")}
                for metaclass, count in sorted(counts.items())]

    def search(self, query: str, limit: int) -> Tuple[List[Node], int]:
        keys = self.ws.index.search(query, limit=None)
        return [self.ws.graph.nodes[key] for key in keys[:limit]], len(keys)

    def issues(self) -> List[Dict[str, Any]]:
        result = []
        for model in self.ws.models:
            for issue in model.issues:
                node = self._issue_node(model, issue)
                result.append({"severity": issue.severity, "message": issue.message, "line": issue.line,
                               "column": issue.column, "file": _file_name(model.uri),
                               "node": self.summary(node) if node is not None else None})
        return result

    def _issue_node(self, model: Any, issue: Any) -> Optional[Node]:
        if issue.element_id is None:
            return None
        return self.ws.graph.node(model.elements[issue.element_id].uri)

    def summary(self, node: Node, children: bool = False) -> Dict[str, Any]:
        result = {"key": node.key, "label": node.label, "metaclass": node.metaclass,
                  "qualifiedName": node.qualified_name, "stub": node.stub}
        if children:
            result["children"] = len(node.children)
        return result

    def row(self, node: Node) -> Dict[str, Any]:
        def labels(kind: str) -> Optional[str]:
            return ", ".join(edge.target_label for edge in node.out if edge.kind == kind) or None
        return {"key": node.key, "label": node.label, "metaclass": node.metaclass,
                "qualifiedName": node.qualified_name,
                "owner": node.owner.label if node.owner is not None else None,
                "typing": labels("typing"), "subclassification": labels("subclassification"),
                "redefinition": labels("redefinition"), "subsetting": labels("subsetting"),
                "reference": labels("reference"), "usages": len(node.edges_in("typing")) or None,
                "multiplicity": node.multiplicity, "value": node.value,
                "doc": node.doc[0].splitlines()[0] if node.doc else None,
                "file": _file_name(node.model_uri), "line": node.line, "stub": node.stub}

    def detail(self, node: Node) -> Dict[str, Any]:
        ancestors = []
        owner = node.owner
        while owner is not None and len(ancestors) < 100:
            ancestors.insert(0, self.summary(owner))
            owner = owner.owner
        issues = []
        for model in self.ws.models:
            for issue in model.issues:
                if self._issue_node(model, issue) is node:
                    issues.append({"severity": issue.severity, "message": issue.message, "line": issue.line,
                                   "column": issue.column})
        result = self.summary(node)
        result.update(node.to_dict())
        result.update({
            "file": _file_name(node.model_uri), "uri": node.key, "ancestors": ancestors,
            "children": [self.summary(child, children=True) for child in node.children[:MAX_CHILDREN]],
            "childCount": len(node.children),
            "out": [self._edge(edge, edge.target, edge.path) for edge in node.out],
            "in": [self._edge(edge, edge.source, edge.source_path) for edge in node.into],
            "text": node.text, "issues": issues,
        })
        return result

    def _edge(self, edge: Edge, other: Optional[Node], path: Optional[str]) -> Dict[str, Any]:
        via = self.ws.graph.node(edge.via)
        return {"kind": edge.kind, "node": self.summary(other) if other is not None else None,
                "label": path or (other.label if other is not None else edge.text or "?"),
                "unresolved": other is None,
                "via": self.summary(via) if via is not None and via is not edge.source and via is not edge.target
                else None}


def _file_name(uri: str) -> str:
    return unquote(uri.rsplit("/", 1)[-1])


def _json(value: Any, status: int = HTTPStatus.OK) -> Tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode("utf-8")


def _csv(rows: List[Dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    columns = ("qualifiedName",) + COLUMNS
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else row[column] for column in columns])
    return out.getvalue().encode("utf-8-sig")  # (With a BOM, for Excel.)


class _Handler(BaseHTTPRequestHandler):
    app: BrowserApp

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            params = {name: values[0] for name, values in parse_qs(url.query).items()}
            try:
                self._send(*self.app.get(url.path, params))
            except Exception as e:  # Reported to the page, the server keeps running.
                self._send(*_json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR))
            return
        name = _STATIC_FILES.get(url.path)
        if name is None:
            self._send(*_json({"error": "not found"}, HTTPStatus.NOT_FOUND))
            return
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, content_type, (STATIC / name).read_bytes())

    def do_POST(self) -> None:
        url = urlparse(self.path)
        try:
            self._send(*self.app.post(url.path))
        except Exception as e:
            self._send(*_json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR))

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet.


def make_server(workspace: Workspace, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A server for a workspace (call serve_forever(); port 0 picks a free port: server.server_address)."""
    handler = type("Handler", (_Handler,), {"app": BrowserApp(workspace)})
    return ThreadingHTTPServer((host, port), handler)


def serve(workspace: Workspace, port: int = 8765, open_browser: bool = True) -> None:
    """Serves a workspace until interrupted (Ctrl+C), opening the page in a web browser."""
    server = make_server(workspace, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Browsing at {url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
