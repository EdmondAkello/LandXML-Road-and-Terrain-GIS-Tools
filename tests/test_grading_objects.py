"""Grading Editor engine: pads and lines with a level at every vertex, previews, builds (pure numpy/GDAL)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml import design_shapes as ds
from landxml_plugin.landxml import grading_objects as go


def dem(path, x0=0.0, y0=200.0, n=200, cell=1.0, level=lambda x, y: 100.0 + 0.0 * x, epsg=32737):
    from osgeo import gdal, osr

    xs = x0 + (np.arange(n) + 0.5) * cell
    ys = y0 - (np.arange(n) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    out = gdal.GetDriverByName("GTiff").Create(path, n, n, 1, gdal.GDT_Float32)
    out.SetGeoTransform((x0, cell, 0, y0, 0, -cell))
    ref = osr.SpatialReference()
    ref.ImportFromEPSG(epsg)
    out.SetProjection(ref.ExportToWkt())
    out.GetRasterBand(1).WriteArray(level(X, Y))
    out = None
    return path


class PadTests(unittest.TestCase):
    def test_triangulation_of_concave_outline(self):
        l_shape = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]
        tris = ds.triangulate_polygon(l_shape)
        self.assertEqual(len(tris), 4)
        v = np.array(l_shape, float)
        def cross(a, b):
            return a[0] * b[1] - a[1] * b[0]

        area = sum(abs(cross(v[j] - v[i], v[k] - v[i])) / 2 for i, j, k in tris)
        self.assertAlmostEqual(area, 300.0)

    def test_sloped_pad_levels_and_batters(self):
        grid = ds.Grid(np.full((120, 120), 100.0), 0, 120, 1.0)
        pad = [(30, 30, 101.0), (90, 30, 101.0), (90, 90, 104.0), (30, 90, 104.0)]     # 5 % up to the north
        design = ds.graded_pad(grid, pad, cut_slope=2, fill_slope=2)
        z = design.sample(np.array([[60.0, 60.5], [60.0, 30.5]]))
        self.assertAlmostEqual(z[0], 101.0 + 3.0 * 30 / 60, delta=0.03)
        self.assertAlmostEqual(z[1], 101.0 + 3.0 * 0.5 / 60, delta=0.03)
        # fill batter 1:2 south of the low edge: 1 m fill daylights 2 m out
        s = design.sample(np.array([[60.0, 29.0], [60.0, 27.5]]))
        self.assertAlmostEqual(s[0], 100.5, delta=0.05)
        self.assertAlmostEqual(s[1], 100.0, delta=0.01)

    def test_benched_cut(self):
        d = ds.batter_rise(np.array([0, 2, 4, 9, 13]), slope=1.0, bench_height=4.0, bench_width=5.0)
        self.assertEqual(list(d), [0, 2, 4, 4, 8])

    def test_line_modes(self):
        grid = ds.Grid(np.full((60, 120), 100.0), 0, 60, 1.0)
        swale = ds.graded_line(grid, [(10, 30, 99.0), (110, 30, 99.0)], width=2, cut_slope=2, mode="cut")
        self.assertAlmostEqual(float(swale.sample(np.array([[60.0, 30.5]]))[0]), 99.0, places=6)
        self.assertGreaterEqual(float(np.nanmin(swale.z - grid.z)), -1.0 - 1e-9)
        self.assertLessEqual(float(np.nanmax(swale.z - grid.z)), 1e-9)                 # cut only
        bund = ds.graded_line(grid, [(10, 30, 99.0), (110, 30, 99.0)], width=2, mode="fill")
        np.testing.assert_allclose(bund.z, grid.z)                                       # a fill-only line under ground


class BuildTests(unittest.TestCase):
    def test_build_writes_landxml_with_vertex_levels_and_a_report(self):
        from landxml_plugin.core import read_tin
        from landxml_plugin.landxml.features import read_line_features
        from landxml_plugin.landxml.parser import load_document

        with tempfile.TemporaryDirectory() as directory:
            ground = dem(os.path.join(directory, "eg.tif"), level=lambda x, y: 100.0 + 0.02 * x)
            pad = go.new_object("pad", "Yard", [[60, 60, 101.5], [140, 60, 102.0], [140, 120, 102.5], [60, 120, 102.0]])
            ramp = go.new_object("line", "Access ramp", [[20, 90, 100.4], [60, 90, 101.75]], width=6.0)
            spec = {"type": "dem", "path": ground}
            preview = go.preview([pad, ramp], spec)
            self.assertIsNotNone(preview)
            _g, _d, rows, _cell = preview
            self.assertEqual([r["name"] for r in rows], ["Yard", "Access ramp"])
            out = os.path.join(directory, "grading.xml")
            result = go.build([pad, ramp], spec, out, epsg=32737, ground_name="EG", cell=1.0,
                              report_path=os.path.join(directory, "q.html"), json_path=os.path.join(directory, "g.json"),
                              dem_path=os.path.join(directory, "design.tif"), dxf_path=os.path.join(directory, "g.dxf"))
            self.assertTrue(os.path.getsize(os.path.join(directory, "g.dxf")) > 10000)
            self.assertGreater(result["fill"], 0)
            feats, _w = read_line_features(load_document(out).root)
            yard = next(f for f in feats if f.name == "Yard outline")
            self.assertEqual([round(p[2], 3) for p in yard.points], [101.5, 102.0, 102.5, 102.0, 101.5])
            self.assertTrue(any(f.name.startswith("Daylight") for f in feats))
            xyz, faces, _m = read_tin(out, "Design")
            self.assertGreater(len(faces), 1000)
            objects, ground_spec, epsg = go.load(os.path.join(directory, "g.json"))
            self.assertEqual(objects[1]["width"], 6.0)
            self.assertEqual(epsg, 32737)
            with open(os.path.join(directory, "q.html"), encoding="utf-8") as handle:
                self.assertIn("Access ramp", handle.read())

    def test_shared_adaptive_mesh_matches_where_unchanged(self):
        """Ground and design on one adaptive mesh: few triangles on pads, both within the tolerance, equal outside."""
        grid = ds.Grid(np.fromfunction(lambda r, c: 100 + 0.05 * c + 0.3 * np.sin(r / 9.0), (160, 160)), 0, 160, 1.0)
        design = ds.graded_pad(grid, [(40, 40, 102.0), (120, 40, 103.0), (120, 120, 103.0), (40, 120, 102.0)], 2, 2)
        (g_pts, g_faces), (d_pts, d_faces) = go._tins(grid, design, 0.02)
        self.assertIs(g_faces, d_faces)
        self.assertLess(len(d_faces), 2 * 159 * 159 * 0.6)                          # fewer than the full grid
        same = np.abs(g_pts[:, 2] - d_pts[:, 2]) < 1e-9
        self.assertGreater(same.mean(), 0.2)                                       # untouched ground shared exactly
        from landxml_plugin.landxml.tin_overlay import TinIndex
        probe = grid.xy[::37]
        for pts, surface in ((g_pts, grid), (d_pts, design)):
            z = TinIndex(pts, d_faces).sample(probe)
            ok = np.isfinite(z)
            self.assertLess(np.abs(z[ok] - surface.z.ravel()[::37][ok]).max(), 0.0201)

    def test_level_tools(self):
        pad = go.new_object("pad", "P", [[0, 0, 100], [10, 0, 100], [10, 10, 100]])
        go.set_levels(pad, "grade", 2.0, index=0)
        self.assertEqual([round(v[2], 3) for v in pad["vertices"]], [100.0, 100.2, 100.4])
        go.set_levels(pad, "raise", -0.4)
        self.assertAlmostEqual(pad["vertices"][0][2], 99.6)
        go.set_levels(pad, "ground", 0.5, ground=lambda xy: np.full(len(xy), 98.0))
        self.assertEqual({v[2] for v in pad["vertices"]}, {98.5})


if __name__ == "__main__":
    unittest.main()
