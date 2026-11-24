"""Thinning a big scene for the phone copy of the viewer (no QGIS)."""

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

from landxml_plugin.landxml.scene_viewer import _b64, decimate, unpack


def grid(n):
    """An n × n grid of vertices (a slope in x, a ripple in y) and its 2 (n-1)² triangles."""
    x, y = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    xyz = np.column_stack([x.ravel(), y.ravel(), 0.5 * x.ravel() + np.sin(y.ravel() / 5.0)])
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            faces += [(a, a + 1, a + n), (a + 1, a + n + 1, a + n)]
    rgb = np.tile(np.array([10, 20, 30], np.uint8), (len(xyz), 1))
    return xyz, np.asarray(faces, np.int64), rgb, np.linspace(0, 1, len(xyz))


class DecimateTests(unittest.TestCase):
    def test_a_mesh_within_the_target_is_untouched(self):
        xyz, faces, rgb, depth = grid(10)
        out = decimate(xyz, faces, rgb, depth, len(faces))
        self.assertIs(out[0], xyz)
        np.testing.assert_array_equal(out[1], faces)

    def test_a_big_mesh_is_thinned_to_about_the_target_and_stays_in_place(self):
        xyz, faces, rgb, depth = grid(100)                      # 19,602 triangles
        new_xyz, new_faces, new_rgb, new_depth = decimate(xyz, faces, rgb, depth, 2000)
        self.assertLessEqual(len(new_faces), 2000)
        self.assertGreater(len(new_faces), 800)
        self.assertEqual(len(new_xyz), len(new_rgb))
        self.assertEqual(len(new_xyz), len(new_depth))
        self.assertTrue(new_faces.max() < len(new_xyz))
        self.assertLess(float(np.abs(new_xyz[:, :2].min(axis=0) - 0).max()), 8.0)       # still covers the same area
        self.assertGreater(float(new_xyz[:, 0].max()), 90.0)
        np.testing.assert_array_equal(new_rgb[0], [10, 20, 30])                          # colours are kept
        self.assertTrue(((new_depth >= 0) & (new_depth <= 1)).all())

    def test_no_triangle_collapses_or_repeats(self):
        xyz, faces, rgb, depth = grid(60)
        _x, new_faces, _c, _d = decimate(xyz, faces, rgb, depth, 500)
        self.assertTrue((new_faces[:, 0] != new_faces[:, 1]).all() and (new_faces[:, 1] != new_faces[:, 2]).all()
                        and (new_faces[:, 0] != new_faces[:, 2]).all())
        self.assertEqual(len(np.unique(np.sort(new_faces, axis=1), axis=0)), len(new_faces))

    def test_a_mesh_without_depth_stays_without(self):
        xyz, faces, rgb, _depth = grid(40)
        self.assertIsNone(decimate(xyz, faces, rgb, None, 300)[3])


class PackTests(unittest.TestCase):
    def test_packing_is_lossless_and_much_smaller(self):
        xyz, faces, rgb, _depth = grid(120)
        xyz = (xyz + [351000.0, 9968000.0, 1200.0]).astype(np.float32)
        for array, dtype, delta in ((xyz, "<f4", False), (faces.ravel(), "<u4", True), (rgb, "u1", False)):
            packed = _b64(array, dtype, delta)
            np.testing.assert_array_equal(unpack(packed), np.asarray(array, dtype))
        raw = xyz.nbytes + faces.astype(np.uint32).nbytes
        packed = len(_b64(xyz, "<f4")["z"]) + len(_b64(faces.ravel(), "<u4", True)["z"])
        self.assertLess(packed, 0.6 * raw * 4 / 3)                 # well under the plain base64 it replaces


if __name__ == "__main__":
    unittest.main()
