"""Earthworks, stationing and corridor parsing on synthetic data (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml.corridor import (
    guess_surfaces,
    read_corridor_sections,
    roadway_surface_refs,
    shoelace,
)
from landxml_plugin.landxml.earthworks import (
    compare_profiles,
    corridor_quantities,
    cut_fill_segments,
    design_line,
    exclude_ranges,
    parse_station_ranges,
    regular_rows,
    section_areas,
    side_slopes,
    summarize_segments,
    template_estimate,
)
from landxml_plugin.landxml.geometry import read_alignments
from landxml_plugin.landxml.longsection import (
    ProfileLine,
    default_pair,
    read_alignment_profiles,
)
from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.reports import (
    corridor_report_html,
    profile_report_html,
    station_label,
)
from landxml_plugin.landxml.sections import read_cross_sections
from landxml_plugin.landxml.stationing import StationedPolyline

CORRIDOR = FIXTURES / "civil3d" / "corridor_synthetic.xml"
CIVIL3D = FIXTURES / "civil3d" / "road_minimal.xml"
SQRT5 = math.sqrt(5.0)


def line(name, points, kind="surface"):
    return ProfileLine("A", name, kind, "existing", None, [points])


class StationingTests(unittest.TestCase):
    def test_vertex_distances_use_arc_length(self):
        road, _ = read_alignments(str(CIVIL3D), segment_length=1)
        distances = road[0]["vertex_distances"]
        self.assertEqual(len(distances), len(road[0]["points"]))
        # 10 m tangent + quarter circle of radius 10 + 7.85 m spiral.
        self.assertAlmostEqual(distances[-1], 10 + 5 * math.pi + 7.85, places=6)
        chord = sum(math.dist(a, b) for a, b in zip(road[0]["points"], road[0]["points"][1:]))
        self.assertLess(chord, distances[-1])

    def test_station_offset_round_trip(self):
        polyline = StationedPolyline([(0, 0), (100, 0), (100, 50)], [0, 100, 150], 1000)
        self.assertEqual(polyline.point_at(1050, 0), (50, 0))
        # Right of an eastbound line is south.
        self.assertEqual(polyline.point_at(1050, 5), (50, -5))
        self.assertEqual(polyline.point_at(1120, -3), (97, 20))
        station, offset, distance = polyline.locate(40, 7)
        self.assertAlmostEqual(station, 1040)
        self.assertAlmostEqual(offset, -7)
        self.assertAlmostEqual(distance, 7)
        self.assertIsNone(polyline.point_at(999))


class ProfileTests(unittest.TestCase):
    def test_surface_profile_gaps_and_defaults(self):
        xml = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2"><Alignments>
          <Alignment name="A" staStart="0"><Profile>
            <ProfSurf name="Short" state="existing"><PntList2D>0 10 50 10</PntList2D></ProfSurf>
            <ProfSurf name="Long" state="existing"><PntList2D>0 10 40 12 40 12 60 14 100 10 30 9 35 9</PntList2D></ProfSurf>
            <ProfAlign name="FRL"><PVI>0 11</PVI><PVI>100 11</PVI></ProfAlign>
          </Profile></Alignment></Alignments></LandXML>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.xml"
            path.write_text(xml, encoding="utf-8")
            alignment = load_document(str(path)).alignments()[0]
        profiles = read_alignment_profiles(alignment)
        design, ground = default_pair(profiles)
        self.assertEqual(design.name, "FRL")
        self.assertEqual(ground.name, "Long")
        self.assertEqual(len(ground.parts), 2)
        self.assertEqual(ground.parts[0], [(0, 10), (40, 12), (60, 14), (100, 10)])
        self.assertAlmostEqual(ground.elevation_at(50), 13)
        self.assertIsNone(ProfileLine("A", "x", "surface", parts=[[(0, 1), (5, 1)]]).elevation_at(6))

    def test_comparison_inserts_exact_transitions(self):
        design = line("FRL", [(0, 101), (200, 99)], "design")
        ground = line("EG", [(0, 100), (200, 100)])
        runs = compare_profiles(design, ground)
        stations = [row["station"] for row in runs[0]]
        self.assertIn(100, stations)
        segments, transitions = cut_fill_segments(runs, high_fill=0.9, deep_cut=2)
        self.assertEqual([s["kind"] for s in segments], ["fill", "cut"])
        self.assertEqual(transitions[0]["station"], 100)
        self.assertEqual(transitions[0]["label"], "fill→cut")
        self.assertAlmostEqual(segments[0]["max_depth"], 1)
        self.assertEqual(segments[0]["severity"], "high fill")
        self.assertEqual(segments[1]["severity"], "")
        self.assertAlmostEqual(segments[0]["mean_depth"], 0.5)
        summary = summarize_segments(segments)
        self.assertEqual(summary["transitions"], 1)
        self.assertAlmostEqual(summary["cut_percent"], 50)
        rows = regular_rows(runs, 30)
        self.assertEqual([round(r["station"]) for r in rows], [0, 30, 60, 90, 120, 150, 180, 200])
        self.assertAlmostEqual(rows[1]["depth"], 0.7)

    def test_tolerance_keeps_transitions_across_grade(self):
        design = line("FRL", [(0, 100), (100, 100), (200, 100)], "design")
        ground = line("EG", [(0, 99), (90, 99.95), (110, 100.05), (200, 101)])
        runs = compare_profiles(design, ground)
        segments, transitions = cut_fill_segments(runs, tolerance=0.1)
        self.assertEqual([s["kind"] for s in segments], ["fill", "grade", "cut"])
        self.assertEqual(len(transitions), 1)
        self.assertAlmostEqual(transitions[0]["station"], 100)
        self.assertEqual(summarize_segments(segments)["transitions"], 1)

    def test_exclusions_split_runs(self):
        self.assertEqual(parse_station_ranges("0+120-0+150; 10 to 20"), [(10, 20), (120, 150)])
        with self.assertRaises(ValueError):
            parse_station_ranges("abc")
        design = line("FRL", [(0, 101), (200, 99)], "design")
        ground = line("EG", [(0, 100), (200, 100)])
        runs = exclude_ranges(compare_profiles(design, ground), [(90, 110)])
        self.assertEqual([(r[0]["station"], r[-1]["station"]) for r in runs], [(0, 90), (110, 200)])
        self.assertAlmostEqual(runs[1][0]["depth"], -0.1)

    def test_level_section_template(self):
        design = line("FRL", [(0, 101), (200, 99)], "design")
        ground = line("EG", [(0, 100), (200, 100)])
        estimate = template_estimate(compare_profiles(design, ground), 10, 2, 2)
        # Trapezoids over the breakpoints 0, 100, 200: (12 + 0) / 2 × 100.
        self.assertAlmostEqual(estimate["fill_volume"], 600)
        self.assertAlmostEqual(estimate["cut_volume"], 600)
        self.assertAlmostEqual(estimate["fill_slope_area"], 2 * 100 * SQRT5 / 2)


class SectionTests(unittest.TestCase):
    def test_section_areas_split_at_crossings(self):
        ground = [(-10, 0), (10, 0)]
        design = [(-10, -1), (10, 1)]
        cut, fill, low, high = section_areas(design, ground)
        self.assertAlmostEqual(cut, 5)
        self.assertAlmostEqual(fill, 5)
        self.assertEqual((low, high), (-10, 10))
        self.assertIsNone(section_areas([(20, 0), (30, 0)], ground))

    def test_side_slopes_classify_fill_cut_and_steep(self):
        ground = [(-30, 100), (30, 100)]
        embankment = [(-7, 100), (-5, 101), (5, 101), (7, 100)]
        slopes = side_slopes(embankment, ground, -5, 5, flat_ratio=10, steep_ratio=1.5)
        self.assertAlmostEqual(slopes["left"]["fill"], SQRT5)
        self.assertAlmostEqual(slopes["right"]["fill"], SQRT5)
        self.assertEqual(slopes["left"]["cut"], 0)
        self.assertEqual(slopes["left"]["daylight"], -7)
        self.assertEqual(slopes["left"]["hinge"], -5)
        steep = side_slopes(embankment, ground, -5, 5, steep_ratio=2.5)
        self.assertAlmostEqual(steep["right"]["steep_fill"], SQRT5)
        cutting = [(-7, 100), (-5, 99), (5, 99), (7, 100)]
        slopes = side_slopes(cutting, ground, flat_ratio=10)
        self.assertAlmostEqual(slopes["left"]["cut"], SQRT5)
        self.assertEqual(slopes["left"]["hinge"], -5)

    def test_shoelace(self):
        self.assertAlmostEqual(shoelace([(0, 0), (4, 0), (4, 3)]), 6)


class CorridorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = load_document(str(CORRIDOR))
        cls.sections, cls.warnings, cls.surfaces, cls.shapes = read_corridor_sections(cls.document.root)
        alignment = cls.document.alignments()[0]
        cls.profiles = read_alignment_profiles(alignment)
        cls.design, cls.ground = default_pair(cls.profiles)

    def test_parsing_and_detection(self):
        self.assertFalse(self.warnings)
        self.assertEqual(len(self.sections), 5)
        self.assertEqual(self.surfaces, ["EG Surface", "Synthetic Corridor TOP"])
        self.assertEqual(self.shapes, ["Pave", "Base"])
        section = self.sections[0]
        self.assertEqual(section.shapes[0].declared_area, 1.0)
        self.assertAlmostEqual(section.shapes[1].area, 2.0)
        self.assertEqual(section.datum_links, [[(-5, -0.3), (5, -0.3)]])
        self.assertEqual(section.other_links, 1)
        self.assertEqual(roadway_surface_refs(self.document.root), ["TOP"])
        ground, design = guess_surfaces(
            self.sections, [p for p in self.profiles if p.kind == "surface"], ["TOP"]
        )
        self.assertEqual((ground, design), ("EG Surface", "Synthetic Corridor TOP"))

    def test_station_relative_sections_are_not_map_coordinates(self):
        records, warnings = read_cross_sections(self.document.root)
        self.assertEqual(records, [])
        self.assertTrue(any("station-relative" in item for item in warnings))

    def test_quantities_match_closed_form(self):
        result = corridor_quantities(
            self.sections, "EG Surface", "Synthetic Corridor TOP", self.design.elevation_at
        )
        rows = {row["station"]: row for row in result["rows"]}
        # Level section on flat ground: area = h(10 + 2h).
        self.assertAlmostEqual(rows[0]["fill_area"], 12)
        self.assertAlmostEqual(rows[50]["fill_area"], 5.5)
        self.assertAlmostEqual(rows[150]["cut_area"], 5.5)
        self.assertAlmostEqual(rows[0]["fill_slope_length"], 2 * SQRT5)
        self.assertAlmostEqual(rows[200]["cut_slope_length"], 2 * SQRT5)
        self.assertAlmostEqual(rows[0]["centre_depth"], 1)
        totals = result["totals"]
        self.assertAlmostEqual(totals["fill_volume"], 575)
        self.assertAlmostEqual(totals["cut_volume"], 575)
        self.assertAlmostEqual(totals["fill_slope_area"], 100 * SQRT5)
        self.assertAlmostEqual(totals["cut_slope_area"], 100 * SQRT5)
        self.assertEqual(totals["steep_fill_area"], 0)
        self.assertAlmostEqual(result["materials"]["Pave"], 200)
        self.assertAlmostEqual(result["materials"]["Base"], 400)
        self.assertAlmostEqual(result["intervals"][-1]["mass_ordinate"], 0)
        self.assertAlmostEqual(result["intervals"][1]["mass_ordinate"], -575)

    def test_datum_and_controls(self):
        datum = corridor_quantities(
            self.sections, "EG Surface", "Synthetic Corridor TOP", self.design.elevation_at, use_datum=True
        )
        rows = {row["station"]: row for row in datum["rows"]}
        self.assertTrue(rows[0]["datum_used"])
        self.assertAlmostEqual(rows[0]["fill_area"], 0.7 * 12)
        self.assertAlmostEqual(rows[100]["cut_area"], 3)
        # Grassed slopes stay on the finished surface.
        self.assertAlmostEqual(rows[0]["fill_slope_length"], 2 * SQRT5)
        line_without_grade, used = design_line(self.sections[0], "Synthetic Corridor TOP", True, None)
        self.assertFalse(used)
        spaced = corridor_quantities(
            self.sections, "EG Surface", "Synthetic Corridor TOP", max_spacing=40, excluded=[(140, 160)]
        )
        self.assertEqual(spaced["intervals"], [])
        self.assertTrue(any("excluded" in reason for _, reason in spaced["skipped"]))
        factored = corridor_quantities(self.sections, "EG Surface", "Synthetic Corridor TOP", cut_factor=0.8)
        self.assertAlmostEqual(factored["totals"]["net_volume"], 575 - 0.8 * 575)

    def test_reports_and_labels(self):
        self.assertEqual(station_label(1234.5, "meter"), "1+234.50")
        self.assertEqual(station_label(1234.5, "USSurveyFoot"), "12+34.50")
        self.assertEqual(station_label(50, "meter"), "0+050.00")
        runs = compare_profiles(self.design, self.ground)
        segments, transitions = cut_fill_segments(runs)
        html = profile_report_html(
            [
                dict(
                    alignment="Synthetic Road",
                    design=self.design.name,
                    ground=self.ground.name,
                    runs=runs,
                    segments=segments,
                    transitions=transitions,
                    summary=summarize_segments(segments),
                )
            ],
            "meter",
        )
        self.assertIn("<svg", html)
        self.assertIn("0+100.00", html)
        quantities = corridor_quantities(self.sections, "EG Surface", "Synthetic Corridor TOP")
        html = corridor_report_html(
            [dict(alignment="Synthetic Road", ground="EG Surface", design="TOP", quantities=quantities)], "meter"
        )
        self.assertIn("Mass haul", html)
        self.assertIn("Pave", html)


class InspectTests(unittest.TestCase):
    def test_surface_volumes_and_profiles_reported(self):
        report = load_document(str(CORRIDOR)).inspect()
        self.assertEqual(report["surface_profiles"], 1)
        self.assertEqual(report["alignments"][0]["surface_profiles"], ["Synthetic Road_EG Surface"])
        volume = report["surface_volumes"][0]
        self.assertEqual((volume["base_surface"], volume["compare_surface"]), ("EG", "Design"))
        self.assertEqual(volume["fill_volume"], 100)


if __name__ == "__main__":
    unittest.main()
