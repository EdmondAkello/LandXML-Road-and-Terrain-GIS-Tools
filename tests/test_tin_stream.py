"""Streaming TIN reader: parity with the element-by-element reader, errors, crop, light parse."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
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

from landxml_plugin import core
from landxml_plugin.landxml import tin_stream
from landxml_plugin.landxml.common import descendants
from landxml_plugin.landxml.parser import load_document

FIXTURES = ROOT / "tests" / "fixtures"
NS = "http://www.landxml.org/schema/LandXML-1.2"


def tin_xml(points, faces, prefix="", extra_root="", point_attr='id="{id}"', face_open="<F>", newline="\n",
            surfaces=1, name="EG"):
    """A LandXML text with ``surfaces`` copies of one TIN. ``points`` is [(id, x, y, z)]."""
    p = f"{prefix}:" if prefix else ""
    declare = f' xmlns:{prefix}="{NS}"' if prefix else f' xmlns="{NS}"'
    body = []
    for s in range(surfaces):
        pts = newline.join(
            f"<{p}P {point_attr.format(id=i)}>{x!r} {y!r} {z!r}</{p}P>" for i, x, y, z in points)
        fcs = newline.join(f"{face_open.replace('<F', '<' + p + 'F')}{a} {b} {c}</{p}F>" for a, b, c in faces)
        body.append(
            f'<{p}Surface name="{name}{s or ""}"><{p}Definition surfType="TIN"><{p}Pnts>{newline}{pts}{newline}</{p}Pnts>'
            f'<{p}Faces>{newline}{fcs}{newline}</{p}Faces></{p}Definition></{p}Surface>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>{newline}<{p}LandXML{declare} version="1.2">{extra_root}'
            f'<{p}Units><{p}Metric linearUnit="meter" verticalLinearUnit="meter"/></{p}Units>'
            f'<{p}Surfaces>{"".join(body)}</{p}Surfaces></{p}LandXML>')


def grid(n, first_id=1, step=7):
    """Points on an n x n grid with ids first_id, first_id+step, ... and two triangles per cell."""
    pts = [(first_id + step * (r * n + c), 1000.0 + 5 * c, 2000.0 + 5 * r, 10 + 0.1 * r + 0.01 * c)
           for r in range(n) for c in range(n)]
    ids = [p[0] for p in pts]
    faces = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, d, e = ids[r * n + c], ids[r * n + c + 1], ids[(r + 1) * n + c], ids[(r + 1) * n + c + 1]
            faces += [(a, b, d), (b, e, d)]
    return pts, faces


class StreamBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text, name="t.xml", binary=None):
        path = Path(self.tmp.name) / name
        if binary is not None:
            path.write_bytes(binary)
        else:
            path.write_text(text, encoding="utf-8", newline="")
        return str(path)

    def reference(self, path, surface=None, include_invisible=False):
        document = load_document(path, full=True)
        node, name = core._select_surface(document.root, surface, None)
        xyz, faces, hidden = core._read_tin_nodes(node, name, include_invisible)
        return xyz, faces, hidden

    def assertParity(self, path, surface=None, include_invisible=False):
        xyz, faces, meta = core.read_tin(path, surface, include_invisible=include_invisible)
        rxyz, rfaces, hidden = self.reference(path, surface, include_invisible)
        np.testing.assert_array_equal(xyz, rxyz)
        np.testing.assert_array_equal(faces, rfaces)
        self.assertEqual(faces.dtype, np.int32)
        self.assertEqual(meta["invisible_face_count"], hidden)
        return xyz, faces, meta


class TestParity(StreamBase):
    def test_sequential_ids(self):
        pts, faces = grid(12, step=1)
        self.assertParity(self.write(tin_xml(pts, faces)))

    def test_gapped_unsorted_ids(self):
        pts, faces = grid(10, first_id=500, step=13)
        pts = pts[::-1]
        self.assertParity(self.write(tin_xml(pts, faces)))

    def test_namespace_prefix_and_crlf(self):
        pts, faces = grid(8)
        self.assertParity(self.write(tin_xml(pts, faces, prefix="lx", newline="\r\n")))

    def test_extra_point_attributes(self):
        pts, faces = grid(6)
        self.assertParity(self.write(tin_xml(pts, faces, point_attr='id="{id}" name="p{id}"')))

    def test_hidden_faces_skipped_and_counted(self):
        pts, faces = grid(6)
        text = tin_xml(pts, faces)
        first = f"<F>{faces[0][0]} {faces[0][1]} {faces[0][2]}</F>"
        text = text.replace(first, first.replace("<F>", '<F i="1">'), 1)
        path = self.write(text)
        _xyz, got, meta = self.assertParity(path)
        self.assertEqual(meta["invisible_face_count"], 1)
        self.assertEqual(len(got), len(faces) - 1)
        self.assertParity(path, include_invisible=True)

    def test_face_attributes_other_than_hidden(self):
        pts, faces = grid(6)
        self.assertParity(self.write(tin_xml(pts, faces, face_open='<F n="1 2 3">')))

    def test_tiny_blocks_never_split_elements(self):
        pts, faces = grid(15)
        path = self.write(tin_xml(pts, faces))
        document = load_document(path)
        surface = document.surfaces()[0]
        (ps, fs) = document.tin_spans(surface)
        ids, xyz = tin_stream.read_points(path, ps, block_bytes=97)
        rxyz, rfaces, _ = self.reference(path)
        np.testing.assert_array_equal(xyz, rxyz)
        got, hidden, count = tin_stream.read_faces(path, fs, ids, block_bytes=61)
        np.testing.assert_array_equal(got, rfaces)
        self.assertEqual(count, len(faces))

    def test_several_surfaces_selected_by_name(self):
        pts, faces = grid(5)
        path = self.write(tin_xml(pts, faces, surfaces=3))
        for name in ("EG", "EG1", "EG2"):
            self.assertParity(path, name)
        xyz, *_ = core.read_tin(path, surface_index=2)
        self.assertEqual(len(xyz), 25)

    def test_utf8_bom(self):
        pts, faces = grid(5)
        self.assertParity(self.write("", binary=b"\xef\xbb\xbf" + tin_xml(pts, faces).encode()))

    def test_repository_fixtures(self):
        checked = 0
        for path in sorted(FIXTURES.rglob("*.xml")):
            document = load_document(str(path), full=True)
            for index, surface in enumerate(document.surfaces()):
                try:
                    expected = core._read_tin_nodes(surface, surface.attrib.get("name"), False)
                except ValueError:
                    continue
                xyz, faces, meta = core.read_tin(str(path), surface_index=index)
                np.testing.assert_array_equal(xyz, expected[0])
                np.testing.assert_array_equal(faces, expected[1])
                checked += 1
        self.assertGreater(checked, 5)


class TestErrors(StreamBase):
    def message(self, text, **kwargs):
        with self.assertRaises(ValueError) as caught:
            core.read_tin(self.write(text), **kwargs)
        return str(caught.exception)

    def reference_message(self, text):
        path = self.write(text, "ref.xml")
        with self.assertRaises(ValueError) as caught:
            self.reference(path)
        return str(caught.exception)

    def check(self, text):
        self.assertEqual(self.message(text), self.reference_message(text))

    def test_missing_point_id(self):
        pts, faces = grid(4)
        faces[3] = (faces[3][0], faces[3][1], 99999)
        self.check(tin_xml(pts, faces))

    def test_duplicate_point_id(self):
        pts, faces = grid(4)
        pts[5] = (pts[2][0],) + pts[5][1:]
        self.check(tin_xml(pts, faces))

    def test_bad_point(self):
        pts, faces = grid(4)
        text = tin_xml(pts, faces).replace(f"{pts[3][1]!r} {pts[3][2]!r} {pts[3][3]!r}", "1 two 3", 1)
        self.check(text)

    def test_face_with_four_references(self):
        pts, faces = grid(4)
        first = faces[0]
        text = tin_xml(pts, faces).replace(f"{first[0]} {first[1]} {first[2]}</F>",
                                           f"{first[0]} {first[1]} {first[2]} {first[0]}</F>", 1)
        self.check(text)

    def test_noninteger_reference(self):
        pts, faces = grid(4)
        first = faces[0]
        text = tin_xml(pts, faces).replace(f"{first[0]} {first[1]} {first[2]}</F>",
                                           f"{first[0]} {first[1]} 2.5</F>", 1)
        self.check(text)

    def test_no_visible_faces(self):
        pts, faces = grid(2)
        text = tin_xml(pts, faces, face_open='<F i="1">')
        self.assertEqual(self.message(text), "No visible triangular faces were found.")

    def test_doctype_is_refused_before_any_tin_is_read(self):
        pts, faces = grid(3)
        text = tin_xml(pts, faces).replace("<LandXML", '<!DOCTYPE LandXML [<!ENTITY x "y">]><LandXML', 1)
        with self.assertRaises(ValueError):
            load_document(self.write(text))


class TestCropAndDocument(StreamBase):
    def test_crop_keeps_touching_triangles_and_renumbers(self):
        pts, faces = grid(10)
        path = self.write(tin_xml(pts, faces))
        box = (1000.0, 2000.0, 1012.0, 2012.0)          # lower-left 3 x 3 vertices
        xyz, got, meta = core.read_tin(path, bounds=box)
        self.assertTrue(meta["cropped"])
        self.assertLess(len(got), len(faces))
        self.assertEqual(got.max(), len(xyz) - 1)
        full_xyz, full_faces, _ = core.read_tin(path)
        expected_xyz, expected_faces = core.crop_tin(full_xyz, full_faces, box)
        np.testing.assert_array_equal(xyz, expected_xyz)
        np.testing.assert_array_equal(got, expected_faces)
        # every kept triangle's bounding box meets the box, and every dropped one misses it
        keep = tin_stream._touches(full_xyz, full_faces, box)
        self.assertEqual(int(keep.sum()), len(got))
        self.assertTrue(tin_stream._touches(xyz, got, box).all())
        with self.assertRaises(ValueError):
            core.read_tin(path, bounds=(0, 0, 1, 1))

    def test_crop_keeps_a_triangle_that_spans_the_area(self):
        # one huge triangle covers the box although none of its vertices lie inside it
        pts = [(1, 0.0, 0.0, 1.0), (2, 1000.0, 0.0, 2.0), (3, 0.0, 1000.0, 3.0)]
        path = self.write(tin_xml(pts, [(1, 2, 3)]))
        xyz, faces, _ = core.read_tin(path, bounds=(100.0, 100.0, 120.0, 120.0))
        self.assertEqual(len(faces), 1)
        self.assertEqual(len(xyz), 3)

    def test_light_document_has_no_tin_nodes_but_full_has(self):
        pts, faces = grid(6)
        path = self.write(tin_xml(pts, faces))
        light, full = load_document(path), load_document(path, full=True)
        self.assertTrue(light.skeleton)
        self.assertFalse(full.skeleton)
        self.assertEqual(len(list(descendants(light.root, "P"))), 0)
        self.assertEqual(len(list(descendants(full.root, "P"))), len(pts))

    def test_inspect_counts_and_bounds_match_full_parse(self):
        pts, faces = grid(7)
        path = self.write(tin_xml(pts, faces, surfaces=2))
        light, full = load_document(path).inspect(), load_document(path, full=True).inspect()
        self.assertEqual(light["surfaces"], full["surfaces"])
        self.assertEqual(light["bounds"], full["bounds"])
        self.assertEqual(light["elevation_range"], full["elevation_range"])
        self.assertEqual(light["surfaces"][0]["points"], len(pts))
        self.assertEqual(light["surfaces"][0]["triangles"], len(faces))

    def test_source_contours_are_left_on_disk(self):
        pts, faces = grid(5)
        contours = "<SourceData><Contours>" + "".join(
            f'<Contour elev="{k}"><PntList2D>' + " ".join(f"{k + i} {i}" for i in range(500)) + "</PntList2D></Contour>"
            for k in range(20)) + "</Contours></SourceData>"
        text = tin_xml(pts, faces).replace("<Definition", contours + "<Definition", 1)
        path = self.write(text)
        light, full = load_document(path), load_document(path, full=True)
        self.assertEqual(len(list(descendants(light.root, "PntList2D"))), 0)
        self.assertEqual(len(list(descendants(full.root, "PntList2D"))), 20)
        self.assertEqual(light.inspect()["surfaces"][0]["points"], len(pts))
        self.assertParity(path)

    def test_self_closing_and_empty_blocks(self):
        text = (f'<LandXML xmlns="{NS}" version="1.2"><Units><Metric linearUnit="meter" verticalLinearUnit="meter"/>'
                '</Units><Surfaces><Surface name="Empty"><Definition surfType="TIN"><Pnts/><Faces></Faces>'
                '</Definition></Surface></Surfaces></LandXML>')
        with self.assertRaises(ValueError):
            core.read_tin(self.write(text))

    def test_pnts_text_in_attribute_does_not_confuse_the_scan(self):
        pts, faces = grid(4)
        text = tin_xml(pts, faces).replace('<Surface name="EG">', '<Surface name="EG" desc="Pnts and Faces">')
        self.assertParity(self.write(text))

    def test_progress_and_cancel(self):
        pts, faces = grid(20)
        path = self.write(tin_xml(pts, faces))
        seen = []
        core.read_tin(path, progress=lambda pct, msg: seen.append(pct))
        self.assertEqual(seen[-1], 12)
        self.assertTrue(all(a <= b for a, b in zip(seen, seen[1:])))
        with self.assertRaises(RuntimeError):
            core.read_tin(path, cancel=lambda: True)

    def test_utf16_file_falls_back_to_full_parse(self):
        pts, faces = grid(4)
        text = tin_xml(pts, faces).replace('encoding="UTF-8"', 'encoding="UTF-16"')
        path = self.write("", binary=text.encode("utf-16"))
        document = load_document(path)
        self.assertFalse(document.skeleton)
        self.assertParity(path)


if __name__ == "__main__":
    unittest.main()
