"""Water ribbons for 3D scenes (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml.scene import ground_drive_path
from landxml_plugin.landxml.water import densify, downstream_levels, orient_downhill, ribbon, strip


def area(xyz, faces):
    a, b, c = (xyz[faces[:, k], :2] for k in range(3))
    return float(np.abs(0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))).sum())


class RibbonTests(unittest.TestCase):
    def test_a_straight_line_gives_a_strip_of_the_width(self):
        xyz, faces = ribbon([[0, 0], [100, 0]], [5, 5], 6.0)
        self.assertEqual(len(faces), 2)
        self.assertAlmostEqual(area(xyz, faces), 600.0)
        self.assertAlmostEqual(float(xyz[:, 1].max() - xyz[:, 1].min()), 6.0)
        self.assertTrue((xyz[:, 2] == 5).all())

    def test_a_right_angle_bend_keeps_the_width_and_stays_joined(self):
        xyz, faces = ribbon(densify([[0, 0], [50, 0], [50, 50]], 10.0), np.full(11, 3.0), 4.0)
        a = area(xyz, faces)
        self.assertAlmostEqual(a, 4.0 * 100.0, delta=0.5)        # the strip's area is width × length, with no gap or overlap at the corner
        # no two faces are flipped: every triangle has the same winding
        p, q, r = (xyz[faces[:, k], :2] for k in range(3))
        cross = (q[:, 0] - p[:, 0]) * (r[:, 1] - p[:, 1]) - (q[:, 1] - p[:, 1]) * (r[:, 0] - p[:, 0])
        self.assertTrue((cross > 0).all() or (cross < 0).all())

    def test_a_sharp_bend_does_not_spike(self):
        xyz, _faces = ribbon([[0, 0], [50, 0], [0, 2]], [1, 1, 1], 4.0)
        self.assertLess(float(np.abs(xyz[:, :2]).max()), 120.0)

    def test_repeated_points_and_short_lines_are_handled(self):
        self.assertEqual(len(ribbon([[0, 0]], [1], 3.0)[1]), 0)
        self.assertEqual(len(ribbon([[0, 0], [0, 0]], [1, 1], 3.0)[1]), 0)
        xyz, faces = ribbon([[0, 0], [0, 0], [10, 0]], [1, 1, 1], 2.0)
        self.assertEqual(len(faces), 2)
        with self.assertRaises(ValueError):
            ribbon([[0, 0], [1, 0]], [1, 1], 0.0)

    def test_water_never_flows_uphill(self):
        np.testing.assert_array_equal(downstream_levels([10, 9, 11, 8, 9.5, 7]), [10, 9, 9, 8, 8, 7])

    def test_a_line_drawn_uphill_is_turned_round(self):
        pts = np.array([[0.0, 0], [10, 0], [20, 0]])
        out, z, turned = orient_downhill(pts, [5, 8, 10])
        self.assertTrue(turned)
        np.testing.assert_array_equal(out[0], [20, 0])
        np.testing.assert_array_equal(z, [10, 8, 5])
        out, z, turned = orient_downhill(pts, [10, 8, 5])
        self.assertFalse(turned)
        self.assertFalse(orient_downhill(pts, [np.nan, 3, np.nan])[2])         # nothing to compare: left as digitised
        self.assertTrue(orient_downhill(pts, [np.nan, 3, 9])[2])               # the ends without ground are ignored

    def test_a_ground_drive_path_is_smoothed_but_keeps_its_plan(self):
        x = np.arange(0, 401, 2.0)
        bumpy = np.column_stack([x, 0 * x, 100 + 0.01 * x + 0.8 * np.sin(x / 3.0)])
        points, stations = ground_drive_path(bumpy, 40.0)
        np.testing.assert_allclose(points[:, :2], bumpy[:, :2])
        self.assertAlmostEqual(float(stations[-1]), 400.0)
        middle = slice(50, -50)
        raw = float(np.abs(np.diff(bumpy[middle, 2])).max())
        self.assertLess(float(np.abs(np.diff(points[middle, 2])).max()), raw / 4)    # most of the bumps are gone
        self.assertLess(float(np.abs(points[middle, 2] - (100 + 0.01 * x[middle])).max()), 0.3)   # the grade is kept

    def test_a_road_strip_has_columns_at_the_offsets_and_stations_along(self):
        xy, station, offset, faces = strip([[0, 0], [50, 0], [50, 50]], [-5.0, 0.0, 5.0])
        self.assertEqual(len(xy), 9)
        self.assertEqual(len(faces), 8)
        np.testing.assert_allclose(station[[0, 3, 6]], [0, 50, 100])
        np.testing.assert_allclose(xy[0], [0, -5])                     # right of a line heading east
        np.testing.assert_allclose(xy[2], [0, 5])
        np.testing.assert_allclose(xy[5], [45, 5], atol=1e-9)         # the inside of the bend is mitred
        self.assertTrue(faces.max() < len(xy))
        self.assertEqual(len(strip([[1, 1]], [0, 1])[3]), 0)

    def test_densify_keeps_vertices_and_limits_the_gaps(self):
        pts = densify([[0, 0], [25, 0], [25, 10]], 10.0)
        self.assertTrue(any((pts == [25, 0]).all(axis=1)))
        self.assertLessEqual(float(np.hypot(*np.diff(pts, axis=0).T).max()), 10.0 + 1e-9)
        self.assertEqual(len(pts), 1 + 3 + 1)


if __name__ == "__main__":
    unittest.main()
