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
from landxml_plugin.landxml.profile import profile_control_points, read_profile_controls
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


from landxml_plugin.landxml.catalog import match_name, read_catalog, suggest_terrain_pair
from landxml_plugin.landxml.design_report import (
    clip_series,
    curvature_series,
    design_report_html,
    design_speeds,
    grade_series,
    horizontal_elements,
    strip_windows,
)
from landxml_plugin.landxml.section_view import build_section_view, difference_regions, view_extent
from landxml_plugin.landxml.superelevation import lane_slopes, read_superelevation

SUPER_XML = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2"><Alignments>
  <Alignment name="Bend" staStart="0">
    <Superelevation staStart="40" staEnd="60">
      <BeginRunoutSta>10</BeginRunoutSta><BeginRunoffSta>20</BeginRunoffSta><FullSuperSta>40</FullSuperSta>
      <FullSuperelev>-5</FullSuperelev><RunoffSta>60</RunoffSta><StartofRunoutSta>80</StartofRunoutSta>
      <EndofRunoutSta>90</EndofRunoutSta></Superelevation>
    <Superelevation staStart="200" staEnd="210"></Superelevation>
    <Feature name="SpeedStation" code="0"><Property label="station" value="0"/><Property label="speed" value="80"/></Feature>
  </Alignment></Alignments></LandXML>"""


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_names_quickly_and_suggests_surfaces(self):
        catalog = read_catalog(str(CORRIDOR))
        self.assertEqual(catalog.surfaces, ["EG", "Design"])
        self.assertEqual(catalog.alignments, ["Synthetic Road"])
        self.assertEqual(catalog.names("section_surfaces"), ["EG Surface", "Synthetic Corridor TOP"])
        self.assertEqual(catalog.names("design_profiles"), ["Synthetic FRL"])
        self.assertEqual(catalog.names("surface_profiles"), ["Synthetic Road_EG Surface"])
        self.assertEqual(catalog.cross_sections["Synthetic Road"], 5)
        self.assertEqual(catalog.roadway_surfaces, ["TOP"])
        base, compare, reason = suggest_terrain_pair(catalog)
        self.assertEqual((base, compare), ("EG", "Design"))
        self.assertIn("volume surface", reason)

    def test_suggestion_without_volume_record(self):
        from landxml_plugin.landxml.catalog import Catalog

        catalog = Catalog(
            surfaces=["BOTTOM", "OGL-REV 0", "TOP"],
            surface_profiles={"A": [("A_OGL-REV 0", "existing")]},
            roadway_surfaces=["BOTTOM", "TOP"],
        )
        base, compare, _reason = suggest_terrain_pair(catalog)
        self.assertEqual((base, compare), ("OGL-REV 0", "BOTTOM"))

    def test_forgiving_name_matching(self):
        names = ["BOTTOM", "OGL-LOT_REV 0", "TOP"]
        self.assertEqual(match_name(names, "ogl", "surface"), "OGL-LOT_REV 0")
        self.assertEqual(match_name(names, "top", "surface"), "TOP")
        self.assertIsNone(match_name(names, "  ", "surface"))
        with self.assertRaisesRegex(ValueError, "Available: 'BOTTOM'"):
            match_name(names, "FRL", "surface")
        with self.assertRaisesRegex(ValueError, "several"):
            match_name(["Road A", "Road B"], "road", "alignment")


class SectionViewTests(unittest.TestCase):
    def test_regions_and_labels(self):
        regions = difference_regions([(-10, -1), (10, 1)], [(-10, 0), (10, 0)])
        self.assertEqual([kind for kind, _ in regions], ["cut", "fill"])
        self.assertAlmostEqual(shoelace(regions[0][1]), 5)
        document = load_document(str(CORRIDOR))
        sections, *_ = read_corridor_sections(document.root)
        view = build_section_view(sections[0], "EG Surface", "Synthetic Corridor TOP", 101.0)
        self.assertAlmostEqual(view["fill_area"], 12)
        self.assertEqual(view["daylight"], (-7, 7))
        self.assertEqual([label["text"] for label in view["slope_labels"]], ["1:2.0", "1:2.0"])
        self.assertEqual(view["shapes"][0][1][0], (-5, 101.0))
        self.assertEqual(view["design_cl"], 101)
        self.assertEqual(view_extent([view]), (-9, 9))
        steep = build_section_view(sections[0], "EG Surface", "Synthetic Corridor TOP", 101.0, steep_ratio=2.5)
        self.assertTrue(all(label["steep"] for label in steep["slope_labels"]))


class DesignReportTests(unittest.TestCase):
    def test_superelevation_lanes_and_speeds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "super.xml"
            path.write_text(SUPER_XML, encoding="utf-8")
            alignment = load_document(str(path)).alignments()[0]
        records = read_superelevation(alignment)
        self.assertEqual(len(records), 1)  # the empty transition is ignored
        left, right = lane_slopes(records, 2.0)
        # A negative (left-curve) rate lifts the right, outside lane.
        self.assertIn((40, 5.0), right)
        self.assertIn((40, -5.0), left)
        self.assertIn((20, 0.0), right)
        self.assertIn((28, -2.0), left)  # reverse crown when the outside lane reaches +2 %
        self.assertEqual(design_speeds(alignment), [(0.0, 80.0)])

    def test_elements_curvature_grades_and_report(self):
        road, _ = read_alignments(str(CIVIL3D), segment_length=1)
        elements = horizontal_elements(road[0])
        self.assertEqual([e["type"] for e in elements], ["line", "curve", "spiral"])
        self.assertAlmostEqual(elements[1]["start"], 110)
        self.assertAlmostEqual(elements[1]["length"], 5 * math.pi)
        self.assertEqual(elements[1]["turn"], "right")
        self.assertAlmostEqual(elements[2]["parameter"], math.sqrt(50 * 7.85))
        curvature = curvature_series(elements)
        self.assertEqual(curvature[2], (110, 100.0))
        self.assertAlmostEqual(curvature[-1][1], 20.0)
        document = load_document(str(CIVIL3D))
        _name, profile = next(document.profiles())
        controls = [c for c in profile_control_points(read_profile_controls(profile)) if c["control_type"] == "VPI"]
        grades = grade_series(controls)
        self.assertAlmostEqual(grades[0][1], 100 / 15)
        self.assertEqual(strip_windows(0, 12000, 5000), [(0, 5000), (5000, 10000), (10000, 12000)])
        self.assertEqual(strip_windows(0, 5500, 5000), [(0, 5500)])
        self.assertEqual(clip_series([(0, 0), (10, 10)], 2, 5), [(2, 2), (5, 5)])
        item = {
            "name": "Road A",
            "alignment": road[0],
            "elements": elements,
            "controls": controls,
            "profile_name": "Design",
            "ground_name": "",
            "runs": [],
            "transitions": [],
            "speeds": [],
            "superelevation": [],
            "slopes": ([], []),
            "swap": False,
        }
        html_text = design_report_html([item], "meter", "Synthetic", "road_minimal.xml")
        for heading in ("Plan", "Curvature diagram", "Gradient diagram", "Horizontal elements", "Vertical controls"):
            self.assertIn(heading, html_text)
        self.assertIn("R10", html_text)
        self.assertIn("<td>1</td>", html_text)


from landxml_plugin.landxml.criteria import list_standards, load_criteria
from landxml_plugin.landxml.review import (
    ProfileModel,
    Review,
    civil3d_profile_text,
    curve_groups,
    parse_speed_ranges,
    recommended_profile,
    review_alignment,
    speed_function,
)
from landxml_plugin.landxml.review_report import recommended_landxml, review_section_html


def element(kind, start, end, radius=None, turn=""):
    return {"type": kind, "start": start, "end": end, "length": end - start, "radius": radius, "turn": turn,
            "radius_start": None, "radius_end": None, "parameter": None, "index": 0}


class CriteriaTests(unittest.TestCase):
    def test_kenya_rdm_tables(self):
        kenya = load_criteria("kenya_rdm13_2025")
        self.assertEqual(kenya.ssd(100, 0), 140)  # Table 3.16
        self.assertEqual(kenya.ssd(100, -6), 155)
        self.assertEqual(kenya.ssd(120, -3), 200)
        self.assertEqual(kenya.k_crest(85), 55)  # Table 6.1
        self.assertEqual(kenya.k_sag(100), 50)  # Table 6.4
        self.assertEqual(kenya.min_vc_length(100), 180)  # Table 6.3
        self.assertEqual(kenya.min_radius(100, 6), 465)  # Table 5.1
        self.assertEqual(kenya.required_superelevation(1000, 100, 6), (4.2, "4.2%"))  # Table 5.10
        self.assertEqual(kenya.required_superelevation(7000, 60, 6)[1], "NC")
        self.assertEqual(kenya.required_superelevation(950, 100, 6)[0], 4.4)  # next smaller tabulated radius
        self.assertEqual(kenya.required_superelevation(60, 100, 6), (None, None))
        self.assertEqual(kenya.runoff_min(100, 6.0), 60)  # Table 5.7 (>= 2 s travel)
        self.assertEqual(kenya.transition_required_radius(100), 590)  # Table 5.8
        self.assertIsNone(kenya.transition_required_radius(60))
        self.assertAlmostEqual(kenya.spiral_min_parameter(100, 500), 210)  # Eq. 5.4
        self.assertEqual(kenya.max_grade("rural", "mountainous"), (6, 8))  # Table 6.5
        self.assertEqual(kenya.max_grade_length(5), 800)  # Table 6.6
        self.assertIsNone(kenya.max_grade_length(3))
        self.assertEqual(kenya.eye_height, 1.05)  # Table 3.13
        self.assertEqual(kenya.object_height, 0.2)

    def test_aashto_formulas_and_listing(self):
        ids = [item[0] for item in list_standards()]
        self.assertIn("kenya_rdm13_2025", ids)
        self.assertIn("aashto_2018", ids)
        aashto = load_criteria("aashto_2018")
        self.assertEqual(aashto.ssd(100), 185)
        self.assertEqual(aashto.k_crest(100), 52)
        self.assertAlmostEqual(aashto.min_vc_length(100), 60)
        self.assertAlmostEqual(aashto.min_radius(100, 8), 100**2 / (127 * 0.20))
        self.assertAlmostEqual(aashto.required_superelevation(500, 100, 8)[0], (10000 / (127 * 500) - 0.12) * 100)
        self.assertIsNone(aashto.max_grade("rural", "rolling"))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.kenya = load_criteria("kenya_rdm13_2025")

    def crest(self, length=100):
        return ProfileModel([
            {"station": 0, "elevation": 100, "length": 0},
            {"station": 500, "elevation": 110, "length": length},
            {"station": 1000, "elevation": 100, "length": 0},
        ])

    def test_profile_model(self):
        model = self.crest()
        curve = model.curves()[0]
        self.assertEqual(curve["kind"], "crest")
        self.assertAlmostEqual(curve["A"], 4)
        self.assertAlmostEqual(curve["K"], 25)
        self.assertAlmostEqual(model.elevation(500), 110 - 4 * 100 / 800)  # PVI mid-ordinate A·L/800
        self.assertAlmostEqual(model.room(1), 1000)

    def test_vertical_k_sight_and_recommended_length(self):
        review = Review(self.kenya, speed_function([], 100))
        model = self.crest()
        review.check_vertical(model)
        review.check_sight(model)
        v1 = next(f for f in review.findings if f["code"] == "V1")
        self.assertEqual(v1["required"], 100)
        self.assertIn("L ≥ 400 m", v1["recommendation"])
        self.assertTrue(any(f["code"] == "S1" for f in review.findings))
        recommended, applied = recommended_profile(model, review.findings, max_change=1.0)
        self.assertEqual(applied, [])  # 1.5 m change exceeds the 1 m cap
        recommended, applied = recommended_profile(model, review.findings, max_change=2.0)
        self.assertEqual(recommended.pvis[1]["length"], 400)
        self.assertAlmostEqual(model.elevation(500) - recommended.elevation(500), 4 * 300 / 800)
        text = civil3d_profile_text(recommended)
        self.assertEqual(text.splitlines()[1], "500.000 110.000 400.000")
        fixed = Review(self.kenya, speed_function([], 100))
        fixed.check_vertical(recommended)
        fixed.check_sight(recommended)
        self.assertFalse([f for f in fixed.findings if f["code"] in ("V1", "S1")])

    def test_phasing_moves_crest_onto_sharp_curve(self):
        elements = [element("line", 0, 400), element("curve", 400, 700, 600, "right"), element("line", 700, 1200)]
        groups = curve_groups(elements)
        self.assertEqual((groups[0]["start"], groups[0]["end"], groups[0]["mid"]), (400, 700, 550))
        model = ProfileModel([
            {"station": 0, "elevation": 100, "length": 0},
            {"station": 750, "elevation": 103, "length": 200},
            {"station": 1200, "elevation": 101.2, "length": 0},
        ])
        review = Review(self.kenya, speed_function([], 100))
        review.check_phasing(model, groups)
        p3 = next(f for f in review.findings if f["code"] == "P3")
        self.assertEqual(p3["severity"], "High")
        fix = p3["fix"]
        self.assertEqual(fix["station"], 550)  # R600 < 1.5 Rmin, so both ends coincide
        self.assertEqual(fix["length"], 300)
        self.assertAlmostEqual(fix["elevation"], 103 + 0.004 * (550 - 750))
        self.assertIn("Both ends coincident", p3["recommendation"])

    def test_horizontal_superelevation_and_tangents(self):
        elements = [element("line", 0, 100), element("curve", 100, 300, 300, "left"), element("line", 300, 400),
                    element("curve", 400, 600, 2000, "left"), element("line", 600, 900)]
        review = Review(self.kenya, speed_function([], 100))
        records = [{"begin_runout": 50, "begin_runoff": 70, "full_start": 100, "rate": -3.0, "full_end": 300,
                    "end_runoff": 330, "end_runout": 350, "curve_start": 100, "curve_end": 300}]
        review.check_horizontal(elements, curve_groups(elements), records)
        codes = {f["code"] for f in review.findings}
        self.assertTrue({"H1", "H2", "E2", "E4", "H6"} <= codes)
        h1 = next(f for f in review.findings if f["code"] == "H1")
        self.assertEqual(h1["required"], 465)
        e1 = next(f for f in review.findings if f["code"] == "E1")  # R2000 at 100 km/h needs 2.6 %
        self.assertEqual(e1["required"], 2.6)

    def test_barrier_warrant_and_curve_sight(self):
        review = Review(self.kenya, speed_function([], 100))
        views = [
            {"station": s, "design": [(-12, 94), (-6, 100), (6, 100), (12, 94)], "ground": [(-20, 94), (20, 94)], "daylight": (-12, 12)}
            for s in (0, 20, 40)
        ]
        review.check_barriers(views)
        barriers = [f for f in review.findings if f["code"] == "R1"]
        self.assertEqual({f["side"] for f in barriers}, {"left", "right"})
        self.assertTrue(all(f["severity"] == "High" for f in barriers))
        self.assertIn("105 m run-out", barriers[0]["recommendation"])  # Table 12.6 at 100 km/h
        cut = [{"station": s, "design": [(-8, 104), (-5, 100), (5, 100), (8, 104)], "ground": [(-30, 104), (30, 104)], "daylight": (-8, 8)}
               for s in (100, 150)]
        groups = curve_groups([element("line", 0, 50), element("curve", 50, 250, 300, "right"), element("line", 250, 400)])
        review.check_curve_sight(groups, cut)
        s2 = next(f for f in review.findings if f["code"] == "S2")
        self.assertEqual(s2["side"], "right")
        self.assertGreater(s2["required"], s2["value"])

    def test_speed_ranges_and_exports(self):
        self.assertEqual(parse_speed_ranges("0-5000:60; 5000-:80"), [(0, 60), (5000, 80)])
        speed = speed_function(parse_speed_ranges("0-5000:60; 5000-:80"), None)
        self.assertEqual((speed(100), speed(6000)), (60, 80))
        with self.assertRaises(ValueError):
            parse_speed_ranges("fast")
        document = load_document(str(CORRIDOR))
        alignment = document.alignments()[0]
        road, _ = read_alignments(str(CORRIDOR))
        _name, profile = next(document.profiles())
        controls = [c for c in profile_control_points(read_profile_controls(profile)) if c["control_type"] == "VPI"]
        result = review_alignment(road[0], controls, self.kenya, design_speed=60)
        xml = recommended_landxml(alignment, result["recommended"], "FRL (review)")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recommended.xml"
            path.write_text(xml, encoding="utf-8")
            reread = load_document(str(path))
            name, prof = next(reread.profiles())
            self.assertEqual(name, "Synthetic Road")
            self.assertEqual(prof.attrib["name"], "FRL (review)")
            self.assertEqual(len(read_profile_controls(prof)), 2)
            self.assertEqual(read_alignments(str(path))[0][0]["vertex_distances"][-1], 200)
        result["criteria"] = self.kenya
        html_text = review_section_html("Synthetic Road", result, "meter", "test")
        self.assertIn("Geometric design review", html_text)


from landxml_plugin.safe_xml import fromstring  # noqa: E402


class DesignSpeedTests(unittest.TestCase):
    def setUp(self):
        self.kenya = load_criteria("kenya_rdm13_2025")

    def test_speed_profile_governing_speed_and_sections(self):
        speeds = speed_function([(0, 100), (3000, 60), (5000, 80)], None)
        self.assertEqual(speeds(2999), 100)
        self.assertEqual(speeds(3000), 60)
        self.assertEqual(speeds.over(2900, 3100), 100)  # an element spanning the change takes the faster speed
        self.assertEqual(speeds.over(3100, 4000), 60)
        self.assertEqual(speeds.sections(0, 6000), [(0, 3000, 100), (3000, 5000, 60), (5000, 6000, 80)])
        self.assertEqual(speed_function([(0, 100)], 50).sections(0, 10), [(0, 10, 50)])  # a typed speed overrides

    def test_civil3d_speed_stations_in_file_order(self):
        xml = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2"><Alignments><Alignment name="A" staStart="0">
          <Feature name="SpeedStation" code="0"><Property label="station" value="0"/><Property label="speed" value="50"/></Feature>
          <Feature name="SpeedStation" code="1"><Property label="station" value="0"/><Property label="speed" value="70"/></Feature>
          <Feature name="SpeedStation" code="2"><Property label="station" value="4200"/><Property label="speed" value="50"/></Feature>
          <Feature name="SpeedStation" code="3"><Property label="station" value="4200"/><Property label="speed" value="40"/></Feature>
        </Alignment></Alignments></LandXML>"""
        alignment = next(e for e in fromstring(xml).iter() if e.tag.endswith("Alignment"))
        self.assertEqual(design_speeds(alignment), [(0.0, 70.0), (4200.0, 40.0)])

    def test_abrupt_change_short_transition_and_sharp_curve(self):
        speeds = speed_function([(0, 100), (3000, 60), (6000, 80), (6400, 100)], None)
        review = Review(self.kenya, speeds)
        rmin_100 = self.kenya.min_radius(100, 6)
        groups = curve_groups([element("line", 0, 3200), element("curve", 3200, 3300, 200, "left"), element("line", 3300, 9000)])
        review.check_speed_changes(0, 9000, groups)
        d1 = [f for f in review.findings if f["code"] == "D1"]
        abrupt = next(f for f in d1 if f["severity"] == "High")
        self.assertEqual(abrupt["value"], 40)
        # Intermediate sections go on the faster side, each 1 km long, in 10 km/h steps.
        self.assertIn("90 km/h over 0–1,000; 80 km/h over 1,000–2,000; 70 km/h over 2,000–3,000", abrupt["recommendation"])
        self.assertTrue(any(f["severity"] == "Low" and f["value"] == 20 for f in d1))  # 60 → 80 acceptable but not preferred
        d2 = next(f for f in review.findings if f["code"] == "D2")
        self.assertEqual((d2["start"], d2["end"], d2["value"]), (6000, 6400, 400))
        d3 = next(f for f in review.findings if f["code"] == "D3")
        self.assertEqual(d3["required"], rmin_100)
        self.assertIn("200 m from the speed change", d3["detail"])

    def test_increasing_speed_places_sections_after_change(self):
        review = Review(self.kenya, speed_function([(0, 50), (2000, 100)], None))
        review.check_speed_changes(0, 8000)
        abrupt = next(f for f in review.findings if f["code"] == "D1")
        self.assertIn("60 km/h over 2,000–3,000", abrupt["recommendation"])
        self.assertIn("90 km/h over 5,000–6,000", abrupt["recommendation"])

    def test_curve_spanning_speed_change_checked_at_faster_speed(self):
        groups = curve_groups([element("line", 0, 900), element("curve", 900, 1100, 300, "right"), element("line", 1100, 2000)])
        slow = Review(self.kenya, speed_function([], 60))
        slow.check_horizontal([], groups)
        mixed = Review(self.kenya, speed_function([(0, 100), (1000, 60)], None))
        mixed.check_horizontal([], groups)
        self.assertFalse(any(f["code"] == "H1" for f in slow.findings))
        self.assertTrue(any(f["code"] == "H1" for f in mixed.findings))

    def test_short_oscillating_sections(self):
        review = Review(self.kenya, speed_function([(0, 50), (1000, 70), (1440, 50), (3000, 70)], None))
        review.check_speed_changes(0, 5000)
        short = [f for f in review.findings if f["code"] == "D2"]
        self.assertEqual([(f["start"], f["end"]) for f in short], [(1000, 1440)])  # 1440–3000 is long enough
        self.assertIn("Merge it into the 50 km/h sections", short[0]["recommendation"])
        dip = Review(self.kenya, speed_function([(0, 70), (2000, 50), (2300, 70)], None))
        dip.check_speed_changes(0, 5000)
        short = next(f for f in dip.findings if f["code"] == "D2")
        self.assertIn("design its elements for 70 km/h", short["recommendation"])

    def test_curve_near_two_speed_changes_reported_once(self):
        review = Review(self.kenya, speed_function([(0, 70), (1000, 50), (1300, 70)], None))
        groups = curve_groups([element("line", 0, 1100), element("curve", 1100, 1200, 90, "left"), element("line", 1200, 3000)])
        review.check_speed_changes(0, 3000, groups)
        d3 = [f for f in review.findings if f["code"] == "D3"]
        self.assertEqual(len(d3), 1)
        self.assertIn("100 m from the speed change", d3[0]["detail"])
