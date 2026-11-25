"""The profile editor's model: editing, checks and writing into a copy of the file (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml.catalog import read_catalog
from landxml_plugin.landxml.common import descendants
from landxml_plugin.landxml.criteria import load_criteria
from landxml_plugin.landxml.geometry import read_alignments
from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.profile import read_profile_controls
from landxml_plugin.landxml.profile_design import ProfileDesign, splice_profile
from landxml_plugin.landxml.writer import HorizontalAlignment, Line, VerticalProfile, landxml_text


def design():
    return ProfileDesign([(0, 100.0, 0), (200, 104.0, 100), (400, 100.0, 0)])


class EditingTests(unittest.TestCase):
    def test_grades_curves_and_k(self):
        d = design()
        self.assertAlmostEqual(d.grade(0), 0.02)
        curve = d.curves()[0]
        self.assertEqual(curve["kind"], "crest")
        self.assertAlmostEqual(curve["change"], -4.0)
        self.assertAlmostEqual(curve["k"], 25.0)
        self.assertAlmostEqual(d.level(200), 103.5)

    def test_moves_keep_the_profile_valid_and_the_ends_in_place(self):
        d = design()
        self.assertAlmostEqual(d.move(1, 380, 105), 400 - 50 - 1)          # stopped where its curve still fits
        d.move(0, 999, 101)                                                    # an end moves only up or down
        self.assertEqual(d.pvis[0][:2], [0.0, 101.0])
        self.assertEqual(d.pvis[1][1], 105.0)

    def test_add_delete_curves_and_undo(self):
        d = design()
        i = d.add(300)                                                         # on the current line
        self.assertEqual(i, 2)
        self.assertAlmostEqual(d.pvis[2][1], 102.0)
        self.assertLessEqual(d.pvis[1][2], 2 * (300 - 200) + 1e-9)
        d.set_k(1, 10)
        self.assertAlmostEqual(d.curves()[0]["k"], 10.0, places=2)
        with self.assertRaises(ValueError):
            d.set_curve(1, 1000)                                               # does not fit
        with self.assertRaises(ValueError):
            d.delete(0)
        d.delete(2)
        self.assertEqual(len(d.pvis), 3)
        self.assertTrue(d.undo())
        self.assertEqual(len(d.pvis), 4)
        self.assertTrue(d.redo())
        self.assertEqual(len(d.pvis), 3)
        while d.undo():
            pass
        self.assertEqual(d.pvis, design().pvis)
        self.assertFalse(d.can_undo)

    def test_a_first_design_from_the_ground(self):
        ground = [(s, 100 + 0.01 * s + (1.5 if 140 < s < 160 else 0.0)) for s in range(0, 401, 5)]
        d = ProfileDesign.from_ground(ground, 0, 400, spacing=100)
        self.assertEqual([p[0] for p in d.pvis], [0, 100, 200, 300, 400])
        self.assertAlmostEqual(d.pvis[0][1], 100.0)
        self.assertAlmostEqual(d.pvis[-1][1], 104.0)
        self.assertLess(abs(d.pvis[2][1] - 102.0), 0.4)                       # the hump is averaged out
        self.assertEqual(len(ProfileDesign.from_ground(ground, 0, 400).pvis), 2)

    def test_a_drag_is_one_undo_step(self):
        d = design()
        mark = d.mark()
        for level in (104.5, 105.0, 105.5):
            d.move(1, level=level)
        d.squash(mark)
        self.assertEqual(d.pvis[1][1], 105.5)
        d.undo()
        self.assertEqual(d.pvis[1][1], 104.0)
        self.assertFalse(d.can_undo)

    def test_nearest_pvi(self):
        d = design()
        self.assertEqual(d.nearest(190), 1)
        self.assertIsNone(d.nearest(120, tolerance=20))


class CheckTests(unittest.TestCase):
    def test_k_grades_and_missing_curves_against_a_standard(self):
        criteria = load_criteria("kenya_rdm13_2025")
        d = ProfileDesign([(0, 100.0, 0), (200, 112.0, 60), (400, 104.0, 0), (600, 104.2, 0)])
        text = "\n".join(message for _s, _at, message in d.checks(criteria, speed=80, max_grade=5))
        self.assertIn("steeper than 5 %", text)                                # 6 % up
        self.assertIn("Crest K", text)                                         # 60 / 10 = K 6 < 45 at 80 km/h
        self.assertIn("has no vertical curve", text)                           # the sag at 400
        self.assertIn("flatter than 0.5 %", text)                              # 0.1 % at the end
        stations = [at for _s, at, _m in d.checks(criteria, speed=80, max_grade=5)]
        self.assertEqual(stations, sorted(stations))
        self.assertEqual(design().checks(), [])


class SpliceTests(unittest.TestCase):
    def source(self):
        road = HorizontalAlignment("Main & side", (0.0, 0.0), 90.0, [Line(400)])
        return landxml_text([(road, [VerticalProfile("Old", [(0, 99, 0), (400, 98, 0)])])], epsg=32737)

    def test_a_new_profile_is_added_and_the_rest_kept(self):
        text = splice_profile(self.source(), "Main & side", design().profile())
        path = Path(tempfile.mkdtemp()) / "out.xml"
        path.write_text(text, encoding="utf-8")
        self.assertEqual(read_catalog(str(path)).design_profiles, {"Main & side": ["Old", "Design FRL"]})
        self.assertEqual(read_alignments(str(path), 5.0)[0][0]["vertex_distances"][-1], 400)
        self.assertIn('epsgCode="32737"', text)
        prof = [p for p in descendants(load_document(str(path)).root, "ProfAlign") if p.attrib["name"] == "Design FRL"][0]
        self.assertEqual([c["station"] for c in read_profile_controls(prof)], [0, 200, 400])

    def test_the_same_name_is_replaced_and_a_missing_alignment_refused(self):
        once = splice_profile(self.source(), "Main & side", design().profile())
        twice = splice_profile(once, "Main & side", ProfileDesign([(0, 1.0, 0), (400, 2.0, 0)]).profile())
        self.assertEqual(twice.count('name="Design FRL"'), 1)
        self.assertIn("<PVI>400 2</PVI>", twice)
        with self.assertRaises(ValueError):
            splice_profile(self.source(), "Nope", design().profile())

    def test_a_profile_element_is_made_when_there_is_none(self):
        bare = landxml_text([(HorizontalAlignment("A", (0.0, 0.0), 0.0, [Line(100)]), [])])
        text = splice_profile(bare, "A", ProfileDesign([(0, 1.0, 0), (100, 2.0, 0)]).profile())
        self.assertIn('<Profile name="A"><ProfAlign name="Design FRL">', text)


if __name__ == "__main__":
    unittest.main()
