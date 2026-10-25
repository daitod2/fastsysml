"""Tests of the translations of fastsysml.browser (translation.py, and their use by the search and the server), with
a dictionary of the tests, and the bundled Japanese translations of the SysML v2 release examples (skipped unless
the release is found; see test_fastsysml.py)."""

import json
import tempfile
import unittest
from pathlib import Path

from fastsysml import SysMLParser
from fastsysml.browser import Workspace
from fastsysml.browser.server import BrowserApp
from fastsysml.browser.translation import TRANSLATIONS, Dictionary, Translator, languages

from test_fastsysml import RELEASE, VEHICLE

MODEL = """package Shop {
    doc /* A small   shop. */
    part def Cart { part goods : Item [*]; }
    part def Item;
    part cart : Cart;
    part other : Cart { :>> goods; }
    connect cart.goods to other.goods;
}
"""

GLOSSARY = {"metaclasses": {"PartDefinition": "パート定義"}, "edges": {"typing": "型"}}
DICTIONARY = {
    "files": ["tests/shop.sysml"],
    "names": {"Shop": "店", "Cart": "カート", "Item": "商品", "goods": "商品", "cart": "カート"},
    "qualifiedNames": {"Shop::other": "別のカート"},
    "texts": {"A small shop.": "小さな店。"},
}


class TranslationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        (root / "glossary.json").write_text(json.dumps(GLOSSARY), encoding="utf-8")
        (root / "shop").mkdir()
        (root / "shop" / "shop.json").write_text(json.dumps(DICTIONARY), encoding="utf-8")
        cls.parser = SysMLParser()
        cls.ws = Workspace(cls.parser)
        cls.ws.translator = Translator("xx", root)
        cls.ws.index.translator = cls.ws.translator
        cls.ws.load_text(MODEL, "tests/shop.sysml")
        cls.tr = cls.ws.translator

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()
        cls.parser.close()
        cls.directory.cleanup()

    def node(self, query):
        [node] = self.ws.search(query)
        return node

    def test_dictionary_files(self):
        dictionary = Dictionary({"files": ["sysml/src/examples/Vehicle Example/*.sysml"]})
        self.assertTrue(dictionary.applies_to("/h/release/sysml/src/examples/Vehicle Example/Vehicle.sysml"))
        self.assertTrue(dictionary.applies_to("sysml/src/examples/Vehicle Example/Vehicle.sysml"))
        self.assertFalse(dictionary.applies_to("/h/release/sysml/src/examples/Room Model/Room.sysml"))

    def test_labels(self):
        self.assertEqual(self.tr.label(self.node("metaclass:PartDefinition name:Cart")), "カート")
        self.assertEqual(self.tr.label(self.node("name:other")), "別のカート")  # By qualified name.
        self.assertEqual(self.tr.label(self.node("name:goods in:Shop::other")), "商品")
        [connection] = self.ws.of_type("ConnectionUsage")
        self.assertEqual(connection.label, "cart.goods to other.goods")
        self.assertEqual(self.tr.label(connection), "カート.商品 → other.商品")  # Paths, word by word.
        package = self.node("type:Package")
        self.assertEqual(self.tr.text(package, package.doc[0]), "小さな店。")  # Whitespace ignored.
        self.assertEqual(self.tr.term("PartDefinition"), "パート定義")
        self.assertEqual(self.tr.term("typing"), "型")
        self.assertEqual(self.tr.term("PartUsage"), "PartUsage")  # Not translated: the original.

    def test_search(self):
        self.assertEqual([n.label for n in self.ws.search("metaclass:PartDefinition 商品")], ["Item"])
        self.assertEqual([n.label for n in self.ws.search("name:カート type:Definition")], ["Cart"])
        self.assertEqual([n.label for n in self.ws.search("小さな")], ["Shop"])  # In the documentation.

    def test_server(self):
        app = BrowserApp(self.ws)

        def get(path, **params):
            status, _, body = app.get(path, params)
            self.assertEqual(status, 200)
            return json.loads(body)

        self.assertEqual(get("/api/info")["language"], "xx")
        self.assertEqual(get("/api/glossary")["edges"], {"typing": "型"})
        self.assertEqual(get("/api/glossary", lang="en"), {})
        [row] = get("/api/search", q="name:cart metaclass:PartUsage")["rows"]
        self.assertEqual((row["label"], row["original"], row["typing"], row["owner"]), ("カート", "cart", "カート", "店"))
        [row] = get("/api/search", q="name:cart metaclass:PartUsage", lang="en")["rows"]
        self.assertEqual((row["label"], row["original"], row["typing"]), ("cart", None, "Cart"))
        detail = get("/api/node", key=self.node("type:Package").key)
        self.assertEqual((detail["label"], detail["original"]), ("店", "Shop"))
        self.assertEqual((detail["doc"], detail["docOriginal"]), (["小さな店。"], ["A small   shop."]))


class BundledTranslationsTest(unittest.TestCase):

    def test_glossary(self):
        self.assertIn("ja", languages())
        tr = Translator("ja")
        with SysMLParser() as parser:
            metaclasses = parser.metaclasses()
        self.assertEqual(sorted(set(metaclasses) - set(tr.glossary["metaclasses"])), [])
        for path in (TRANSLATIONS / "ja").rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))  # Valid JSON.
            if path.name != "glossary.json":
                self.assertTrue(data["files"], path)

    @unittest.skipUnless(VEHICLE.exists(), "SysML v2 release not found")
    def test_vehicle(self):
        with Workspace(language="ja") as ws:
            ws.load([VEHICLE], validate=False)
            [wheel] = ws.search("type:PartDefinition name:車輪")
            self.assertEqual(wheel.name, "Wheel")
            [engine] = ws.search("type:PartUsage name:engine in:VehicleConfiguration_b::PartsTree::vehicle_b")
            self.assertEqual(ws.translator.label(engine), "エンジン")
            [requirement] = ws.search("type:RequirementDefinition name:MassRequirement")
            self.assertEqual([ws.translator.text(requirement, doc) for doc in requirement.doc],
                             ["実質量は要求質量より小さくなければならない。"])
        self.assertTrue(RELEASE.exists())


if __name__ == "__main__":
    unittest.main()
