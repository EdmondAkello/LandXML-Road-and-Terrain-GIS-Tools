"""DEM to TIN (no QGIS)."""

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

from landxml_plugin.landxml.dem_tin import adaptive_tin, grid_tin
from landxml_plugin.landxml.tin_overlay import sample_tin


def terrain(rows=97, cols=130, seed=1):
    r, c = np.mgrid[0:rows, 0:cols].astype(float)
    rng = np.random.default_rng(seed)
    return 100 + 0.05 * c + 6 * np.exp(-((r - 40) ** 2 + (c - 70) ** 2) / 300) + 0.02 * rng.standard_normal((rows, cols))


def as_xyz(nodes, z):
    return np.column_stack([nodes[:, 1], -nodes[:, 0], z[nodes[:, 0], nodes[:, 1]]]).astype(float)


def max_miss(nodes, faces, z):
    xyz = as_xyz(nodes, z)
    r, c = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    points = np.column_stack([c.ravel(), -r.ravel()]).astype(float)
    got = sample_tin((xyz, faces), points)
    ok = np.isfinite(got) & np.isfinite(z.ravel())
    return float(np.abs(got[ok] - z.ravel()[ok]).max()), int(ok.sum())


def edge_use(faces):
    edges = {}
    for a, b, c in faces:
        for e in ((a, b), (b, c), (c, a)):
            key = tuple(sorted(e))
            edges[key] = edges.get(key, 0) + 1
    return edges


class AdaptiveTests(unittest.TestCase):
    def test_within_the_tolerance_with_far_fewer_triangles(self):
        z = terrain()
        nodes, faces = adaptive_tin(z, 0.25)
        miss, covered = max_miss(nodes, faces, z)
        self.assertLessEqual(miss, 0.25 + 1e-9)
        self.assertEqual(covered, z.size)                          # the whole grid is covered
        self.assertLess(len(faces), 0.5 * 2 * (z.shape[0] - 1) * (z.shape[1] - 1))
        tighter = adaptive_tin(z, 0.05)[1]
        self.assertGreater(len(tighter), len(faces))

    def test_crack_free_and_anticlockwise(self):
        z = terrain(65, 65)
        nodes, faces = adaptive_tin(z, 0.1)
        xyz = as_xyz(nodes, z)
        a, b, c = xyz[faces[:, 0], :2], xyz[faces[:, 1], :2], xyz[faces[:, 2], :2]
        cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        self.assertTrue((cross > 0).all())
        area = 0.5 * cross.sum()
        self.assertAlmostEqual(area, 64.0 * 64.0)                  # covers the square exactly once: no gaps, no overlaps
        uses = edge_use(faces)
        self.assertTrue(all(count in (1, 2) for count in uses.values()))

    def test_flat_ground_is_two_triangles_and_holes_are_left_out(self):
        nodes, faces = adaptive_tin(np.full((33, 33), 10.0), 0.01)
        self.assertEqual(len(faces), 2)
        z = terrain(33, 33)
        z[10:15, 10:15] = np.nan
        nodes, faces = adaptive_tin(z, 0.1)
        self.assertTrue(np.isfinite(z[nodes[:, 0], nodes[:, 1]]).all())   # no vertex without data
        self.assertLess(max_miss(nodes, faces, z)[0], 0.1 + 1e-9)

    def test_a_triangle_budget(self):
        z = terrain()
        _nodes, faces = adaptive_tin(z, 0.0, max_triangles=2000)
        self.assertLessEqual(len(faces), 2000)                     # the budget is a hard limit
        self.assertGreater(len(faces), 1000)

    def test_the_regular_grid(self):
        z = terrain(11, 21)
        nodes, faces = grid_tin(z, step=2)
        self.assertEqual(len(faces), 2 * 5 * 10)
        self.assertEqual(len(nodes), 6 * 11)                        # every second node, ends included


if __name__ == "__main__":
    unittest.main()
