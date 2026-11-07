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
BASIN = ROOT / "tests" / "fixtures" / "generic" / "basin.xml"
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
    balance_report_html,
    basin_report_html,
    change_grid,
    comparison_report_html,
    revision_comparison,
    scale_comparison,
    zone_labels,
    earthwork_balance,
    parse_factors,
    spill_level,
    stage_storage,
    stored_volume,
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


def _basin_volume(h):
    """Prismoidal volume of the fixture basin below level h (exact for 1:1 sides on a 10 x 6 floor)."""
    area = lambda z: (10 + 2 * z) * (6 + 2 * z)  # noqa: E731
    return h / 6 * (area(0) + 4 * area(h / 2) + area(h))


class BasinTests(unittest.TestCase):
    def setUp(self):
        self.xyz, self.faces, _meta = read_tin(str(BASIN), "Test Pond")

    def test_storage_and_wet_area_match_closed_form(self):
        for level in (0.4, 1.0, 1.7, 2.5, 3.0):
            with self.subTest(level=level):
                volume, wet = stored_volume(self.xyz, self.faces, level)
                self.assertAlmostEqual(volume, _basin_volume(level), places=9)
                self.assertAlmostEqual(wet, (10 + 2 * level) * (6 + 2 * level), places=9)
        self.assertEqual(stored_volume(self.xyz, self.faces, 0.0), (0.0, 0.0))
        self.assertAlmostEqual(stored_volume(self.xyz, self.faces, 4.0)[0], 360.0 + 192.0, places=9)

    def test_stage_storage_table_rim_and_freeboard(self):
        summary = stage_storage(self.xyz, self.faces, freeboard=0.5, step=0.5, required=300.0)
        self.assertEqual((summary["floor"], summary["spill"], summary["rim"]), (0.0, 3.0, 3.0))
        self.assertAlmostEqual(summary["rim_volume"], 360.0, places=9)
        self.assertAlmostEqual(summary["design_volume"], 270.0 + 5 / 6, places=9)
        self.assertAlmostEqual(summary["rim_area"], 192.0, places=9)
        self.assertEqual([row["level"] for row in summary["rows"]], [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        self.assertEqual(summary["status"], "PASS TO RIM ONLY")
        self.assertEqual(stage_storage(self.xyz, self.faces, required=250.0)["status"], "PASS")
        self.assertEqual(stage_storage(self.xyz, self.faces, required=400.0)["status"], "FAIL")
        self.assertEqual(stage_storage(self.xyz, self.faces)["status"], "NOT CHECKED")
        lower = stage_storage(self.xyz, self.faces, rim=2.2, freeboard=0.3, step=1.0)
        self.assertEqual([round(row["level"], 9) for row in lower["rows"]], [1.0, 1.9, 2.0, 2.2])
        self.assertAlmostEqual(lower["design_volume"], _basin_volume(1.9), places=9)

    def test_spill_level_is_the_lowest_edge_point(self):
        self.assertEqual(spill_level(self.xyz, self.faces), 3.0)
        notched = self.xyz.copy()
        notched[5, 2] = 2.0  # one top corner lowered: water leaves there first
        self.assertEqual(spill_level(notched, self.faces), 2.0)

    def test_invalid_inputs(self):
        for kwargs in ({"rim": -1.0}, {"step": 0}, {"freeboard": -0.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                stage_storage(self.xyz, self.faces, **kwargs)

    def test_basin_report(self):
        units = quantity_units("meter", "meter")
        summary = stage_storage(self.xyz, self.faces, required=300.0)
        text = basin_report_html(summary, "Test Pond", units, "basin.xml")
        for expected in ("Basin stage–storage", "Capacity: PASS TO RIM ONLY", "Test Pond", "360.000", "Stage–storage table"):
            self.assertIn(expected, text)


class BalanceTests(unittest.TestCase):
    SITES = [{"name": "A", "cut": 1000.0, "fill": 200.0, "area": 300.0},
             {"name": "B", "cut": 100.0, "fill": 900.0, "area": 200.0}]

    def test_site_to_site_transfer_then_import(self):
        balance = earthwork_balance(self.SITES, (0.9,))
        item = balance["scenarios"][0]
        self.assertEqual((balance["cut"], balance["fill"], balance["area"]), (1100.0, 1100.0, 500.0))
        self.assertAlmostEqual(item["sites"][0]["surplus"], 700.0)
        self.assertAlmostEqual(item["sites"][1]["shortfall"], 810.0)
        self.assertAlmostEqual(item["transfer"], 700.0)
        self.assertAlmostEqual(item["import_compacted"], 110.0)
        self.assertAlmostEqual(item["import_in_place"], 110.0 / 0.9)
        self.assertEqual(item["spoil_in_place"], 0.0)

    def test_spoil_unsuitable_topsoil_and_single_site(self):
        spoil = earthwork_balance([{"name": "A", "cut": 1000.0, "fill": 200.0, "area": 300.0}], (0.9,), 0.1, 0.15)
        item = spoil["scenarios"][0]
        self.assertEqual(item["transfer"], 0.0)
        self.assertAlmostEqual(item["sites"][0]["usable"], 810.0)
        self.assertAlmostEqual(item["spoil_in_place"], 610.0 / 0.9)
        self.assertAlmostEqual(item["unsuitable_in_place"], 100.0)
        self.assertAlmostEqual(spoil["topsoil"], 45.0)
        unsuitable = earthwork_balance(self.SITES, (0.9,), 0.1)["scenarios"][0]
        self.assertAlmostEqual(unsuitable["import_compacted"], 819.0 - 610.0)

    def test_factors_and_validation(self):
        self.assertEqual(parse_factors(""), (0.85, 0.90, 0.95))
        self.assertEqual(parse_factors("0.9; 1.1"), (0.9, 1.1))
        for text in ("0", "x", "3"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_factors(text)
        for kwargs in ({"unsuitable": 1.0}, {"topsoil_depth": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                earthwork_balance(self.SITES, **kwargs)
        with self.assertRaises(ValueError):
            earthwork_balance([])
        self.assertEqual([item["factor"] for item in earthwork_balance(self.SITES)["scenarios"]], [0.85, 0.90, 0.95])

    def test_balance_report(self):
        units = quantity_units("meter", "meter")
        text = balance_report_html(earthwork_balance(self.SITES, (0.9,), 0.1, 0.15), units, "balance.xml")
        for expected in ("Earthwork balance", "Balance by shrinkage factor", "Factor 0.90: by site", "Topsoil stripping", "Unsuitable cut"):
            self.assertIn(expected, text)


class RevisionComparisonTests(unittest.TestCase):
    def setUp(self):
        # depth = (y - 10) / 5 on a 10 x 20 m grid of unit cells; the second revision adds 1 m of fill everywhere
        self.first = _grid(10, 20, z=lambda x, y: (y - 10) / 5)
        raised = self.first[0].copy()
        raised[:, 2] += 1.0
        self.second = (raised, self.first[1])
        self.zones = [("South", [([(0, 0), (10, 0), (10, 10), (0, 10)], [])]),
                      ("North", [([(0, 10), (10, 10), (10, 20), (0, 20)], [])])]

    def test_totals_and_zone_changes_match_closed_form(self):
        result = revision_comparison(self.first, self.second, self.zones)
        total = result["total"]
        self.assertAlmostEqual(total["cut_a"], 100.0, places=9)
        self.assertAlmostEqual(total["fill_a"], 100.0, places=9)
        self.assertAlmostEqual(total["cut_b"], 25.0, places=9)
        self.assertAlmostEqual(total["fill_b"], 225.0, places=9)
        self.assertAlmostEqual(total["net_change"], 200.0, places=9)
        zones = {row["name"]: row for row in result["zones"]}
        self.assertEqual(list(zones), ["South", "North"])
        self.assertAlmostEqual(zones["South"]["cut_a"], 100.0, places=9)
        self.assertAlmostEqual(zones["South"]["fill_b"], 25.0, places=9)
        self.assertAlmostEqual(zones["South"]["cut_b"], 25.0, places=9)
        self.assertAlmostEqual(zones["North"]["fill_a"], 100.0, places=9)
        self.assertAlmostEqual(zones["North"]["fill_b"], 200.0, places=9)
        scaled = scale_comparison(result, 2.0)
        self.assertAlmostEqual(scaled["total"]["fill_b"], 450.0, places=9)
        self.assertEqual(scaled["total"]["area_b"], total["area_b"])

    def test_without_zones_and_outside_zone(self):
        result = revision_comparison(self.first, self.second, self.zones[:1])
        self.assertEqual([row["name"] for row in result["zones"]], ["South", "Outside zones"])
        self.assertEqual([row["name"] for row in revision_comparison(self.first, self.second)["zones"]], ["Outside zones"])

    def test_zone_labels_respect_holes_and_order(self):
        ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
        hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
        points = np.array([(5, 5), (1, 1), (20, 20)], dtype=float)
        labels = zone_labels(points, [("Ring", [(ring, [hole])]), ("All", [(ring, [])])])
        self.assertEqual(labels.tolist(), ["All", "Ring", "Outside zones"])

    def test_change_grid_treats_missing_depth_as_zero(self):
        nodata = -9999.0
        first = np.array([[1.0, nodata], [nodata, 2.0]], dtype=np.float32)
        second = np.array([[3.0, 0.5], [nodata, nodata]], dtype=np.float32)
        self.assertEqual(change_grid(first, second, nodata).tolist(), [[2.0, 0.5], [nodata, -2.0]])

    def test_comparison_report(self):
        units = quantity_units("meter", "meter")
        result = revision_comparison(self.first, self.second, self.zones)
        text = comparison_report_html(result, ("Rev B", "Rev C"), units, ("b.xml", "c.xml"))
        for expected in ("Grading revision comparison", "By zone", "Rev B cut", "Rev C fill", "South", "existing-ground"):
            self.assertIn(expected, text)
        self.assertNotIn("By zone", comparison_report_html(revision_comparison(self.first, self.second), ("A", "B"), units))


if __name__ == "__main__":
    unittest.main()
