"""LandXML writer: geometry laid out from design elements, written and read back by the plugin's own readers (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml.catalog import read_catalog, read_units
from landxml_plugin.landxml.common import descendants
from landxml_plugin.landxml.geometry import read_alignments
from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.profile import read_vertical_profile
from landxml_plugin.landxml.features import read_line_features
from landxml_plugin.landxml.writer import (
    LEFT, RIGHT, Curve, FeatureLine, HorizontalAlignment, Line, PICurve, Spiral, Surface, VerticalProfile,
    alignment_from_pis, landxml_text, write_landxml,
)


def road(**options):
    return HorizontalAlignment("Test road", start=(10000.0, 20000.0), bearing=60.0, elements=[
        Line(200), Spiral(60, radius_end=300, turn=RIGHT), Curve(150, 300, RIGHT), Spiral(60, radius_start=300, turn=RIGHT),
        Line(120), Spiral(50, radius_end=220, turn=LEFT), Curve(90, 220, LEFT), Spiral(50, radius_start=220, turn=LEFT),
        Line(150)], **options)


def written(alignments, **options):
    folder = tempfile.mkdtemp()
    return write_landxml(str(Path(folder) / "road.xml"), alignments, **options)


def nearest(points, x, y):
    """Distance from (x, y) to the polyline through ``points``."""
    best = math.inf
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx - x, ay + t * dy - y))
    return best


class LayoutTests(unittest.TestCase):
    def test_a_tangent_runs_on_the_bearing(self):
        line = HorizontalAlignment("L", (0.0, 0.0), 90.0, [Line(100)]).layout()[0]
        self.assertAlmostEqual(line["end"][0], 100.0)
        self.assertAlmostEqual(line["end"][1], 0.0)

    def test_a_curve_keeps_its_radius_and_turns_the_right_way(self):
        laid = HorizontalAlignment("C", (0.0, 0.0), 0.0, [Curve(math.pi * 50, 100, RIGHT)]).layout()[0]   # a quarter turn
        self.assertAlmostEqual(laid["center"][0], 100.0)                       # heading north, turning right: centre east
        self.assertAlmostEqual(laid["end"][0], 100.0, places=6)
        self.assertAlmostEqual(laid["end"][1], 100.0, places=6)
        self.assertAlmostEqual(math.degrees(laid["heading_end"]), 0.0, places=6)  # now heading east

    def test_a_clothoid_turns_by_length_over_twice_the_radius_and_matches_the_series(self):
        laid = HorizontalAlignment("S", (0.0, 0.0), 90.0, [Spiral(60, radius_end=300, turn=LEFT)]).layout()[0]
        self.assertAlmostEqual(laid["heading_end"], 60 / (2 * 300), places=9)
        a2 = 60 * 300                                                            # A² = L R
        x = 60 - 60 ** 5 / (40 * a2 ** 2) + 60 ** 9 / (3456 * a2 ** 4)
        y = 60 ** 3 / (6 * a2) - 60 ** 7 / (336 * a2 ** 3)
        self.assertAlmostEqual(laid["end"][0], x, places=5)
        self.assertAlmostEqual(laid["end"][1], y, places=5)

    def test_elements_must_join_in_curvature(self):
        with self.assertRaises(ValueError):
            HorizontalAlignment("X", (0, 0), 0, [Line(50), Spiral(40, radius_end=300), Curve(50, 250)]).layout()
        with self.assertRaises(ValueError):
            HorizontalAlignment("X", (0, 0), 0, [Curve(50, 250), Spiral(40, radius_start=300)]).layout()
        with self.assertRaises(ValueError):
            HorizontalAlignment("X", (0, 0), 0, [Line(-5)]).layout()
        self.assertAlmostEqual(road().length, 930.0)


class RoundTripTests(unittest.TestCase):
    def test_the_plugin_reads_back_the_same_centreline(self):
        alignment = road()
        path = written([(alignment, [])], epsg=32737)
        results, unsupported = read_alignments(path, 1.0)
        self.assertEqual(unsupported, [])
        got = results[0]
        self.assertEqual(got["name"], "Test road")
        self.assertAlmostEqual(got["vertex_distances"][-1], alignment.length, places=3)
        read = [(e, n) for n, e in got["points"]]                                # written northing first
        worst = max(nearest(read, x, y) for x, y, _s in alignment.points(5.0))
        self.assertLess(worst, 0.01)                                             # within a centimetre everywhere
        self.assertEqual([s["geometry_type"] for s in got["segments"]],
                         ["line", "spiral", "curve", "spiral", "line", "spiral", "curve", "spiral", "line"])

    def test_the_profile_reads_back_with_the_same_levels(self):
        alignment = road(start_station=1000.0)
        profile = VerticalProfile("FRL", [(1000, 1500.0, 0), (1300, 1512.0, 160), (1650, 1504.5, 200), (1930, 1509.0, 0)])
        path = written([(alignment, [profile])])
        prof = next(descendants(load_document(path).root, "ProfAlign"))
        for station, level in read_vertical_profile(prof, 2.0):
            self.assertAlmostEqual(level, profile.elevation(station), places=4)
        self.assertEqual(read_catalog(path).design_profiles, {"Test road": ["FRL"]})

    def test_the_header_states_only_what_was_given(self):
        text = landxml_text([(road(), [])], linear_unit="USSurveyFoot")
        self.assertIn('linearUnit="USSurveyFoot"', text)
        self.assertNotIn("CoordinateSystem", text)                               # nothing inferred
        self.assertIn('epsgCode="2193"', landxml_text([(road(), [])], epsg=2193))
        path = written([(road(), [])], linear_unit="foot")
        self.assertEqual(read_units(path)[0].lower(), "foot")

    def test_easting_first_when_asked(self):
        text = landxml_text([(HorizontalAlignment("E", (100.0, 200.0), 90.0, [Line(10)]), [])], northing_first=False)
        self.assertIn("<Start>100 200</Start>", text)
        text = landxml_text([(HorizontalAlignment("N", (100.0, 200.0), 90.0, [Line(10)]), [])])
        self.assertIn("<Start>200 100</Start>", text)

    def test_names_are_escaped(self):
        text = landxml_text([(HorizontalAlignment('A & "B" <1>', (0.0, 0.0), 0.0, [Line(10)]), [])])
        self.assertIn('name="A &amp; &quot;B&quot; &lt;1&gt;"', text)
        self.assertEqual(read_alignments(written([(HorizontalAlignment("A & B", (0.0, 0.0), 0.0, [Line(10)]), [])]), 1.0)[0][0]["name"],
                         "A & B")


class PITests(unittest.TestCase):
    PIS = [(0.0, 0.0), (400.0, 0.0), (700.0, 300.0), (1000.0, 250.0)]

    def test_curves_fitted_at_the_pis_end_at_the_last_pi_and_stay_tangent(self):
        road = alignment_from_pis("PI road", self.PIS, [PICurve(250, 60, 60), PICurve(400, 40, 80)])
        laid = road.layout()
        self.assertEqual([item["kind"] for item in laid],
                         ["line", "spiral", "curve", "spiral", "line", "spiral", "curve", "spiral", "line"])
        self.assertAlmostEqual(laid[-1]["end"][0], 1000.0, places=4)
        self.assertAlmostEqual(laid[-1]["end"][1], 250.0, places=4)
        self.assertAlmostEqual(laid[4]["heading_start"], math.atan2(300, 300), places=7)    # on the PI2-PI3 tangent
        self.assertAlmostEqual(laid[-1]["heading_end"], math.atan2(-50, 300), places=7)
        mid = laid[4]["start"]                                                              # the tangent runs on the PI line
        self.assertAlmostEqual(mid[1] - 0.0, (mid[0] - 400.0) * 1.0, places=5)

    def test_a_plain_curve_and_a_straight_through_pi(self):
        road = alignment_from_pis("Plain", [(0, 0), (100, 0), (200, 0), (300, 100)], [None, PICurve(80)])
        self.assertEqual([item["kind"] for item in road.layout()], ["line", "curve", "line"])
        with self.assertRaises(ValueError):
            alignment_from_pis("Kink", [(0, 0), (100, 0), (200, 50)], [None])

    def test_curves_that_do_not_fit_are_refused(self):
        with self.assertRaises(ValueError) as error:
            alignment_from_pis("Tight", self.PIS, [PICurve(2000), PICurve(2000)])
        self.assertIn("overlap", str(error.exception))
        with self.assertRaises(ValueError):
            alignment_from_pis("Long spirals", [(0, 0), (500, 0), (1000, 40)], [PICurve(300, 200, 200)])

    def test_the_plugin_reads_back_a_pi_alignment(self):
        road = alignment_from_pis("PI road", self.PIS, [PICurve(250, 60, 60), PICurve(400, 40, 80)])
        results, unsupported = read_alignments(written([(road, [])]), 1.0)
        self.assertEqual(unsupported, [])
        read = [(e, n) for n, e in results[0]["points"]]
        self.assertLess(max(nearest(read, x, y) for x, y, _s in road.points(5.0)), 0.01)


class SurfaceAndFeatureTests(unittest.TestCase):
    def test_a_ground_profile_is_written_as_a_surface_profile(self):
        from landxml_plugin.landxml.common import descendants
        from landxml_plugin.landxml.longsection import read_alignment_profiles
        from landxml_plugin.landxml.parser import load_document
        from landxml_plugin.landxml.writer import GroundProfile, VerticalProfile, alignment_from_pis, landxml_text
        alignment = alignment_from_pis("A", [(0.0, 0.0), (100.0, 0.0)], [])
        ground = GroundProfile("Existing ground", [(0, 10.0), (50, 11.5), (100, 10.5)])
        design = VerticalProfile("Design", [(0, 10.0, 0), (50, 12.0, 20), (100, 11.0, 0)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.xml"
            path.write_text(landxml_text([(alignment, [ground, design])]), encoding="utf-8")
            node = next(descendants(load_document(str(path)).root, "Alignment"))
            kinds = {p.name: p.kind for p in read_alignment_profiles(node)}
        self.assertEqual(kinds, {"Existing ground": "surface", "Design": "design"})
        with self.assertRaises(ValueError):
            GroundProfile("bad", [(0, 1), (0, 2)])

    def test_files_name_their_application_and_are_recognised(self):
        import tempfile
        from landxml_plugin.landxml.parser import load_document
        from landxml_plugin.landxml.writer import landxml_text
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.xml"
            path.write_text(landxml_text([]), encoding="utf-8")
            report = load_document(str(path)).inspect()
        self.assertEqual(report["vendor"], "LandXML Road & Terrain GIS Tools")
        self.assertFalse(any("unidentified" in w for w in report["warnings"]))

    def test_the_elevation_unit_is_written_only_when_given(self):
        from landxml_plugin.landxml.writer import landxml_text
        self.assertNotIn("elevationUnit", landxml_text([]))
        self.assertIn('linearUnit="meter" elevationUnit="meter"', landxml_text([], vertical_unit="meter"))
        self.assertIn('elevationUnit="USSurveyFoot"', landxml_text([], linear_unit="foot", vertical_unit="USSurveyFoot"))
        with self.assertRaises(ValueError):
            landxml_text([], vertical_unit="foot")             # an Imperial elevation in a Metric block

    def test_tin_surfaces_and_feature_lines_read_back(self):
        points = [(0.0, 0.0, 10.0), (10.0, 0.0, 11.0), (10.0, 10.0, 12.0), (0.0, 10.0, 11.0)]
        surface = Surface("EG", points, [(0, 1, 2), (0, 2, 3)])
        line = FeatureLine("Edge", [(0.0, 0.0, 10.0), (10.0, 0.0, 11.0), (10.0, 10.0, 12.0)])
        path = written([], surfaces=[surface], feature_lines=[line])
        self.assertEqual(read_catalog(path).surfaces, ["EG"])
        from landxml_plugin.core import read_tin
        tin = read_tin(path, "EG")
        xyz = tin[0] if isinstance(tin, tuple) else tin.points
        self.assertEqual(len(xyz), 4)
        records, warnings = read_line_features(load_document(path).root)
        self.assertEqual(warnings, [])
        self.assertEqual([(r.name, r.kind, len(r.points)) for r in records], [("Edge", "FeatureLine", 3)])
        self.assertEqual(tuple(records[0].points[1][:3]), (0.0, 10.0, 11.0))              # northing first, then the level
        with self.assertRaises(ValueError):
            landxml_text([], surfaces=[surface, surface])


class ProfileChecks(unittest.TestCase):
    def test_bad_profiles_are_refused(self):
        with self.assertRaises(ValueError):
            VerticalProfile("P", [(0, 10, 0), (0, 11, 0)])
        with self.assertRaises(ValueError):
            VerticalProfile("P", [(0, 10, 50), (100, 11, 0)])
        with self.assertRaises(ValueError):
            VerticalProfile("P", [(0, 10, 0), (100, 12, 120), (150, 11, 80), (300, 11, 0)])   # curves overlap
        with self.assertRaises(ValueError):
            landxml_text([(road(), [VerticalProfile("P", [(0, 10, 0), (5000, 12, 0)])])])          # beyond the alignment

    def test_a_vertical_curve_is_a_symmetric_parabola_joining_the_grades(self):
        p = VerticalProfile("P", [(0, 100.0, 0), (200, 104.0, 100), (400, 100.0, 0)])
        self.assertAlmostEqual(p.elevation(150), 103.0)                          # tangent point: on the incoming grade
        self.assertAlmostEqual(p.elevation(250), 103.0)
        self.assertAlmostEqual(p.elevation(200), 104.0 - 0.04 * 100 / 8)       # mid-ordinate A L / 8 below the PVI


if __name__ == "__main__":
    unittest.main()
