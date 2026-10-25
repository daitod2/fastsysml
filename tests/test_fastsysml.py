"""Tests of the Python binding: python -m unittest discover -s tests (with fastsysml installed or src on
PYTHONPATH). The tests using the example models of the SysML v2 release are skipped unless it is found at
SYSML_V2_RELEASE (a clone of https://github.com/Systems-Modeling/SysML-v2-Release) or next to this repository."""

import os
import tempfile
import unittest
from pathlib import Path

from fastsysml import BridgeError, External, SysMLParser, Unresolved

RELEASE = Path(os.environ.get("SYSML_V2_RELEASE", Path(__file__).resolve().parents[2] / "sysml-v2-release"))
VEHICLE = RELEASE / "sysml/src/examples/Vehicle Example/SysML v2 Spec Annex A SimpleVehicleModel.sysml"


class ParserTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.parser = SysMLParser()

    @classmethod
    def tearDownClass(cls):
        cls.parser.close()

    def test_parse_only(self):
        model = self.parser.parse("package P { part def A { attribute x; } part a : A; }")
        self.assertEqual(model.errors, [])
        [package] = model.roots[0].children[0].children  # Namespace -> OwningMembership -> Package
        self.assertEqual(package.type, "Package")
        self.assertEqual(package.name, "P")
        [part] = model.find(type="PartUsage")
        self.assertEqual(part.name, "a")
        self.assertEqual(part.text, "part a : A;")
        self.assertEqual(part.line, 1)
        [typing] = part.children
        self.assertIsInstance(typing.ref("type"), Unresolved)  # not resolved without resolve
        self.assertEqual(typing.ref("type").text, "A")

    def test_syntax_error(self):
        model = self.parser.parse("package P { part def A; part a : ; }")
        self.assertTrue(model.errors)
        self.assertEqual(model.errors[0].line, 1)

    def test_resolve(self):
        model = self.parser.parse(
            "package P { private import ScalarValues::*; part def A { attribute x : Real; } part a : A; }",
            resolve="lazy", validate=True, derived=["qualifiedName"])
        self.assertEqual(model.errors, [])
        [part] = model.find(type="PartUsage")
        [definition] = model.find(type="PartDefinition")
        self.assertIs(part.children[0].ref("type"), definition)
        self.assertEqual(part.derived["qualifiedName"], "P::a")
        [attribute] = model.find(type="AttributeUsage")
        real = attribute.children[0].ref("type")
        self.assertIsInstance(real, External)
        self.assertEqual(real.qualified_name, "ScalarValues::Real")

    def test_unresolved_reference(self):
        model = self.parser.parse("package P { part a : Missing; }", resolve="lazy", validate=True)
        self.assertTrue(any("Missing" in issue.message for issue in model.errors))
        [part] = model.find(type="PartUsage")
        self.assertIsInstance(part.children[0].ref("type"), Unresolved)
        self.assertIs(model.errors[0].element, part.children[0])

    def test_files_reference_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory, "A.sysml")
            b = Path(directory, "B.sysml")
            a.write_text("package A { part def Engine; }", encoding="utf-8")
            b.write_text("package B { private import A::*; part engine : Engine; }", encoding="utf-8")
            model_a, model_b = self.parser.parse_files([a, b], resolve="lazy", validate=True)
            self.assertEqual(model_b.errors, [])
            target = model_b.find(type="PartUsage")[0].children[0].ref("type")
            self.assertIsInstance(target, External)
            self.assertEqual(target.qualified_name, "A::Engine")
            # Without A, B's reference cannot be resolved.
            [alone] = self.parser.parse_files([b], resolve="lazy", validate=True)
            self.assertTrue(alone.errors)

    def test_keep_and_unload(self):
        self.parser.parse("package Kept { part def K; }", name="Kept.sysml", keep=True)
        model = self.parser.parse("package U { private import Kept::*; part k : K; }", resolve="lazy",
                                  validate=True)
        self.assertEqual(model.errors, [])
        self.assertEqual(self.parser.unload(), 1)
        self.assertEqual(self.parser.stats()["inputs"], 0)

    def test_kerml(self):
        model = self.parser.parse("package P { classifier C; feature f : C; }", name="p.kerml",
                                  resolve="lazy", validate=True)
        self.assertEqual(model.errors, [])
        self.assertEqual([e.name for e in model.find(type="Classifier")], ["C"])

    @unittest.skipUnless(VEHICLE.exists(), "SysML v2 release examples not found (set SYSML_V2_RELEASE)")
    def test_file(self):
        model = self.parser.parse_file(VEHICLE, resolve="lazy", validate=True)
        self.assertEqual(model.errors, [])
        self.assertGreater(len(model), 5000)
        unresolved = [target for element in model for targets in element.refs.values() for target in targets
                      if isinstance(target, Unresolved)]
        self.assertEqual(unresolved, [])

    def test_bad_request(self):
        with self.assertRaises(BridgeError):
            self.parser.parse("package P;", name="p.txt")
        # The bridge still works after an error.
        self.assertEqual(self.parser.parse("package P;").errors, [])


class ModeTest(unittest.TestCase):

    def check(self, mode):
        with SysMLParser(mode=mode) as parser:
            model = parser.parse("package P { part def A; part a : A; }", resolve="lazy", validate=True)
            self.assertEqual(model.errors, [])
            self.assertEqual(parser.startup["mode"], mode)

    def test_userside(self):
        self.check("userside")

    def test_plain(self):
        self.check("plain")


if __name__ == "__main__":
    unittest.main()
