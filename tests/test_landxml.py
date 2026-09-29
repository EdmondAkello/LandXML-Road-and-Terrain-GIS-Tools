"""Parser and Processing regressions for synthetic and locally supplied sources."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
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

from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.features import read_line_features
from landxml_plugin.landxml.sections import read_cross_sections
from landxml_plugin.landxml.geometry import read_alignments, regular_station_distances
from landxml_plugin.landxml.profile import (
    ProfileMapPlacement,
    profile_control_points,
    read_profile_controls,
    read_vertical_profile,
)
from landxml_plugin.core import read_tin, transform_vertices


OPENROADS = FIXTURES / "openroads" / "terrain_minimal.xml"
CIVIL3D = FIXTURES / "civil3d" / "road_minimal.xml"
GENERIC = FIXTURES / "generic" / "multiple.xml"


class ParserTests(unittest.TestCase):
    def test_profile_map_placement_clips_partial_station_range(self):
        placement = ProfileMapPlacement(
            [(0, 0), (100, 0)], [(1000, 2000), (1100, 2000)],
            100, 10, 25, 2,
        )
        samples = placement.clipped_samples([(50, 0), (150, 20), (250, 0)])
        self.assertEqual(samples, [(100, 10), (150, 20), (200, 10)])
        self.assertEqual(placement.point(*samples[0]), (1000, 2025))
        self.assertIsNone(placement.point(250, 0))

    def test_station_grid_uses_absolute_station_multiples(self):
        distances = regular_station_distances(103.19, 60, 20)
        self.assertEqual([station for _, station in distances], [120, 140, 160])
        self.assertAlmostEqual(distances[0][0], 16.81)
        self.assertEqual(
            regular_station_distances(103.19, 60, 20, include_end=True)[-1][1],
            163.19,
        )

    def test_civil3d_detection_and_entities(self):
        report = load_document(str(CIVIL3D)).inspect()
        self.assertEqual(report["vendor"], "Civil 3D")
        self.assertEqual(report["profiles"], 1)
        self.assertEqual(report["cross_sections"], 1)

    def test_generic_namespace_and_vendor(self):
        report = load_document(str(GENERIC)).inspect()
        self.assertEqual(report["vendor"], "generic")
        self.assertEqual(report["version"], "1.1")

    def test_openroads_terrain_structure(self):
        report = load_document(str(OPENROADS)).inspect()
        self.assertEqual(report["vendor"], "OpenRoads Designer")
        self.assertEqual(report["surfaces"][0]["points"], 3)
        self.assertEqual(report["surfaces"][0]["triangles"], 1)
        self.assertEqual(report["breaklines"], 1)
        self.assertIsNone(report["declared_crs"])
        self.assertIsNone(report["vertical_unit"])

    def test_horizontal_tangent(self):
        road, warnings = read_alignments(str(CIVIL3D))
        self.assertFalse(warnings)
        self.assertEqual(road[0]["segments"][0]["geometry_type"], "line")
        self.assertEqual(road[0]["segments"][0]["points"], [(0.0, 0.0), (10.0, 0.0)])

    def test_circular_curve(self):
        road, _ = read_alignments(str(CIVIL3D), segment_length=1)
        segment = road[0]["segments"][1]
        self.assertEqual(segment["geometry_type"], "curve")
        self.assertEqual(segment["radius"], 10)
        self.assertEqual(segment["points"][0], (10, 0))
        self.assertEqual(segment["points"][-1], (20, 10))

    def test_spiral(self):
        road, _ = read_alignments(str(CIVIL3D), segment_length=1)
        segment = road[0]["segments"][2]
        self.assertEqual(segment["geometry_type"], "spiral")
        self.assertEqual(segment["points"][-1], (27.84, 10.205))
        self.assertGreater(len(segment["points"]), 2)

    def test_unsupported_spiral_is_reported_without_partial_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "other_spiral.xml"
            path.write_text(
                CIVIL3D.read_text(encoding="utf-8").replace(
                    'spiType="clothoid"', 'spiType="bloss"'
                ),
                encoding="utf-8",
            )
            alignments, warnings = read_alignments(str(path))
            self.assertEqual(alignments, [])
            self.assertTrue(
                any("only explicitly declared clothoids" in item for item in warnings)
            )

    def test_vertical_profile_and_grades(self):
        doc = load_document(str(CIVIL3D))
        name, profile = next(doc.profiles())
        self.assertEqual(name, "Road A")
        controls = read_profile_controls(profile)
        self.assertEqual(controls[1]["curve_type"], "crest")
        self.assertAlmostEqual(controls[1]["grade_in"], 1 / 15)
        points = profile_control_points(controls)
        self.assertEqual(
            [(point["control_type"], point["station"]) for point in points],
            [("VPI", 100), ("VPC", 110), ("VPI", 115), ("VPT", 120), ("VPI", 140)],
        )
        self.assertAlmostEqual(points[1]["elevation"], 2 / 3)
        self.assertAlmostEqual(points[2]["k_value"], 3.75)
        self.assertAlmostEqual(points[3]["elevation"], 1.2)
        samples = read_vertical_profile(profile, 1)
        self.assertEqual(samples[0], (100, 0))
        self.assertEqual(samples[-1], (140, 2))

    def test_tin_topology_and_elevations(self):
        points, faces, meta = read_tin(str(OPENROADS))
        self.assertEqual(points.shape, (3, 3))
        self.assertEqual(faces.tolist(), [[0, 1, 2]])
        self.assertEqual((meta["zmin"], meta["zmax"]), (10, 12))

    def test_openroads_breakline_metadata(self):
        doc = load_document(str(OPENROADS))
        records, warnings = read_line_features(doc.root)
        self.assertFalse(warnings)
        record = records[0]
        self.assertEqual(record.surface_name, "Synthetic terrain")
        self.assertEqual(record.feature_code, "Breakline")
        self.assertEqual(record.breakline_type, "standard")
        self.assertEqual(record.properties, {"id": "1", "UsrID": "42"})
        self.assertIsNone(record.name)

    def test_ftus_unit_is_preserved(self):
        doc = load_document(str(OPENROADS))
        self.assertEqual(doc.horizontal_unit, "USSurveyFoot")
        points, _, _ = read_tin(str(OPENROADS))
        self.assertEqual(transform_vertices(points, "stored").tolist(), points.tolist())

    def test_meter_unit_is_preserved(self):
        self.assertEqual(load_document(str(CIVIL3D)).horizontal_unit, "meter")

    def test_international_foot_is_distinct(self):
        self.assertEqual(load_document(str(GENERIC)).horizontal_unit, "foot")
        self.assertNotEqual(
            load_document(str(GENERIC)).horizontal_unit,
            load_document(str(OPENROADS)).horizontal_unit,
        )

    def test_invalid_xml_and_wrong_root(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.xml"
            bad.write_text("<LandXML><Surfaces>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Cannot parse LandXML"):
                load_document(str(bad))
            bad.write_text("<not-landxml/>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Expected a LandXML root"):
                load_document(str(bad))
            bad.write_text(
                '<!DOCTYPE LandXML [<!ENTITY x "unsafe">]><LandXML>&x;</LandXML>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "DOCTYPE"):
                load_document(str(bad))

    def test_multiple_alignments(self):
        alignments, warnings = read_alignments(str(GENERIC))
        self.assertFalse(warnings)
        self.assertEqual([item["name"] for item in alignments], ["First", "Second"])

    def test_multiple_surfaces(self):
        doc = load_document(str(GENERIC))
        self.assertEqual(
            [item["name"] for item in doc.inspect()["surfaces"]], ["One", "Two"]
        )
        _, _, meta = read_tin(str(GENERIC), "Two")
        self.assertEqual(meta["zmin"], 2)

    def test_duplicate_surface_names_are_not_selected_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicates.xml"
            path.write_text(
                GENERIC.read_text(encoding="utf-8").replace('name="Two"', 'name="One"'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                read_tin(str(path), "One")
            _, _, meta = read_tin(str(path), surface_index=1)
            self.assertEqual(meta["zmin"], 2)

    def test_absolute_cross_section_keeps_alignment(self):
        records, warnings = read_cross_sections(load_document(str(CIVIL3D)).root)
        self.assertFalse(warnings)
        self.assertEqual(records[0].alignment_name, "Road A")
        self.assertEqual(records[0].station, 110)


if __name__ == "__main__":
    unittest.main()
