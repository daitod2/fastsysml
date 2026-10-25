"""Tests of fastsysml.browser (the graph of nodes and edges, search and traversal): python -m unittest discover -s
tests. The test with the vehicle example of the SysML v2 release is skipped unless it is found (see
test_fastsysml.py)."""

import tempfile
import unittest
from pathlib import Path

from fastsysml import SysMLParser
from fastsysml.browser import Workspace

from test_fastsysml import VEHICLE

MODEL = """package P {
    private import ScalarValues::Real;
    doc /* The P package */
    metadata def Safety;
    item def Torque;
    port def DrivePort { out item torque : Torque; }
    part def Engine { port drive : DrivePort; attribute mass : Real; }
    part def Wheel;
    part def SpareWheel :> Wheel;
    part def Vehicle {
        part engine : Engine [1] { @Safety; }
        part wheels : Wheel [4];
        part spare : SpareWheel;
        part axle { port drive : ~DrivePort; }
        connect engine.drive to axle.drive;
        flow engine.drive.torque to axle.drive.torque;
        attribute speed : Real = 100.0;
        ref part front = wheels;
    }
    requirement def MassReq { doc /* The mass limit */ subject v : Vehicle; }
    requirement massReq : MassReq;
    part vehicle : Vehicle {
        satisfy massReq by vehicle;
        perform drive;
    }
    action drive;
    dependency from vehicle to massReq;
    state def S {
        entry; then off;
        state off;
        state on;
        transition off_on first off then on;
    }
}
"""


class BrowserTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.parser = SysMLParser()
        cls.ws = Workspace(cls.parser)
        cls.model = cls.ws.load_text(MODEL, "p.sysml")

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()
        cls.parser.close()

    def node(self, query):
        [node] = self.ws.search(query)
        return node

    def edges(self, node, kind):
        return sorted(edge.target_label for edge in node.out if edge.kind == kind)

    def test_no_errors(self):
        self.assertEqual(self.model.errors, [])

    def test_nodes(self):
        engine = self.node("type:PartUsage name:engine")
        self.assertEqual(engine.qualified_name, "P::Vehicle::engine")
        self.assertEqual(engine.owner.qualified_name, "P::Vehicle")
        self.assertEqual(engine.membership, "FeatureMembership")
        self.assertEqual(engine.multiplicity, "[1]")
        self.assertEqual(engine.metadata, ["Safety"])
        self.assertEqual(engine.line, 11)
        self.assertEqual(engine.text, "part engine : Engine [1] { @Safety; }")
        package = self.node("type:Package name:P")
        self.assertEqual(package.doc, ["The P package"])
        self.assertEqual(package.owner.label, "p.sysml")  # The root namespace of the file.
        self.assertEqual(self.ws.roots, [package.owner])
        speed = self.node("name:speed")
        self.assertEqual(speed.value, "100.0")
        # No nodes for the structure (memberships, typings, multiplicities, expressions, connector ends, ...).
        metaclasses = {node.metaclass for node in self.ws.graph.nodes.values() if not node.stub}
        for hidden in ("OwningMembership", "FeatureMembership", "FeatureTyping", "MultiplicityRange",
                       "LiteralRational", "Documentation", "Feature", "FeatureValue", "FeatureChaining"):
            self.assertNotIn(hidden, metaclasses)

    def test_specializations(self):
        engine = self.node("type:PartUsage name:engine")
        self.assertEqual(self.edges(engine, "typing"), ["Engine"])
        wheel = self.node("type:PartDefinition name:Wheel")
        spare = self.node("name:SpareWheel")
        self.assertEqual(self.ws.specializations(wheel), [spare])
        self.assertEqual(self.ws.generals(spare), [wheel])
        self.assertEqual([n.label for n in self.ws.usages(wheel)], ["wheels"])
        self.assertEqual(sorted(n.label for n in self.ws.usages(wheel, specializations=True)), ["spare", "wheels"])
        axle_drive = self.node("type:PortUsage in:P::Vehicle::axle name:drive")
        self.assertEqual(self.edges(axle_drive, "typing"), ["~DrivePort"])

    def test_connections(self):
        vehicle = self.node("type:PartDefinition name:Vehicle")
        [connection] = [n for n in vehicle.children if n.metaclass == "ConnectionUsage"]
        self.assertEqual(self.edges(connection, "end"), ["axle.drive", "engine.drive"])
        self.assertEqual(connection.label, "engine.drive to axle.drive")
        [edge] = connection.edges_in("connection") or [e for e in self.ws.graph.edges if e.kind == "connection"]
        self.assertEqual((edge.source_label, edge.target_label), ("engine.drive", "axle.drive"))
        self.assertEqual(edge.source.qualified_name, "P::Engine::drive")
        [flow] = [e for e in self.ws.graph.edges if e.kind == "flow"]
        self.assertEqual((flow.source_label, flow.target_label), ("engine.drive.torque", "axle.drive.torque"))
        front = self.node("name:front")
        self.assertEqual(self.edges(front, "value"), ["wheels"])

    def test_requirements_and_behavior(self):
        vehicle = self.node("type:PartUsage name:vehicle")
        self.assertEqual(self.edges(vehicle, "satisfy"), ["massReq"])
        self.assertEqual(self.edges(vehicle, "perform"), ["drive"])
        self.assertEqual(self.edges(vehicle, "dependency"), ["massReq"])
        self.assertEqual(self.node("type:RequirementDefinition name:MassReq").doc, ["The mass limit"])
        off = self.node("type:StateUsage name:off")
        self.assertEqual(self.edges(off, "transition"), ["on"])
        transition = self.node("name:off_on")
        self.assertEqual(self.edges(transition, "end"), ["off", "on"])

    def test_library(self):
        real = self.ws.graph.node(self.node("name:speed").targets("typing")[0].key)
        self.assertTrue(real.stub)
        self.assertEqual(real.qualified_name, "ScalarValues::Real")
        self.assertEqual(sorted(n.label for n in real.sources("typing")), ["mass", "speed"])
        self.assertEqual(self.ws.search("Real"), [])  # Stubs are not searched by default.
        self.assertEqual(self.ws.search("name:Real", stubs=True), [real])
        fetched = self.ws.node(real.key)  # Got from the bridge.
        self.assertIs(fetched, real)
        self.assertFalse(real.stub)
        self.assertEqual(real.metaclass, "DataType")
        self.assertEqual(real.owner.qualified_name, "ScalarValues")
        self.assertEqual(sorted(n.label for n in real.sources("typing")), ["mass", "speed"])  # Kept.
        self.assertTrue(real.targets("subclassification"))
        self.assertEqual(self.ws.search("name:Real"), [real])
        # The import of a library membership: shown as an edge to its member.
        package = self.node("type:Package name:P")
        self.assertEqual(self.edges(package, "import"), ["Real"])
        self.assertIs(package.targets("import")[0], real)

    def test_search(self):
        self.assertEqual([n.label for n in self.ws.search("type:Definition wheel")], ["Wheel", "SpareWheel"])
        self.assertEqual([n.label for n in self.ws.search("metaclass:PartDefinition name:*wheel")],
                         ["Wheel", "SpareWheel"])
        self.assertEqual([n.qualified_name for n in self.ws.search("in:Vehicle type:PortUsage")],
                         ["P::Vehicle::axle::drive"])
        self.assertEqual([n.label for n in self.ws.search("meta:safety")], ["engine"])
        self.assertEqual([n.label for n in self.ws.search("limit")], ["MassReq"])  # In the documentation.
        self.assertEqual([n.label for n in self.ws.search("sp")][:1], ["spare"])  # Short: without the index.
        rows = self.ws.sql("SELECT n.name FROM edges e JOIN nodes n ON n.key = e.source "
                           "WHERE e.kind = 'typing' AND e.target = ?", self.node("type:PartDefinition name:Engine").key)
        self.assertEqual(rows, [("engine",)])

    def test_neighborhood(self):
        engine = self.node("type:PartDefinition name:Engine")
        nodes, edges = self.ws.neighborhood(engine, depth=1)
        self.assertEqual({n.label for n in nodes}, {"Engine", "engine"})
        nodes, edges = self.ws.neighborhood(engine, depth=1, ownership=True)
        self.assertEqual({n.label for n in nodes}, {"Engine", "engine", "drive", "mass", "P"})
        nodes, edges = self.ws.neighborhood(engine, depth=2, kinds=["typing"], direction="in")
        self.assertEqual({n.label for n in nodes}, {"Engine", "engine"})

    def test_files(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory, "a.sysml"), Path(directory, "b.sysml")
            a.write_text("package A { part def T; }", encoding="utf-8")
            b.write_text("package B { private import A::*; part t : T; }", encoding="utf-8")
            with Workspace(self.parser) as ws:
                ws.parser.unload()
                ws.load([a, b])
                [t] = ws.search("type:PartUsage name:t")
                [definition] = t.targets("typing")
                self.assertFalse(definition.stub)  # In the other file.
                self.assertEqual(definition.qualified_name, "A::T")
                a.write_text("package A { part def T; part def U; }", encoding="utf-8")
                ws.reload()
                self.assertEqual(len(ws.search("type:PartDefinition")), 2)
                ws.parser.unload()
        # (The inputs of this test's own workspace were unloaded; its graph is unchanged.)


@unittest.skipUnless(VEHICLE.exists(), "SysML v2 release not found")
class VehicleTest(unittest.TestCase):

    def test_vehicle(self):
        with Workspace() as ws:
            ws.load([VEHICLE])
            self.assertEqual(ws.issues, [])
            [wheel] = ws.search("type:PartDefinition name:Wheel")
            self.assertEqual(len(ws.usages(wheel)), 8)
            [engine] = ws.search("type:PartUsage name:engine in:VehicleConfiguration_b::PartsTree::vehicle_b")
            self.assertEqual(sorted(e.kind for e in engine.out), ["perform", "satisfy", "typing"])
            self.assertIn("allocation", [e.kind for e in engine.into])
            kinds = {e.kind for e in ws.graph.edges}
            for kind in ("typing", "redefinition", "subsetting", "connection", "interface", "flow", "allocation",
                         "binding", "succession", "transition", "satisfy", "verify", "perform", "dependency",
                         "value", "import"):
                self.assertIn(kind, kinds)
            self.assertFalse([n for n in ws.graph.nodes.values() if n.label.startswith("(")])


if __name__ == "__main__":
    unittest.main()
