"""Tree scattering for 3D scenes (no QGIS)."""

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

from landxml_plugin.landxml.vegetation import (
    GRASS,
    SHRUB,
    clear_of,
    limit_for,
    plant_kinds,
    plant_sizes,
    polygon_area,
    scatter,
    thin,
)

SQUARE = np.array([[0.0, 0], [100, 0], [100, 100], [0, 100]])
HOLE = np.array([[40.0, 40], [60, 40], [60, 60], [40, 60]])


class ScatterTests(unittest.TestCase):
    def test_one_tree_per_cell_inside_the_polygon(self):
        points = scatter([SQUARE], 10.0, seed=1)
        self.assertEqual(len(points), 100)                       # 10 × 10 cells, every jittered tree inside the square
        self.assertTrue(((points >= 0) & (points <= 100)).all())
        cells = {(int(x // 10), int(y // 10)) for x, y in points}
        self.assertEqual(len(cells), 100)                        # one in each cell: even cover, no clumps

    def test_the_same_inputs_give_the_same_trees(self):
        np.testing.assert_array_equal(scatter([SQUARE], 7.0, seed=3), scatter([SQUARE], 7.0, seed=3))
        self.assertFalse(np.array_equal(scatter([SQUARE], 7.0, seed=3), scatter([SQUARE], 7.0, seed=4)))

    def test_holes_stay_empty_and_the_area_is_the_polygon_minus_its_holes(self):
        points = scatter([SQUARE, HOLE], 2.0, seed=2)
        self.assertFalse(((points[:, 0] > 40) & (points[:, 0] < 60) & (points[:, 1] > 40) & (points[:, 1] < 60)).any())
        self.assertAlmostEqual(polygon_area([SQUARE, HOLE]), 10000.0 - 400.0)
        self.assertAlmostEqual(len(points) / polygon_area([SQUARE, HOLE]), 0.25, delta=0.03)   # about 1 per 4 square units

    def test_a_triangle_only_gets_trees_inside_it(self):
        triangle = np.array([[0.0, 0], [100, 0], [0, 100]])
        points = scatter([triangle], 5.0, seed=5)
        self.assertTrue((points[:, 0] + points[:, 1] <= 100 + 1e-9).all())
        self.assertAlmostEqual(len(points), polygon_area([triangle]) / 25.0, delta=25)

    def test_a_window_limits_the_grid_to_the_scene(self):
        points = scatter([np.array([[0.0, 0], [10000, 0], [10000, 10000], [0, 10000]])], 10.0, seed=1, box=(0, 0, 100, 100))
        self.assertEqual(len(points), 100)                       # the scene's share of a huge polygon, not the whole of it
        self.assertEqual(len(scatter([SQUARE], 10.0, box=(500, 500, 600, 600))), 0)

    def test_a_huge_polygon_at_a_tiny_spacing_is_coarsened_not_exhausted(self):
        huge = np.array([[0.0, 0], [1e6, 0], [1e6, 1e6], [0, 1e6]])
        points = scatter([huge], 0.1, seed=1, box=(0, 0, 1e5, 1e5))
        self.assertLess(len(points), 9_000_000)
        self.assertGreater(len(points), 1000)

    def test_a_non_positive_spacing_is_refused(self):
        with self.assertRaises(ValueError):
            scatter([SQUARE], 0.0)


class ClearAndSizeTests(unittest.TestCase):
    ROAD = (np.array([[0.0, 45, 10], [100, 45, 10], [100, 55, 10], [0, 55, 10]]), np.array([[0, 1, 2], [0, 2, 3]]))

    def test_trees_keep_clear_of_a_design(self):
        points = np.array([[50.0, 50], [50, 56], [50, 59], [50, 62], [50, 80], [50, 20]])
        keep = clear_of(points, self.ROAD, 5.0)
        np.testing.assert_array_equal(keep, [False, False, False, True, True, True])   # on it, 1 m and 4 m off: out; 7 m off: in

    def test_no_clear_distance_removes_only_what_stands_on_the_design(self):
        points = np.array([[50.0, 50], [50, 56]])
        np.testing.assert_array_equal(clear_of(points, self.ROAD, 0.0), [False, True])

    def test_no_designs_means_everything_is_clear(self):
        self.assertTrue(clear_of(np.array([[1.0, 1], [2, 2]]), None, 5.0).all())

    def test_styles_give_grass_shrubs_or_a_stable_mix(self):
        self.assertTrue((plant_kinds(50, 1) == GRASS).all())
        self.assertTrue((plant_kinds(50, 2) == SHRUB).all())
        mix = plant_kinds(4000, 0, seed=3)
        self.assertAlmostEqual(float((mix == SHRUB).mean()), 0.15, delta=0.03)
        np.testing.assert_array_equal(mix, plant_kinds(4000, 0, seed=3))

    def test_auto_sizes_are_in_metres_and_follow_the_output_unit(self):
        kinds = np.array([GRASS] * 500 + [SHRUB] * 500)
        heights, widths = plant_sizes(kinds, 4.0, 0.0, 1.0, seed=1)
        self.assertAlmostEqual(float(heights[:500].mean()), 0.8, delta=0.05)
        self.assertAlmostEqual(float(heights[500:].mean()), 1.8, delta=0.1)
        self.assertTrue((widths[:500] >= 0.8 * 3.6 - 1e-9).all() and (widths[:500] <= 1.2 * 3.6 + 1e-9).all())  # clumps about the spacing
        feet, _w = plant_sizes(kinds, 4.0, 0.0, 0.3048, seed=1)
        np.testing.assert_allclose(feet, heights / 0.3048)
        chosen, _w = plant_sizes(kinds, 4.0, 3.0, 1.0, seed=1)
        self.assertAlmostEqual(float(chosen[500:].mean()), 3.0, delta=0.15)                # a given height sets the shrubs

    def test_the_budget_pays_for_more_grass_than_shrubs(self):
        self.assertGreater(limit_for(400_000, 1), limit_for(400_000, 0))
        self.assertGreater(limit_for(400_000, 0), limit_for(400_000, 2))
        self.assertEqual(limit_for(400_000, 1), 400_000 // 4 // 8)

    def test_thinning_keeps_a_random_but_stable_share_in_order(self):
        keep = thin(1000, 100, seed=1)
        self.assertEqual(len(keep), 100)
        self.assertTrue((np.diff(keep) > 0).all())
        np.testing.assert_array_equal(keep, thin(1000, 100, seed=1))
        np.testing.assert_array_equal(thin(50, 100), np.arange(50))


if __name__ == "__main__":
    unittest.main()
