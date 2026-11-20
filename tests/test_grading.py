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
from landxml_plugin.landxml.tin_overlay import candidate_pairs, depth_tin


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


class ReportChartTests(unittest.TestCase):
    def test_every_grading_report_has_its_chart(self):
        from landxml_plugin.landxml.grading_charts import bars_svg, depth_bands_svg, platforms_svg, stage_storage_svg

        units = quantity_units("meter", "meter")
        xyz, faces, _meta = read_tin(str(FIXTURE), "Pad Earthworks")
        volumes = units.volumes(volume_summary(xyz, faces))
        self.assertIn("<svg", grading_report_html(volumes, "Pad Earthworks", units))
        pxyz, pfaces, _meta = read_tin(str(PLATFORMS), "Yard FWL")
        platforms = platform_summary(pxyz, pfaces)
        text = platform_report_html(platforms, "Yard FWL", units)
        self.assertIn("marker-end='url(#fall)'", text)  # the 1 % platform gets a fall arrow
        bxyz, bfaces, _meta = read_tin(str(BASIN), "Test Pond")
        self.assertIn("Freeboard level", basin_report_html(stage_storage(bxyz, bfaces, required=300.0), "Test Pond", units))
        balance = earthwork_balance(BalanceTests.SITES, (0.9,))
        self.assertEqual(balance_report_html(balance, units).count("<svg"), 2)
        grid = _grid(4, 4, z=lambda x, y: y - 2)
        self.assertIn("<svg", comparison_report_html(revision_comparison(grid, grid), ("A", "B"), units))
        self.assertEqual(depth_bands_svg([], units), "")
        self.assertEqual(platforms_svg([]), "")
        self.assertEqual(stage_storage_svg({"rows": []}, units), "")
        self.assertIn("<rect", bars_svg(["x"], [("a", "#000000", [-1.0])], "m³"))



def grid_tin(n, height, size=100.0, jitter=0.0, seed=0, diagonal=0):
    """Square grid TIN with optional interior jitter (kept small enough not to fold triangles)."""
    rng = np.random.default_rng(seed)
    xs = np.linspace(0.0, size, n + 1)
    x, y = (a.ravel() for a in np.meshgrid(xs, xs))
    inner = (x > 0) & (x < size) & (y > 0) & (y < size)
    x = x + inner * rng.uniform(-jitter, jitter, x.size)
    y = y + inner * rng.uniform(-jitter, jitter, y.size)
    faces = []
    for r in range(n):
        for c in range(n):
            a = r * (n + 1) + c
            b, d = a + 1, a + n + 1
            faces += [[a, b, d + 1], [a, d + 1, d]] if diagonal == 0 else [[a, b, d], [b, d + 1, d]]
    return np.c_[x, y, height(x, y)], np.asarray(faces)


class DepthFromTwoSurfacesTests(unittest.TestCase):
    def test_planar_surfaces_with_different_triangulations_are_exact(self):
        # depth = 0.02 x − 0.01 y on a 100 m square: fill 17500/3, cut 2500/3 (closed form)
        design = grid_tin(17, lambda x, y: 10 + 0.02 * x)
        existing = grid_tin(23, lambda x, y: 10 + 0.01 * y, jitter=1.0, diagonal=1)
        xyz, faces, stats = depth_tin(design, existing)
        summary = volume_summary(xyz, faces, ())
        self.assertAlmostEqual(summary["fill_volume"], 17500.0 / 3.0, places=6)
        self.assertAlmostEqual(summary["cut_volume"], 2500.0 / 3.0, places=6)
        self.assertAlmostEqual(summary["plan_area"], 10000.0, places=6)
        self.assertAlmostEqual(stats["overlay_area"], stats["design_area"], places=6)

    def test_net_volume_and_swap_on_curved_surfaces(self):
        design = grid_tin(30, lambda x, y: 5 + np.sin(x / 9) + 0.03 * y, jitter=0.6, seed=1)
        existing = grid_tin(41, lambda x, y: 5 + 0.8 * np.cos(y / 7), jitter=0.5, seed=2, diagonal=1)
        xyz, faces, _stats = depth_tin(design, existing)
        summary = volume_summary(xyz, faces, ())

        def prism_volume(surface):
            corners = surface[0][surface[1]]
            u, v = corners[:, 1, :2] - corners[:, 0, :2], corners[:, 2, :2] - corners[:, 0, :2]
            return float((0.5 * np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) * corners[:, :, 2].mean(axis=1)).sum())

        # Over the same square, net = ∫design − ∫existing exactly.
        self.assertAlmostEqual(summary["net_volume"], prism_volume(design) - prism_volume(existing), places=6)
        swapped = volume_summary(*depth_tin(existing, design)[:2], ())
        self.assertAlmostEqual(swapped["cut_volume"], summary["fill_volume"], places=6)
        self.assertAlmostEqual(swapped["fill_volume"], summary["cut_volume"], places=6)

    def test_partial_overlap_and_no_overlap(self):
        design = grid_tin(4, lambda x, y: 0 * x + 1.0)
        existing = (grid_tin(4, lambda x, y: 0 * x)[0] + [50.0, 0.0, 0.0], grid_tin(4, lambda x, y: 0 * x)[1])
        xyz, faces, stats = depth_tin(design, existing)
        self.assertAlmostEqual(stats["overlay_area"], 5000.0, places=6)
        self.assertAlmostEqual(volume_summary(xyz, faces, ())["fill_volume"], 5000.0, places=6)
        far = (existing[0] + [500.0, 0.0, 0.0], existing[1])
        with self.assertRaisesRegex(ValueError, "do not overlap"):
            depth_tin(design, far)

    def test_candidate_pairs_match_brute_force(self):
        rng = np.random.default_rng(3)
        low = rng.uniform(0, 100, (60, 2))
        a = np.c_[low, low + rng.uniform(0.5, 8, (60, 2))]
        low = rng.uniform(0, 100, (80, 2))
        b = np.c_[low, low + rng.uniform(0.5, 30, (80, 2))]
        first, second = candidate_pairs(a, b)
        brute = {(i, j) for i in range(60) for j in range(80)
                 if a[i, 0] <= b[j, 2] and b[j, 0] <= a[i, 2] and a[i, 1] <= b[j, 3] and b[j, 1] <= a[i, 3]}
        self.assertEqual(set(zip(first.tolist(), second.tolist())), brute)

    def test_fixture_design_minus_existing_matches_volume_surface(self):
        design = read_tin(str(FIXTURE), "Pad FG")
        existing = read_tin(str(FIXTURE), "Pad EG")
        summary = volume_summary(*depth_tin(design[:2], existing[:2])[:2], ())
        self.assertAlmostEqual(summary["cut_volume"], 100.0, places=6)
        self.assertAlmostEqual(summary["fill_volume"], 100.0, places=6)
        self.assertAlmostEqual(summary["plan_area"], 200.0, places=6)

if __name__ == "__main__":
    unittest.main()


class LevelLineTests(unittest.TestCase):
    def test_pyramid_gives_one_closed_ring_and_plane_one_open_line(self):
        from landxml_plugin.landxml.plan_features import level_lines

        xyz = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [1, 1, 2]], float)
        faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
        (ring,) = level_lines(xyz, faces, 1.0)
        self.assertTrue(np.allclose(ring[0], ring[-1]))
        self.assertEqual(len(ring), 5)
        self.assertTrue(np.allclose(ring[:, 2], 1.0))
        self.assertTrue(np.allclose(np.abs(ring[:, :2] - 1.0).max(axis=1), 0.5))
        self.assertEqual(level_lines(xyz, faces, 3.0), [])
        plane = np.array([[0, 0, 0], [4, 0, 4], [4, 4, 4], [0, 4, 0]], float)
        (line,) = level_lines(plane, np.array([[0, 1, 2], [0, 2, 3]]), 1.5)
        self.assertTrue(np.allclose(sorted(line[[0, -1], 1]), [0, 4]))
        self.assertTrue(np.allclose(line[:, 0], 1.5))


class FootprintLineTests(unittest.TestCase):
    def test_island_at_grade_becomes_a_hole_and_slivers_are_dropped(self):
        from landxml_plugin.landxml.plan_features import footprint_lines

        n = 7
        xs, ys = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
        xyz = np.column_stack([xs.ravel(), ys.ravel(), 100 + xs.ravel()])
        depth = np.where((np.abs(xs - 3) <= 1) & (np.abs(ys - 3) <= 1), 0.0, 1.0).ravel()
        faces = []
        for r in range(n - 1):
            for c in range(n - 1):
                a, b, d, e = r * n + c, r * n + c + 1, (r + 1) * n + c, (r + 1) * n + c + 1
                faces += [(a, b, e), (a, e, d)]
        lines = footprint_lines(xyz, np.array(faces), depth)
        lengths = sorted(round(float(np.hypot(*np.diff(l[:, :2], axis=0).T).sum()), 6) for l in lines)
        self.assertEqual(lengths, [8.0, 24.0])            # the 2 x 2 untouched island, inside the 6 x 6 edge
        for line in lines:
            self.assertTrue(np.allclose(line[:, 2], 100 + line[:, 0]))
        self.assertEqual(len(footprint_lines(xyz, np.array(faces), depth, min_length=10.0)), 1)
        self.assertEqual(footprint_lines(xyz, np.array(faces), np.zeros(len(xyz))), [])


def _grid_tin(xs, ys, level):
    X, Y = np.meshgrid(np.asarray(xs, float), np.asarray(ys, float))
    xyz = np.column_stack([X.ravel(), Y.ravel(), level(X.ravel(), Y.ravel())])
    n = len(xs)
    faces = []
    for r in range(len(ys) - 1):
        for c in range(n - 1):
            a, b, d, e = r * n + c, r * n + c + 1, (r + 1) * n + c, (r + 1) * n + c + 1
            faces += [(a, b, e), (a, e, d)]
    return xyz, np.array(faces)


class EdgeAnalysisTests(unittest.TestCase):
    def _depth(self, design):
        from landxml_plugin.landxml.tin_overlay import sample_tin

        ground = (np.array([[-100, -100, 100], [200, -100, 100], [200, 200, 100], [-100, 200, 100]], float),
                  np.array([[0, 1, 2], [0, 2, 3]]))
        return design[0], design[1], design[0][:, 2] - sample_tin(ground, design[0])

    def test_batters_tie_in_and_an_open_side_is_a_retained_edge(self):
        from landxml_plugin.landxml.edges import edge_analysis

        def level(x, y):  # platform at 103 over 20..40, 1V:2H batters to the ground west, south and east; cut off at y = 40
            return np.maximum(100.0, 103.0 - 0.5 * np.maximum.reduce([np.zeros_like(x), 20 - x, x - 40, 20 - y]))

        steps = [14, 15, 16, 17, 18, 19, 20, 30, 40, 41, 42, 43, 44, 45, 46]
        design = _grid_tin(steps, [14, 15, 16, 17, 18, 19, 20, 30, 40], level)
        result = edge_analysis(*self._depth(design))
        sched = result["schedule"]
        self.assertAlmostEqual(sched["retained_fill"]["max"], 3.0, places=6)
        self.assertAlmostEqual(sched["retained_fill"]["total"], 32.0, delta=0.5)   # the open north side
        self.assertAlmostEqual(sched["batter_fill"]["max"], 3.0, places=6)
        self.assertAlmostEqual(sched["batter_fill"]["total"], 32.0 + 2 * 26.0, delta=1.0)
        self.assertAlmostEqual(result["perimeter"], 32 * 2 + 26 * 2, delta=1.0)   # an at-grade corner triangle is cut off
        self.assertNotIn("retained_cut", sched)
        self.assertEqual(result["steps"], [])

    def test_wall_between_platforms_is_an_internal_step(self):
        from landxml_plugin.landxml.edges import edge_analysis

        xs = [0, 10, 24.95, 25, 35, 35.05, 50, 60]
        design = _grid_tin(xs, xs, lambda x, y: np.where((x >= 25) & (x <= 35) & (y >= 25) & (y <= 35), 105.0, 103.0))
        result = edge_analysis(*self._depth(design))
        self.assertAlmostEqual(result["schedule"]["retained_fill"]["total"], 240.0, places=6)
        self.assertAlmostEqual(result["schedule"]["retained_fill"]["max"], 3.0, places=6)
        (step,) = result["steps"]
        self.assertEqual(step["type"], "wall")
        self.assertAlmostEqual(step["height"], 2.0, places=6)
        self.assertAlmostEqual(step["length"], 40.1, delta=0.2)
        self.assertAlmostEqual(step["x"], 30.0, delta=0.1)
        with self.assertRaises(ValueError):
            edge_analysis(design[0], design[1], np.zeros(len(design[0])))


class SampleTinTests(unittest.TestCase):
    def test_plane_values_inside_nan_outside(self):
        from landxml_plugin.landxml.tin_overlay import sample_tin

        xyz = np.array([[0, 0, 0], [10, 0, 10], [10, 10, 20], [0, 10, 10]], float)
        tin = (xyz, np.array([[0, 1, 2], [0, 2, 3]]))
        points = np.array([[5, 5], [2, 7], [10, 10], [0, 0], [11, 5], [5, -0.5]], float)
        got = sample_tin(tin, points)
        self.assertTrue(np.allclose(got[:4], points[:4, 0] + points[:4, 1]))
        self.assertTrue(np.isnan(got[4:]).all())


class SurfaceStyleTests(unittest.TestCase):
    def test_presets_custom_breaks_and_data_classes(self):
        from landxml_plugin.landxml import surface_style as ss

        slope = ss.resolve(ss.parse_style("slope"))
        self.assertEqual([c["label"] for c in slope["classes"]][0], "Flat (platforms) < 2 %")
        self.assertEqual(list(ss.classify([0, 2, 29.9, 30, 300], slope)), [0, 1, 1, 2, 3])
        depth = ss.resolve(ss.parse_style("depth"))
        self.assertEqual(len(depth["classes"]), 12)
        self.assertTrue(depth["classes"][0]["label"].startswith("< -5"))
        custom = ss.resolve(ss.parse_style("depth: -1, 0, 1 | #ff0000, #ffaaaa, #aaffaa, #00ff00"))
        self.assertEqual([c["color"] for c in custom["classes"]], ["#ff0000", "#ffaaaa", "#aaffaa", "#00ff00"])
        elevation = ss.resolve(ss.parse_style("elevation: equal 4"), values=[100, 104, 108])
        self.assertEqual([c["high"] for c in elevation["classes"]][:3], [102.0, 104.0, 106.0])
        quantile = ss.resolve(ss.parse_style("elevation: quantile 2 | greys"), values=[1, 2, 3, 10])
        self.assertEqual(len(quantile["classes"]), 2)
        with self.assertRaises(ValueError):
            ss.parse_style("colour: 1, 2")

    def test_triangle_values_and_regions(self):
        from landxml_plugin.landxml import surface_style as ss

        xyz = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 5], [0, 10, 5]], float)   # rises 50 % to the north
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        self.assertTrue(np.allclose(ss.triangle_values(xyz, faces, "slope"), 50.0))
        self.assertTrue(np.allclose(ss.triangle_values(xyz, faces, "aspect"), 180.0))   # falls towards the south
        self.assertTrue(np.allclose(ss.triangle_values(xyz, faces, "elevation"), [5 / 3, 10 / 3]))
        resolved = ss.resolve(ss.parse_style("slope"))
        index = ss.classify(ss.triangle_values(xyz, faces, "slope"), resolved)
        regions = ss.class_regions(xyz, faces, index, len(resolved["classes"]))
        self.assertEqual(list(regions), [2])
        self.assertAlmostEqual(float(ss.class_areas(xyz, faces, index, 4)[2]), 100.0)
        split, split_faces, colors, _ = ss.mesh_colors(xyz, faces, resolved)
        self.assertEqual((len(split), len(split_faces)), (6, 2))         # slope: one colour per triangle
        self.assertTrue((colors == (0xfd, 0xae, 0x61)).all())
        same, same_faces, by_level, _ = ss.mesh_colors(xyz, faces, ss.resolve(ss.parse_style("elevation: 2.5")))
        self.assertIs(same, xyz)
        self.assertEqual(len({tuple(c) for c in by_level}), 2)
        svg = ss.plan_svg([(regions, resolved)], legend=resolved)
        self.assertIn("<svg", svg)
        self.assertIn("Batter 30–250 %", svg)


class BandPieceTests(unittest.TestCase):
    def test_bands_are_exact_on_a_plane(self):
        from landxml_plugin.landxml import surface_style as ss

        xyz = np.array([[0, 0, 0], [10, 0, 10], [10, 10, 10], [0, 10, 0]], float)   # value = x
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        resolved = ss.resolve(ss.parse_style("elevation: 2.5, 5"))
        pieces = ss.band_pieces(xyz, faces, xyz[:, 2], resolved)
        self.assertTrue(np.allclose(ss.piece_areas(pieces, 3), [25.0, 25.0, 50.0]))
        svg = ss.plan_svg([(pieces, resolved)], legend=resolved)
        self.assertIn("stroke-width='0.4'", svg)


class SceneSolidsTests(unittest.TestCase):
    def test_ear_clipping_extrusion_and_water(self):
        from landxml_plugin.landxml.scene import extrude, triangulate_ring, water_mesh

        l_shape = np.array([[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]], float)
        tris = triangulate_ring(l_shape)
        area = sum(0.5 * abs(np.cross(l_shape[b] - l_shape[a], l_shape[c] - l_shape[a])) for a, b, c in tris)
        self.assertEqual(len(tris), 4)
        self.assertAlmostEqual(area, 6.0)
        xyz, faces = extrude(np.vstack([l_shape, l_shape[:1]]), 100.0, 5.0)
        self.assertEqual((len(xyz), len(faces)), (12, 12 + 4))
        self.assertEqual((xyz[:, 2].min(), xyz[:, 2].max()), (100.0, 105.0))
        plane = np.array([[0, 0, 0], [10, 0, 10], [10, 10, 10], [0, 10, 0]], float)     # z = x
        wxyz, wfaces = water_mesh(plane, np.array([[0, 1, 2], [0, 2, 3]]), 4.0)
        wet = sum(0.5 * abs(np.cross(wxyz[b, :2] - wxyz[a, :2], wxyz[c, :2] - wxyz[a, :2])) for a, b, c in wfaces)
        self.assertAlmostEqual(wet, 40.0)
        self.assertTrue(np.allclose(wxyz[:, 2], 4.0))


class SyntheticCorridorTests(unittest.TestCase):
    def test_fill_slopes_run_out_to_flat_ground(self):
        from landxml_plugin.landxml.synthetic_corridor import synthetic_corridor

        ground = (np.array([[-50, -100, 100], [250, -100, 100], [250, 100, 100], [-50, 100, 100]], float),
                  np.array([[0, 1, 2], [0, 2, 3]]))
        xs = np.arange(0, 201, 2.0)
        centre = np.column_stack([xs, np.zeros_like(xs), np.full_like(xs, 103.0)])          # 3 m above the ground
        xyz, faces, station, offset, daylight = synthetic_corridor(centre, xs + 1000, ground, 7.0, 1.5, 0.025, 0.04, 1.5, 2.0)
        edge_z = 103.0 - 0.025 * 3.5 - 0.04 * 1.5
        self.assertTrue(np.allclose(daylight, 2.0 * (edge_z - 100.0), atol=1e-6))           # 1V:2H fill to the ground
        self.assertTrue(np.allclose(xyz[offset == 0, 2], 103.0))
        outer = np.abs(offset) == offset.max()
        self.assertTrue(np.allclose(xyz[outer, 2], 100.0, atol=1e-6))
        self.assertTrue(np.allclose(xyz[offset == 0, 1], 0.0))
        self.assertTrue((xyz[offset > 0, 1] < 0).all())                                     # right of travel (east) is south
        self.assertEqual(len(faces), 2 * 6 * (len(xs) - 1))
        self.assertTrue(np.allclose(station[::7], xs + 1000))
        cut = synthetic_corridor(centre - [0, 0, 6], xs, ground, 7.0, 1.5, 0.025, 0.04, 1.5, 2.0)[4]
        self.assertTrue(np.allclose(cut, 1.5 * (100.0 - (97.0 - 0.025 * 3.5 - 0.04 * 1.5)), atol=1e-6))  # 1V:1.5H cut


class ConceptStructureTests(unittest.TestCase):
    def test_csv_schedule_reads_common_headings(self):
        from landxml_plugin.landxml.structures import read_schedule_csv

        text = ("site,type,chainage,mode,cell_b_m,cell_d_m,cells,invert_in,invert_out\n"
                "R1,relief,9+025.00,conventional,3.0,1.5,2,1846.77,1846.71\n"
                "P1,pipe culvert,9100,,,,2,,\n"
                "X,box,bad,,1,1,1,,\n")
        items, notes = read_schedule_csv(text)
        self.assertEqual([i["id"] for i in items], ["R1", "P1"])
        self.assertEqual((items[0]["kind"], items[0]["station"], items[0]["cells"], items[0]["span"], items[0]["rise"]),
                         ("box", 9025.0, 2, 3.0, 1.5))
        self.assertEqual(items[1]["kind"], "pipe")
        self.assertIn("cell_b_m → span", notes[0])
        self.assertIn("mode", notes[1])
        self.assertTrue(any("Row 4 skipped" in n for n in notes))

    def test_typed_schedule(self):
        from landxml_plugin.landxml.structures import read_schedule_text

        items = read_schedule_text("1+250 = box 3 x 2.0 x 2.0; 1+840 = pipe 2 x 0.9, skew 15\n1+364-2+293 = bridge, span 30")
        self.assertEqual([(i["kind"], i["station"]) for i in items], [("box", 1250.0), ("pipe", 1840.0), ("bridge", 1364.0)])
        self.assertEqual((items[0]["cells"], items[0]["span"], items[0]["rise"]), (3, 2.0, 2.0))
        self.assertEqual((items[1]["cells"], items[1]["span"], items[1]["skew"]), (2, 0.9, 15.0))
        self.assertEqual((items[2]["end"], items[2]["span"]), (2293.0, 30.0))
        with self.assertRaises(ValueError):
            read_schedule_text("1+250 = box 3 x 2.0")

    def test_box_culvert_width_cover_and_bridge_piers(self):
        from landxml_plugin.landxml.structures import MeshBuilder, bridge, culvert, low_points

        flat = lambda points: np.full(len(points), 100.0)
        builder = MeshBuilder()
        item = {"id": "C1", "kind": "box", "cells": 3, "span": 2.0, "rise": 2.0, "skew": 0.0,
                "invert_in": None, "invert_out": None}
        text = culvert(builder, item, np.array([0.0, 0.0, 101.0]), np.array([0.0, -1.0]), 10.0, 10.0, flat)
        wide = np.ptp(builder.result()[0][:, 0])
        single = MeshBuilder()
        culvert(single, dict(item, cells=1), np.array([0.0, 0.0, 101.0]), np.array([0.0, -1.0]), 10.0, 10.0, flat)
        self.assertAlmostEqual(wide - np.ptp(single.result()[0][:, 0]), 2 * (2.0 + 0.25), places=6)   # two more cells and walls
        self.assertIn("does not fit under the road by 2.1 m", text)       # 100 + 2.0 + 2 x 0.25 against 101 - 0.6
        builder = MeshBuilder()
        xs = np.arange(0, 101, 2.0)
        centre = np.column_stack([xs, 0 * xs, np.full_like(xs, 130.0)])
        normal = np.tile([0.0, -1.0], (len(xs), 1))
        text, piers = bridge(builder, centre, xs, normal, np.arange(len(xs)), 11.0, 25.0, flat)
        self.assertEqual(piers, 3)
        self.assertIn("4 spans", text)
        ground = 100 - 5 * np.exp(-((xs - 50) / 8) ** 2)
        self.assertEqual(low_points(xs, np.full_like(xs, 101.0), ground), [50.0])


class ChannelAndIndexTests(unittest.TestCase):
    def test_channel_lowers_the_ground_only_along_the_culvert(self):
        from landxml_plugin.landxml.structures import carve_channels

        xs, ys = np.meshgrid(np.arange(-60, 61, 10.0), np.arange(-60, 61, 10.0))
        xyz = np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, 100.0)])
        n = 13
        faces = np.array([[r * n + c, r * n + c + 1, (r + 1) * n + c + 1] for r in range(n - 1) for c in range(n - 1)]
                         + [[r * n + c, (r + 1) * n + c + 1, (r + 1) * n + c] for r in range(n - 1) for c in range(n - 1)])
        channel = {"left": np.array([0.0, -10.0]), "right": np.array([0.0, 10.0]), "z_left": 97.0, "z_right": 97.0, "width": 4.0}
        cxyz, cfaces, lowered = carve_channels(xyz, faces, [channel])
        self.assertGreater(lowered, 0)
        self.assertGreater(len(cfaces), len(faces))
        self.assertAlmostEqual(cxyz[np.hypot(cxyz[:, 0], cxyz[:, 1]) < 1e-9, 2].min(), 97.0)
        self.assertTrue(np.allclose(cxyz[np.abs(cxyz[:, 0]) > 20, 2], 100.0))          # banks reach the ground within 4.5 m

    def test_tin_index_matches_sample_tin(self):
        from landxml_plugin.landxml.tin_overlay import TinIndex, sample_tin

        xs, ys = np.meshgrid(np.arange(0, 101, 5.0), np.arange(0, 101, 5.0))
        xyz = np.column_stack([xs.ravel(), ys.ravel(), np.sin(xs.ravel() / 9.0) * 3 + ys.ravel() * 0.1])
        n = 21
        faces = np.array([[r * n + c, r * n + c + 1, (r + 1) * n + c + 1] for r in range(n - 1) for c in range(n - 1)]
                         + [[r * n + c, (r + 1) * n + c + 1, (r + 1) * n + c] for r in range(n - 1) for c in range(n - 1)])
        points = np.random.default_rng(3).uniform(-10, 110, (5000, 2))
        expected = sample_tin((xyz, faces), points)
        got = TinIndex(xyz, faces).sample(points)
        self.assertTrue(np.array_equal(np.isnan(expected), np.isnan(got)))
        self.assertTrue(np.allclose(expected[~np.isnan(expected)], got[~np.isnan(got)]))


class FastLookupTests(unittest.TestCase):
    def test_station_offset_matches_a_full_search(self):
        from landxml_plugin.landxml.scene import station_offset

        t = np.linspace(0, 10, 400)
        path = np.column_stack([t * 50, np.sin(t) * 80])
        stations = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(path, axis=0).T))])
        points = np.random.default_rng(5).uniform([-50, -200], [550, 200], (3000, 2))
        station, offset = station_offset(points, path, stations)
        a, b = path[:-1], path[1:]
        d = b - a
        tt = np.clip((((points[:, None] - a) * d).sum(2)) / (d ** 2).sum(1), 0, 1)
        dist = np.linalg.norm(points[:, None] - (a + tt[..., None] * d), axis=2)
        self.assertTrue(np.allclose(np.abs(offset), dist.min(axis=1)))
        near, _o = station_offset(points, path, stations, max_distance=30.0)
        self.assertTrue(np.array_equal(np.isnan(near), dist.min(axis=1) > 30.0))

    def test_descendant_index_matches_a_walk(self):
        from landxml_plugin import safe_xml
        from landxml_plugin.landxml.common import descendants, local_name

        root = safe_xml.fromstring('<a xmlns="urn:x"><b><c/><d><c/></d></b><c><e/></c></a>')
        walk = lambda e, n: [x for x in e.iter() if x is not e and local_name(x.tag) == n]
        for element in root.iter():
            for name in ("b", "c", "d", "e"):
                self.assertEqual(list(descendants(element, name)), walk(element, name))
        root[0].append(safe_xml.Element("{urn:x}c"))          # a change to the tree rebuilds the index
        self.assertEqual(len(list(descendants(root, "c"))), 4)


class BridgeLayoutTests(unittest.TestCase):
    def test_bridge_spans_and_piers_from_the_schedule(self):
        from landxml_plugin.landxml.structures import MeshBuilder, bridge, read_schedule_text

        items = read_schedule_text("1+000-1+100 = bridge, spans 4; 1+000-1+100 = bridge, piers 1+020 1+050 1+090")
        self.assertEqual((items[0]["spans"], items[0]["piers"]), (4.0, None))
        self.assertEqual(items[1]["piers"], [1020.0, 1050.0, 1090.0])
        xs = np.arange(1000, 1101, 2.0)
        centre = np.column_stack([xs, 0 * xs, np.full_like(xs, 130.0)])
        normal = np.tile([0.0, -1.0], (len(xs), 1))
        flat = lambda points: np.full(len(points), 100.0)
        text, piers = bridge(MeshBuilder(), centre, xs, normal, np.arange(len(xs)), 11.0, 25.0, flat, spans=4)
        self.assertEqual((piers, "4 spans" in text), (3, True))
        text, piers = bridge(MeshBuilder(), centre, xs, normal, np.arange(len(xs)), 11.0, 25.0, flat,
                             piers=[1020.0, 1050.0, 1090.0])
        self.assertEqual(piers, 3)
        self.assertIn("longest 40 m", text)


class StructureScheduleTests(unittest.TestCase):
    def test_roads_piers_and_deck_options_in_the_typed_schedule(self):
        from landxml_plugin.landxml.structures import for_road, read_schedule_text

        items = read_schedule_text("Main road @ 1+360.9-2+242.2 = viaduct, depth 2.6, width 12; "
                                   "1+403-2+203 = piers every 40, 5.0 x 3.2, cap 10.5 x 2.5; "
                                   "1+723 = pier 7.5 x 5.5, cap 12.6 x 12.6 x 2.5; "
                                   "A9 @ 0+773.6-0+831.4 = bridge, spans 1, deck 1235 to 1240; 1+250 = box 3 x 2.0 x 2.0")
        viaduct = items[0]
        self.assertEqual((viaduct["kind"], viaduct["road"], viaduct["deck_depth"], viaduct["deck_width"]), ("bridge", "Main road", 2.6, 12.0))
        piers = [i for i in items if i["kind"] == "pier"]
        self.assertEqual(len(piers), 22)
        self.assertEqual([p["station"] for p in piers[:21]], [1403.0 + 40 * k for k in range(21)])
        self.assertEqual((piers[-1]["station"], piers[-1]["pier_width"], piers[-1]["cap"]), (1723.0, 7.5, (12.6, 12.6, 2.5)))
        self.assertEqual(piers[0]["cap"], (10.5, 10.5, 2.5))
        a9 = for_road(items, "A9 highway")
        self.assertEqual([i["kind"] for i in a9 if i["kind"] != "pier"], ["bridge", "box"])
        self.assertEqual(next(i for i in a9 if i["kind"] == "bridge")["deck_levels"], (1235.0, 1240.0))
        self.assertNotIn(viaduct, a9)
        with self.assertRaises(ValueError):
            read_schedule_text("1+400 = piers every 40, 5 x 3")            # equal spacing needs a range

    def test_csv_schedule_reads_road_pier_and_deck_columns(self):
        from landxml_plugin.landxml.structures import read_schedule_csv

        items, notes = read_schedule_csv("Road,Chainage,To,Type,Pier width,Pier length,Pile cap,Deck depth,Deck level\n"
                                         "Main,1+360,2+240,viaduct,,,,2.6,\nMain,1+403,,pier,5.0,3.2,9 x 2.5,,\n"
                                         "A9,0+773.6,0+831.4,bridge,,,,,1235 to 1240\n")
        self.assertIn("Road → road", notes[0])
        self.assertEqual([(i["kind"], i["road"]) for i in items], [("bridge", "Main"), ("pier", "Main"), ("bridge", "A9")])
        self.assertEqual((items[1]["pier_width"], items[1]["pier_length"], items[1]["cap"]), (5.0, 3.2, (9.0, 9.0, 2.5)))
        self.assertEqual((items[0]["deck_depth"], items[2]["deck_levels"]), (2.6, (1235.0, 1240.0)))

    def test_bridge_draws_scheduled_piers_with_their_sections(self):
        from landxml_plugin.landxml.structures import MeshBuilder, bridge

        stations = np.arange(0.0, 101.0, 2.0)
        centre = np.column_stack([stations, np.zeros_like(stations), np.full_like(stations, 50.0)])
        normal = np.tile([0.0, 1.0], (len(stations), 1))
        builder = MeshBuilder()
        specs = [{"station": 30.0, "pier_width": 6.0, "pier_length": 2.0, "cap": (8.0, 8.0, 2.0)},
                 {"station": 70.0, "pier_width": 4.0, "pier_length": 1.5, "cap": None}]
        text, count = bridge(builder, centre, stations, normal, np.arange(len(stations)), 10.0, 25.0,
                             lambda pts: np.zeros(len(pts)), depth=2.0, pier_specs=specs)
        self.assertEqual(count, 2)
        self.assertIn("2 from the pier schedule", text)
        xyz, _faces = builder.result()
        cap = xyz[(np.abs(xyz[:, 0] - 30.0) <= 4.0 + 1e-9) & (xyz[:, 2] < 0.31)]
        self.assertAlmostEqual(float(cap[:, 2].min()), 0.3 - 2.0)          # pile cap 2.0 deep, top 0.3 above ground


class RenderEdgeTests(unittest.TestCase):
    def test_banded_point_in_polygon_matches_the_plain_even_odd_rule(self):
        from landxml_plugin.landxml.grading import _inside_many

        def plain(points, ring):
            x, y = points[:, 0], points[:, 1]
            inside = np.zeros(len(points), bool)
            for (x0, y0), (x1, y1) in zip(ring, np.roll(ring, -1, axis=0)):
                if y0 != y1:
                    inside ^= ((y0 > y) != (y1 > y)) & (x < (x1 - x0) * (y - y0) / (y1 - y0) + x0)
            return inside

        rng = np.random.default_rng(3)
        for trial in range(20):
            angles = np.sort(rng.uniform(0, 2 * np.pi, rng.integers(3, 40)))
            radii = rng.uniform(2, 10, len(angles))
            ring = np.c_[radii * np.cos(angles), radii * np.sin(angles)]
            points = rng.uniform(-11, 11, (2000, 2))
            if trial % 2:                       # shared levels: vertices and points on band edges
                ring, points = np.round(ring), np.round(points)
            np.testing.assert_array_equal(_inside_many(points, ring), plain(points, ring))

    def test_wall_top_merges_the_steps_down_a_wall_face(self):
        from landxml_plugin.landxml.edges import wall_top

        comb = np.array([[0, 0, 10], [5, 0, 10], [5.01, 0, 7], [5.02, 0, 10], [10, 0, 10], [10.01, 0, 7], [15, 0, 10]])
        top = wall_top(comb)
        self.assertEqual(len(top), 4)
        self.assertTrue((top[:, 2] == 10).all())

    def test_refine_pretest_keeps_every_triangle_near_the_footprint(self):
        from landxml_plugin.landxml.structures import _near_cells

        footprint = (np.array([[100.0, 100, 0], [104, 100, 0], [100, 104, 0]]), np.array([[0, 1, 2]]))
        tri = np.array([[[99.0, 99], [109, 99], [99, 109]],          # overlaps it
                        [[500.0, 500], [510, 500], [500, 510]]])    # far away
        longest = np.array([np.hypot(10, 10)] * 2)
        np.testing.assert_array_equal(_near_cells(tri, longest, footprint), [True, False])

    def test_viewer_splits_normals_at_creases_and_layers_shared_edges(self):
        html = (Path(__file__).resolve().parents[1] / "landxml" / "viewer.html").read_text(encoding="utf-8")
        for needle in ("function creaseNormals", "linesByWidth", 'isGround(s)) ? 4 : 1',
                       "float contour(", "vec3 tint(", 'id="groundmode"', 'id="contourint"', "GROUND_ROLES"):
            self.assertIn(needle, html)


class ViewerTemplateTests(unittest.TestCase):
    def test_viewer_has_the_phone_layout_touch_gestures_and_a3_sheet(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "landxml" / "viewer.html").read_text(encoding="utf-8")
        for needle in ("@media (max-width: 760px)", "touch-action: none", "pinchState()", "function composeA3",
                       "@page { size: A3 landscape", 'id="a3png"', 'id="a3print"', "function drawCallouts"):
            self.assertIn(needle, html)


class CustomaryUnitRegressionTests(unittest.TestCase):
    def test_structure_station_and_explicit_csv_dimensions(self):
        from landxml_plugin.landxml.structures import (
            read_schedule_csv,
            read_schedule_text,
        )

        for unit in ("foot", "USSurveyFoot"):
            item = read_schedule_text("12+50 = pipe 1 x 3", horizontal_unit=unit)[0]
            self.assertEqual(item["station"], 1250)
            rows, _ = read_schedule_csv(
                "station,type,span_ft,rise_ft\n12+50,box,6,4\n", horizontal_unit=unit
            )
            self.assertAlmostEqual(
                rows[0]["span"],
                6 * 0.3048 / (1200 / 3937 if unit == "USSurveyFoot" else 0.3048),
            )
            self.assertIsNotNone(rows[0]["rise"])
        rows, _ = read_schedule_csv(
            "station,type,diameter_in\n1250,pipe,36\n", horizontal_unit="foot"
        )
        self.assertAlmostEqual(rows[0]["span"], 3)
        rows, _ = read_schedule_csv(
            "station,type,diameter\n1250,pipe,36\n", horizontal_unit="foot"
        )
        self.assertEqual(rows[0]["span"], 36)
        rows, _ = read_schedule_csv(
            "station_m,type,DiameterMM,invert_in,invert_out\n381,pipe,900,95,94\n",
            horizontal_unit="foot",
        )
        self.assertAlmostEqual(rows[0]["station"], 1250)
        self.assertAlmostEqual(rows[0]["span"], 2.952755905511811)
        self.assertEqual((rows[0]["invert_in"], rows[0]["invert_out"]), (95, 94))

    def test_schedule_conversions_reject_nonfinite_results(self):
        from landxml_plugin.landxml.structures import read_schedule_csv
        for station in ("nan", "inf", "1e308"):
            rows, _ = read_schedule_csv(f"station_yd,type,span,rise\n{station},box,6,4\n", horizontal_unit="millimeter")
            self.assertEqual(rows, [])
        with self.assertRaisesRegex(ValueError, "numeric range"):
            read_schedule_csv("station,type,span_m,rise\n1,box,1e308,1\n", horizontal_unit="millimeter")

    def test_mixed_unit_camber_and_edge_face_area(self):
        from landxml_plugin.landxml.synthetic_corridor import synthetic_corridor
        from landxml_plugin.landxml.edges import edge_analysis

        faces = np.array([[0, 1, 2], [0, 2, 3]])
        ground = (
            np.array(
                [[-100, -100, 0], [200, -100, 0], [200, 100, 0], [-100, 100, 0]], float
            ),
            faces,
        )
        xyz, *_ = synthetic_corridor(
            [[0, 0, 10], [100, 0, 10]],
            [0, 100],
            ground,
            carriageway=20,
            shoulder=0,
            camber=0.02,
            vertical_factor=1 / 0.3048,
        )
        self.assertAlmostEqual(10 - xyz[2, 2], 0.06096)
        xyz = np.array([[0, 0, 2], [10, 0, 2], [10, 10, 2], [0, 10, 2]], float)
        result = edge_analysis(xyz, faces, np.full(4, 2.0), vertical_factor=1 / 0.3048)
        self.assertAlmostEqual(
            result["schedule"]["retained_fill"]["face_over_1"], 262.4671916010499
        )

    def test_concept_structures_keep_physical_sizes_in_feet(self):
        from landxml_plugin.landxml.structures import MeshBuilder, culvert, bridge

        for h, v, name in (
            (0.3048, 0.3048, "foot"),
            (1200 / 3937, 1.0, "USSurveyFoot"),
        ):
            scale = np.array([h, h, v])
            metric, customary = MeshBuilder(), MeshBuilder()
            point = np.array([0.0, 0.0, 10.0])
            item = dict(id="Box", kind="box", span=2.0, rise=1.0, cells=2, skew=0.0)
            culvert(metric, item, point, [0, 1], 10, 10, lambda xy: np.zeros(len(xy)))
            culvert(
                customary,
                dict(item, span=2 / h, rise=1 / v),
                point / scale,
                [0, 1],
                10 / h,
                10 / h,
                lambda xy: np.zeros(len(xy)),
                horizontal_unit=name,
                vertical_unit=name if v != 1 else "meter",
            )
            np.testing.assert_allclose(
                customary.result()[0] * scale, metric.result()[0], atol=1e-10
            )
            metric, customary = MeshBuilder(), MeshBuilder()
            centre = np.array([[0, 0, 10], [50, 0, 10], [100, 0, 10]], float)
            bridge(
                metric,
                centre,
                np.array([0, 50, 100]),
                np.tile([0, 1], (3, 1)),
                [0, 1, 2],
                10,
                25,
                lambda xy: np.zeros(len(xy)),
            )
            bridge(
                customary,
                centre / scale,
                np.array([0, 50, 100]) / h,
                np.tile([0, 1], (3, 1)),
                [0, 1, 2],
                10 / h,
                25 / h,
                lambda xy: np.zeros(len(xy)),
                horizontal_unit=name,
                vertical_unit=name if v != 1 else "meter",
                station_unit=name,
            )
            np.testing.assert_allclose(
                customary.result()[0] * scale, metric.result()[0], atol=1e-10
            )
