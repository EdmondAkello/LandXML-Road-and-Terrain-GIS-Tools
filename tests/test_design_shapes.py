"""Land-development design shapes on a ground grid (no QGIS)."""

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

from landxml_plugin.landxml.design_shapes import (
    Grid, basin, bunker, distance_to_ring, pit, platform, raised, signed_distance, smooth_area, stockpile, terraces, volumes,
)

SQUARE = np.array([[40.0, -60.0], [60.0, -60.0], [60.0, -40.0], [40.0, -40.0]])     # 20 x 20 in the middle


def flat(level=100.0, size=100, cell=1.0):
    return Grid(np.full((size, size), level), 0.0, 0.0, cell)


def sloping(size=100, cell=1.0, grade=0.05):
    grid = flat(size=size, cell=cell)
    grid.z = 100.0 + grade * grid.xy[:, 0].reshape(size, size)
    return grid


class GridTests(unittest.TestCase):
    def test_bilinear_sampling_and_a_grid_tin(self):
        g = sloping()
        self.assertAlmostEqual(g.sample(np.array([[30.0, -20.0]]))[0], 101.5)
        self.assertTrue(np.isnan(g.sample(np.array([[-5.0, -5.0]]))[0]))
        self.assertAlmostEqual(g.sample(np.array([[0.2, -0.2]]))[0], g.z[0, 0])      # the edge half-cell
        points, faces = Grid(np.arange(12.0).reshape(3, 4), 0, 0, 2).tin()
        self.assertEqual((len(points), len(faces)), (12, 12))
        fine = g.resampled(0.5, (20, -40, 40, -20))
        self.assertEqual(fine.z.shape, (40, 40))
        self.assertAlmostEqual(fine.sample(np.array([[30.0, -30.0]]))[0], 101.5, places=6)

    def test_distances(self):
        self.assertAlmostEqual(distance_to_ring(np.array([[50.0, -30.0]]), SQUARE)[0], 10.0)
        sd = signed_distance(flat(), SQUARE)
        self.assertLess(sd[50, 50], 0)                       # inside
        self.assertGreater(sd[5, 5], 0)


class ShapeTests(unittest.TestCase):
    def test_a_platform_daylights_on_sloping_ground(self):
        g = sloping()
        d = platform(g, SQUARE, 102.5, cut_slope=1.0, fill_slope=2.0)
        self.assertTrue(np.allclose(d.z[45:55, 45:55], 102.5))
        self.assertAlmostEqual(d.z[50, 0], g.z[50, 0])        # far away: untouched
        row = d.z[50]
        self.assertAlmostEqual(row[62], min(g.z[50, 62], 102.5 + 2.5 / 1.0))   # the 1:1 cut face meets the ground
        self.assertAlmostEqual(row[37], max(g.z[50, 37], 102.5 - 2.5 / 2.0))   # the 2:1 fill face on the low side
        cut, fill = volumes(g, d)
        self.assertGreater(cut, 0)
        self.assertGreater(fill, 0)

    def test_a_basin_is_a_frustum(self):
        g = flat()
        d = basin(g, SQUARE, 98.0, side_slope=2.0)
        cut, fill = volumes(g, d)
        top = 20 + 2 * 2 * 2                                   # 2 deep, 2:1 sides
        exact = 2 / 3 * (20 * 20 + top * top + 20 * top)
        self.assertAlmostEqual(cut, exact, delta=exact * 0.06)
        self.assertEqual(fill, 0.0)

    def test_a_stockpile_reaches_its_volume(self):
        g = flat()
        d = stockpile(g, SQUARE, volume=800.0, slope=1.5)
        cut, fill = volumes(g, d)
        self.assertAlmostEqual(fill, 800.0, delta=8.0)
        self.assertEqual(cut, 0.0)
        peak = stockpile(g, SQUARE, top=200.0, slope=1.5)       # too high for the footprint: a peak
        self.assertAlmostEqual(peak.z.max(), 100 + 10 / 1.5, delta=0.5)
        with self.assertRaises(ValueError):
            stockpile(g, SQUARE, volume=1e9)

    def test_pit_benches_are_level_and_stop_at_the_floor(self):
        ring = np.array([[10.0, -90.0], [90.0, -90.0], [90.0, -10.0], [10.0, -10.0]])
        d = pit(flat(), ring, floor=70.0, bench_height=10.0, berm=8.0, face_slope=0.5)
        levels = np.unique(np.round(d.z[50, 10:51], 6))
        for bench in (90.0, 80.0, 70.0):
            self.assertIn(bench, levels)                        # level berms 10 apart
        self.assertEqual(d.z.min(), 70.0)

    def test_terraces_give_flat_benches_on_a_slope(self):
        g = sloping(grade=0.1)
        big = np.array([[5.0, -95.0], [95.0, -95.0], [95.0, -5.0], [5.0, -5.0]])
        d = terraces(g, big, step=2.0, riser=0.3, smoothing=0)
        row = np.round(d.z[50, 30:70], 6)
        self.assertGreater(np.mean(np.diff(row) == 0), 0.5)    # most of the row is level bench
        self.assertAlmostEqual(d.z[50, 0], g.z[50, 0])

    def test_golf_features(self):
        g = sloping(grade=0.02)
        bumpy = g.like(g.z + 0.5 * np.sin(g.xy[:, 0] / 3).reshape(g.z.shape))
        eased = smooth_area(bumpy, SQUARE, window=10, blend=2)
        self.assertLess(np.std(np.diff(eased.z[50, 45:55])), np.std(np.diff(bumpy.z[50, 45:55])))
        green = raised(g, SQUARE, height=0.6)
        self.assertAlmostEqual(green.z[50, 50], float(np.mean(g.sample(SQUARE))) + 0.6, places=6)
        hole = bunker(g, SQUARE, depth=1.0)
        self.assertAlmostEqual(hole.z.min(), g.sample(SQUARE).min() - 1.0, places=6)
        self.assertTrue((hole.z <= g.z + 1e-9).all())


if __name__ == "__main__":
    unittest.main()
