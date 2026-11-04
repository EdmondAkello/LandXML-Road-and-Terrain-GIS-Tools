"""Exact grading quantities from volume (depth) TIN surfaces (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "generic" / "grading_volume.xml"
PLATFORMS = ROOT / "tests" / "fixtures" / "generic" / "platforms.xml"
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.core import read_tin
from landxml_plugin.landxml.catalog import read_catalog
from landxml_plugin.landxml.grading import (
    DEFAULT_BANDS,
    connected_triangles,
    outline_polygons,
    platform_report_html,
    platform_summary,
    triangle_slopes,
    depth_warning,
    fraction_below,
    grading_report_html,
    parse_bands,
    reconcile,
    split_volumes,
    suggest_volume_surface,
    triangles,
    volume_summary,
)
from landxml_plugin.landxml.quantity_units import quantity_units


def _one(points):
    xyz = np.asarray(points, dtype=float)
    return xyz, np.array([[0, 1, 2]])


def _sampled(xyz, n=600):
    """Brute-force midpoint quadrature on an n x n barycentric grid."""
    a, b, c = xyz
    i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    keep = i + j < n
    u = (i[keep] + 1 / 3) / n
    v = (j[keep] + 1 / 3) / n
    lower = np.stack([u, v], 1)
    keep2 = i + j < n - 1
    u2 = (i[keep2] + 2 / 3) / n
    v2 = (j[keep2] + 2 / 3) / n
    uv = np.vstack([lower, np.stack([u2, v2], 1)])
    z = a[2] + uv[:, 0] * (b[2] - a[2]) + uv[:, 1] * (c[2] - a[2])
    area = 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))
    cell = area / (n * n)
    return z, cell


class TriangleFormulaTests(unittest.TestCase):
    def test_lone_fill_vertex_closed_form(self):
        # Zero line cuts at a third of the way up; the fill triangle is 4/9 of the plan area.
        depths, area = triangles(*_one([(0, 0, -1), (1, 0, -1), (0, 1, 2)]))
        fill, cut = split_volumes(depths, area)
        self.assertAlmostEqual(fill[0], 4 / 27, places=12)
        self.assertAlmostEqual(cut[0], 4 / 27, places=12)
        self.assertAlmostEqual(fraction_below(depths, 0.0)[0], 5 / 9, places=12)

    def test_lone_cut_vertex_and_signed_total(self):
        depths, area = triangles(*_one([(0, 0, 2), (1, 0, 2), (0, 1, -1)]))
        fill, cut = split_volumes(depths, area)
        self.assertAlmostEqual(cut[0], 1 / 54, places=12)  # 0.5 * (1/9) * 1 / 3
        self.assertAlmostEqual(fill[0] - cut[0], 0.5 * 3 / 3, places=12)

    def test_whole_fill_cut_and_zero_vertex(self):
        for points, expected_fill, expected_cut in (
            ([(0, 0, 1), (2, 0, 1), (0, 2, 1)], 2.0, 0.0),
            ([(0, 0, -3), (2, 0, -3), (0, 2, -3)], 0.0, 6.0),
            ([(0, 0, 0), (2, 0, 0), (0, 2, 3)], 2.0, 0.0),
            ([(0, 0, -3), (2, 0, 0), (0, 2, 3)], 1.0, 1.0),
        ):
            with self.subTest(points=points):
                fill, cut = split_volumes(*triangles(*_one(points)))
                self.assertAlmostEqual(fill[0], expected_fill, places=12)
                self.assertAlmostEqual(cut[0], expected_cut, places=12)

    def test_random_triangles_match_brute_force_quadrature(self):
        rng = np.random.default_rng(20261031)
        for _ in range(25):
            xyz = np.column_stack([rng.uniform(0, 30, 3), rng.uniform(0, 30, 3), rng.uniform(-4, 4, 3)])
            depths, area = triangles(xyz, np.array([[0, 1, 2]]))
            fill, cut = split_volumes(depths, area)
            z, cell = _sampled(xyz)
            scale = max(float(area[0]) * 4, 1e-9)
            self.assertAlmostEqual(fill[0], z[z > 0].sum() * cell, delta=2e-3 * scale)
            self.assertAlmostEqual(cut[0], -z[z < 0].sum() * cell, delta=2e-3 * scale)
            for level in (-1.5, 0.0, 0.7):
                self.assertAlmostEqual(fraction_below(depths, level)[0] * area[0], (z < level).sum() * cell,
                                       delta=3e-3 * max(float(area[0]), 1e-9))

    def test_flat_triangle_at_a_limit_belongs_to_the_band_above(self):
        depths, _area = triangles(*_one([(0, 0, 1), (1, 0, 1), (0, 1, 1)]))
        self.assertEqual(fraction_below(depths, 1.0)[0], 0.0)
        self.assertEqual(fraction_below(depths, 1.0, inclusive=True)[0], 1.0)

    def test_level_at_the_top_vertex_counts_the_whole_triangle(self):
        for points in ([(0, 0, -2), (1, 0, -0.6), (0, 1, 2)], [(0, 0, -0.6), (1, 0, 2), (0, 1, 2)]):
            with self.subTest(points=points):
                depths, _area = triangles(*_one(points))
                self.assertEqual(fraction_below(depths, 2.0)[0], 1.0)
                self.assertEqual(fraction_below(depths, -0.6 if points[0][2] == -0.6 else -2.0)[0], 0.0)


class VolumeSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.xyz, self.faces, self.meta = read_tin(str(FIXTURE), "Pad Earthworks")

    def test_fixture_matches_closed_form_and_skips_hidden_triangle(self):
        self.assertEqual(self.meta["invisible_face_count"], 1)
        summary = volume_summary(self.xyz, self.faces)
        self.assertAlmostEqual(summary["cut_volume"], 100.0, places=9)
        self.assertAlmostEqual(summary["fill_volume"], 100.0, places=9)
        self.assertAlmostEqual(summary["net_volume"], 0.0, places=9)
        self.assertAlmostEqual(summary["plan_area"], 200.0, places=9)
        self.assertAlmostEqual(summary["cut_area"], 100.0, places=9)
        self.assertAlmostEqual(summary["fill_area"], 100.0, places=9)
        self.assertAlmostEqual(summary["grade_area"], 0.0, places=9)
        self.assertAlmostEqual(summary["max_cut"], 2.0)
        self.assertAlmostEqual(summary["max_fill"], 2.0)
        self.assertEqual(summary["triangle_count"], 4)

    def test_depth_bands_are_exact_and_sum_to_plan_area(self):
        summary = volume_summary(self.xyz, self.faces, (-1, 0, 0.5))
        areas = {row["band"]: row["area"] for row in summary["bands"]}
        # depth = (v - 10) / 5 over a 10 m width: each metre of depth spans 5 m of v, i.e. 50 m2.
        self.assertAlmostEqual(areas["< -1"], 50.0, places=9)
        self.assertAlmostEqual(areas["-1 to 0"], 50.0, places=9)
        self.assertAlmostEqual(areas["0 to 0.5"], 25.0, places=9)
        self.assertAlmostEqual(areas["≥ 0.5"], 75.0, places=9)
        default = volume_summary(self.xyz, self.faces)
        self.assertEqual(len(default["bands"]), len(DEFAULT_BANDS) + 1)
        self.assertAlmostEqual(sum(row["area"] for row in default["bands"]), 200.0, places=9)

    def test_band_parsing(self):
        self.assertEqual(parse_bands(""), DEFAULT_BANDS)
        self.assertEqual(parse_bands("1, -1;0"), (-1.0, 0.0, 1.0))
        for text in ("1, a", "1, 1", "nan"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_bands(text)

    def test_elevation_surface_warns(self):
        xyz, faces, _meta = read_tin(str(FIXTURE), "Pad EG")
        self.assertIn("elevation surface", depth_warning(volume_summary(xyz, faces)))
        self.assertIsNone(depth_warning(volume_summary(self.xyz, self.faces)))


class SelectionAndReconciliationTests(unittest.TestCase):
    def test_volume_surface_detection_needs_unambiguous_evidence(self):
        catalog = read_catalog(str(FIXTURE))
        self.assertEqual(suggest_volume_surface(catalog.surfaces)[0], "Pad Earthworks")
        self.assertIsNone(suggest_volume_surface(["A Earthworks", "B Earthworks", "EG"])[0])
        self.assertIsNone(suggest_volume_surface(["EG", "FG"])[0])
        self.assertEqual(suggest_volume_surface(["EG", "FG", "VOL1"], [("VOL1", "EG", "FG")])[0], "VOL1")

    def test_reconcile_pass_fail_and_not_checked(self):
        computed = {"cut_volume": 100.004, "fill_volume": 99.98, "plan_area": 200.0}
        rows, status = reconcile(computed, {"cut_volume": 100.0, "fill_volume": None, "plan_area": 200.0}, 0.01)
        self.assertEqual(status, "PASS")
        self.assertEqual([row[0] for row in rows], ["cut_volume", "plan_area"])
        _rows, status = reconcile(computed, {"fill_volume": 100.0}, 0.01)
        self.assertEqual(status, "FAIL")
        self.assertEqual(reconcile(computed, {"cut_volume": None}, 0.01), ([], "NOT CHECKED"))
        with self.assertRaises(ValueError):
            reconcile(computed, {}, -1)

    def test_report_lists_quantities_check_and_bands(self):
        xyz, faces, _meta = read_tin(str(FIXTURE), "Pad Earthworks")
        units = quantity_units("meter", "meter")
        summary = units.volumes(volume_summary(xyz, faces))
        rows, status = reconcile(summary, {"cut_volume": 100.0}, 0.01)
        text = grading_report_html(summary, "Pad Earthworks", units, "grading_volume.xml", rows, status, 0.01)
        for expected in ("Grading quantities", "Pad Earthworks", "Reconciliation: PASS", "Plan area by depth", "100.000"):
            self.assertIn(expected, text)
        self.assertNotIn("Reconciliation", grading_report_html(summary, "Pad Earthworks", units))


def _grid(nx, ny, skip=(), z=lambda x, y: 0.0):
    """Unit-cell grid of triangles; ``skip`` lists (i, j) cells to leave out."""
    xyz = np.array([(x, y, z(x, y)) for x in range(nx + 1) for y in range(ny + 1)], dtype=float)
    vid = lambda x, y: x * (ny + 1) + y  # noqa: E731
    faces = []
    for i in range(nx):
        for j in range(ny):
            if (i, j) in skip:
                continue
            faces += [(vid(i, j), vid(i + 1, j), vid(i + 1, j + 1)), (vid(i, j), vid(i + 1, j + 1), vid(i, j + 1))]
    return xyz, np.array(faces)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        xyz, faces, _meta = read_tin(str(PLATFORMS), "Yard FWL")
        self.summary = platform_summary(xyz, faces)

    def test_slope_classes_match_the_fixture(self):
        areas = self.summary["class_areas"]
        self.assertAlmostEqual(self.summary["plan_area"], 880.0, places=9)
        self.assertAlmostEqual(areas["flat"], 800.0, places=9)
        self.assertAlmostEqual(areas["batter"], 80.0, places=9)
        self.assertAlmostEqual(areas["sloping"], 0.0, places=9)
        self.assertAlmostEqual(areas["steep"], 0.0, places=9)

    def test_platform_levels_falls_and_flags(self):
        first, second = sorted(self.summary["platforms"], key=lambda item: item["level_mean"])
        self.assertAlmostEqual(first["area"], 400.0, places=9)
        self.assertAlmostEqual(first["level_mean"], 100.0, places=9)
        self.assertAlmostEqual(first["fall"], 0.0, places=9)
        self.assertIsNone(first["fall_bearing"])
        self.assertTrue(first["below_min_fall"])
        self.assertAlmostEqual(first["share_below_min_fall"], 1.0)
        self.assertAlmostEqual(second["area"], 400.0, places=9)
        self.assertAlmostEqual(second["level_mean"], 101.9, places=9)
        self.assertAlmostEqual(second["level_min"], 101.8, places=9)
        self.assertAlmostEqual(second["level_max"], 102.0, places=9)
        self.assertAlmostEqual(second["fall"], 0.01, places=9)
        self.assertAlmostEqual(second["mean_grade"], 0.01, places=9)
        self.assertAlmostEqual(second["fall_bearing"], 90.0, places=6)
        self.assertFalse(second["below_min_fall"])
        self.assertEqual([item["id"] for item in self.summary["platforms"]], ["P1", "P2"])
        outline = second["polygons"][0][0]
        self.assertEqual((outline[:, 0].min(), outline[:, 0].max()), (24.0, 44.0))

    def test_thresholds(self):
        xyz, faces, _meta = read_tin(str(PLATFORMS), "Yard FWL")
        self.assertEqual(platform_summary(xyz, faces, min_area=500)["platforms"], [])
        steeper_minimum = platform_summary(xyz, faces, min_fall=0.02)
        self.assertTrue(all(item["below_min_fall"] for item in steeper_minimum["platforms"]))
        self.assertAlmostEqual(platform_summary(xyz, faces, flat=0.005)["class_areas"]["flat"], 400.0, places=9)
        for kwargs in ({"flat": 0}, {"flat": 0.5}, {"min_fall": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                platform_summary(xyz, faces, **kwargs)

    def test_slopes_scale_with_the_vertical_unit(self):
        xyz = np.array([(0, 0, 0), (10, 0, 1), (0, 10, 0)], dtype=float)
        self.assertAlmostEqual(triangle_slopes(xyz, np.array([[0, 1, 2]]))[0], 0.1)
        self.assertAlmostEqual(triangle_slopes(xyz, np.array([[0, 2, 1]]), 0.3048)[0], 0.03048)

    def test_outline_keeps_holes_and_separate_parts(self):
        xyz, faces = _grid(3, 3, skip={(1, 1)})
        (outer, holes), = outline_polygons(xyz, faces)
        self.assertAlmostEqual(abs(float(np.dot(outer[:, 0], np.roll(outer[:, 1], -1)) - np.dot(np.roll(outer[:, 0], -1), outer[:, 1]))) / 2, 9.0)
        self.assertEqual(len(holes), 1)
        self.assertEqual(len(holes[0]), 4)
        xyz, faces = _grid(3, 1, skip={(1, 0)})
        labels = connected_triangles(faces, np.ones(len(faces), bool))
        self.assertEqual(len(set(labels.tolist())), 2)
        self.assertEqual(len(outline_polygons(xyz, faces)), 2)

    def test_mixed_orientation_faces_still_join(self):
        xyz, faces = _grid(2, 2)
        faces[::3] = faces[::3][:, [0, 2, 1]]
        self.assertEqual(len(outline_polygons(xyz, faces)), 1)
        self.assertEqual(set(connected_triangles(faces, np.ones(len(faces), bool)).tolist()), {0})

    def test_platform_report(self):
        units = quantity_units("meter", "meter")
        text = platform_report_html(self.summary, "Yard FWL", units, "platforms.xml")
        for expected in ("Platform levels and falls", "Yard FWL", "Platforms (2; 1 below minimum fall)", "90°", "Batter"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
