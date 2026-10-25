"""Tests of the browser's server (fastsysml.browser.server): its API, and the page served over HTTP."""

import json
import threading
import unittest
import urllib.request
from urllib.parse import quote

from fastsysml import SysMLParser
from fastsysml.browser import Workspace
from fastsysml.browser.server import BrowserApp, make_server

MODEL = """package P {
    part def Engine { attribute mass : ScalarValues::Real; }
    part def Vehicle { part engine : Engine; part spare : Engine; }
    part vehicle : Vehicle;
    part broken : Missing;
}
"""


class ServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.parser = SysMLParser()
        cls.ws = Workspace(cls.parser)
        cls.ws.load_text(MODEL, "p.sysml")
        cls.app = BrowserApp(cls.ws)

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()
        cls.parser.close()

    def get(self, path, **params):
        status, content_type, body = self.app.get(path, {name: str(value) for name, value in params.items()})
        self.assertEqual(status, 200, body)
        return json.loads(body) if content_type.startswith("application/json") else body.decode("utf-8-sig")

    def key(self, query):
        [row] = self.get("/api/search", q=query)["rows"]
        return row["key"]

    def test_info_and_types(self):
        info = self.get("/api/info")
        self.assertEqual(info["files"], ["p.sysml"])
        self.assertEqual(info["errors"], 2)  # Missing (not resolved, so not an occurrence definition)
        types = {t["metaclass"]: t for t in self.get("/api/types")}
        self.assertEqual(types["PartDefinition"]["count"], 2)
        self.assertEqual(types["PartDefinition"]["group"], "Definition")
        self.assertEqual(types["PartUsage"]["group"], "Usage")

    def test_search_rows(self):
        result = self.get("/api/search", q="type:PartUsage in:Vehicle")
        self.assertEqual(result["total"], 2)
        rows = {row["label"]: row for row in result["rows"]}
        self.assertEqual(rows["engine"]["typing"], "Engine")
        self.assertEqual(rows["engine"]["owner"], "Vehicle")
        self.assertEqual(rows["engine"]["line"], 3)
        engine = self.get("/api/search", q="metaclass:PartDefinition name:Engine")["rows"][0]
        self.assertEqual(engine["usages"], 2)
        # The usages of Engine, by the kind of edge.
        self.assertEqual(self.get("/api/search", q='typing:"P::Engine"')["total"], 2)
        csv = self.get("/api/search.csv", q="metaclass:PartDefinition")
        self.assertTrue(csv.startswith("qualifiedName,label,metaclass"))
        self.assertIn("P::Engine,Engine,PartDefinition", csv)

    def test_node(self):
        detail = self.get("/api/node", key=self.key("metaclass:PartDefinition name:Engine"))
        self.assertEqual(detail["label"], "Engine")
        self.assertEqual([a["label"] for a in detail["ancestors"]], ["p.sysml", "P"])
        self.assertEqual([c["label"] for c in detail["children"]], ["mass"])
        self.assertEqual(sorted(e["node"]["label"] for e in detail["in"] if e["kind"] == "typing"),
                         ["engine", "spare"])
        self.assertTrue(detail["text"].startswith("part def Engine"))
        broken = self.get("/api/node", key=self.key("name:broken"))
        [edge] = [e for e in broken["out"] if e["kind"] == "typing"]
        self.assertTrue(edge["unresolved"])
        self.assertEqual(edge["label"], "Missing")
        self.assertEqual(len(broken["issues"]), 2)

    def test_library_node(self):
        mass = self.get("/api/node", key=self.key("name:mass"))
        [typing] = [e for e in mass["out"] if e["kind"] == "typing"]
        self.assertTrue(typing["node"]["stub"])
        real = self.get("/api/node", key=typing["node"]["key"])
        self.assertTrue(real["fetched"])
        self.assertFalse(real["stub"])
        self.assertEqual(real["metaclass"], "DataType")
        self.assertFalse(self.get("/api/node", key=typing["node"]["key"])["fetched"])  # Only the first time.

    def test_children_and_issues(self):
        [root] = self.get("/api/children")
        self.assertEqual(root["label"], "p.sysml")
        [package] = self.get("/api/children", key=root["key"])
        self.assertEqual(package["label"], "P")
        self.assertEqual(package["children"], 4)
        [issue] = [i for i in self.get("/api/issues") if "Missing" in i["message"]]
        self.assertEqual(issue["node"]["label"], "broken")
        self.assertEqual(issue["file"], "p.sysml")

    def test_http(self):
        server = make_server(self.ws, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}/"
            with urllib.request.urlopen(base) as response:
                self.assertIn("SysML ブラウザ", response.read().decode("utf-8"))
            with urllib.request.urlopen(base + "app.js") as response:
                self.assertIn("javascript", response.headers["Content-Type"])
            with urllib.request.urlopen(base + "api/search?q=" + quote("metaclass:PartUsage name:vehicle")) as response:
                self.assertEqual(json.loads(response.read())["rows"][0]["label"], "vehicle")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "../pyproject.toml")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
