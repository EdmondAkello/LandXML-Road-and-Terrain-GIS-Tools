"""Setting-out data: geometry schedule, key points, stations with bearings and levels, feature-line offsets."""

import importlib.util
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml import setting_out as so_
from landxml_plugin.landxml.corridor_quantities import write_xlsx
from landxml_plugin.landxml.writer import (RIGHT, Curve, FeatureLine, HorizontalAlignment, Line, Spiral, VerticalProfile,
                                           write_landxml)


class SettingOutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "road.xml")
        self.road = HorizontalAlignment("Road", start=(500000.0, 9900000.0), bearing=30.0, elements=[
            Line(100), Spiral(40, None, 200, RIGHT), Curve(80, 200, RIGHT), Spiral(40, 200, None, RIGHT), Line(120)])
        profile = VerticalProfile("FRL", [(0, 100.0, 0), (self.road.length, 104.0, 0)])
        # an edge 3.5 m right of the first straight, at 0.1 below the profile
        b = math.radians(30.0)
        right = np.array([math.cos(b), -math.sin(b)])
        ahead = np.array([math.sin(b), math.cos(b)])
        edge = [tuple(np.array([500000.0, 9900000.0]) + ahead * s + right * 3.5) + (100 + 4 * s / self.road.length - 0.1,)
                for s in (0.0, 50.0, 100.0)]
        write_landxml(self.path, [(self.road, [profile])], northing_first=True, epsg=32737,
                      feature_lines=[FeatureLine("ETW R", edge, "ETW")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_schedule_key_points_and_bearings(self):
        so = so_.setting_out(self.path, "Road", True, 20.0)
        self.assertEqual([e["type"] for e in so.elements], ["line", "spiral", "curve", "spiral", "line"])
        self.assertEqual([k for _s, k in so.keys], ["BP", "TS", "SC", "CS", "ST", "EP"])
        self.assertEqual(so_.dms(so.elements[0]["b0"]), "030°00'00\"")
        self.assertAlmostEqual(so.elements[1]["A"], math.sqrt(200 * 40), places=6)
        # the deflection of the bend: spirals 40 / (2 R) each plus the arc 80 / R, to the right
        turn = math.degrees(40 / 400 * 2 + 80 / 200)
        self.assertAlmostEqual(so.elements[-1]["b0"], 30.0 + turn, delta=1 / 3600)       # within a second of arc
        for a, b in zip(so.elements, so.elements[1:]):                                  # tangent continuity
            self.assertAlmostEqual(a["b1"], b["b0"], delta=1 / 3600)
        mid = so.elements[2]["start"] + 40                                              # middle of the arc
        self.assertAlmostEqual(so_.bearing_at(so.elements, mid), 30 + math.degrees(40 / 400 + 40 / 200), delta=1 / 3600)
        ts = next(r for r in so.rows if r["point"] == "TS")
        self.assertAlmostEqual(ts["e"], 500000.0 + 100 * math.sin(math.radians(30)), places=3)
        self.assertAlmostEqual(ts["n"], 9900000.0 + 100 * math.cos(math.radians(30)), places=3)
        self.assertAlmostEqual(ts["design"], 100 + 4 * 100 / self.road.length, places=3)
        self.assertEqual(len([r for r in so.rows if abs(r["station"] % 20) < 1e-6]), int(self.road.length // 20) + 1)
        self.assertEqual(len(so.pis), 3)
        self.assertAlmostEqual(abs(so.pis[1]["deflection"]), turn, delta=0.01)

    def test_feature_line_offsets_and_outputs(self):
        so = so_.setting_out(self.path, "Road", True, 20.0)
        self.assertEqual(list(so.offsets), ["ETW R"])
        at40 = next(i for i, r in enumerate(so.rows) if abs(r["station"] - 40) < 1e-6)
        offset, level = so.offsets["ETW R"][at40]
        self.assertAlmostEqual(offset, 3.5, places=3)
        self.assertAlmostEqual(level, so.rows[at40]["design"] - 0.1, places=3)
        out = os.path.join(self.tmp.name, "so.xlsx")
        write_xlsx(out, so_.sheets(so))
        import zipfile
        names = zipfile.ZipFile(out).namelist()
        self.assertEqual(len([n for n in names if n.startswith("xl/worksheets/sheet")]), 6)
        csv_path = os.path.join(self.tmp.name, "so.csv")
        so_.write_csv(csv_path, so)
        with open(csv_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[0], "Point,Easting,Northing,Level,Description")
        self.assertTrue(lines[1].startswith("CL1,500000.0,9900000.0,100.0,CL 0+000.000 BP"))
        page = so_.report_html(so)
        self.assertIn("Setting-out data", page)
        self.assertIn("030°00&#x27;00&quot;", page)


if __name__ == "__main__":
    unittest.main()
