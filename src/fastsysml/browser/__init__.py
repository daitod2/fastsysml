"""Browsing SysML models: the elements as nodes with the relationships between them as edges (instead of the
abstract syntax), with search, lists by metaclass and traversal, and the library elements got when needed.

    from fastsysml.browser import Workspace

    with Workspace() as ws:
        ws.load(["Vehicle.sysml"])
        for node in ws.search("type:PartUsage wheel"):
            print(node.qualified_name, [(e.kind, e.target_label) for e in node.out])
"""

from .graph import CONNECTOR_KINDS, EDGE_KINDS, SPECIALIZATION_KINDS, Edge, Graph, Node
from .search import SearchIndex
from .workspace import Workspace

__all__ = ["Workspace", "Graph", "Node", "Edge", "SearchIndex", "EDGE_KINDS", "SPECIALIZATION_KINDS",
           "CONNECTOR_KINDS"]
