"""Synthetic split-export preparation and downstream Processing regressions."""

# ruff: noqa: E402 - bootstrap QGIS and the plugin through the runtime test module

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import test_qgis_processing as runtime

import numpy as np
from qgis.core import QgsProcessingContext, QgsProcessingException, QgsProcessingFeedback

from landxml_plugin.landxml.common import descendants, local_name
from landxml_plugin.landxml.corridor import read_corridor_sections
from landxml_plugin.landxml.earthworks import corridor_quantities
from landxml_plugin.landxml.longsection import default_pair, read_alignment_profiles
from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.tin_sampling import TinSampler, regular_stations


def surface(name, rectangles, level=100, hidden=False):
    points, faces = [], []
    for xmin, ymin, xmax, ymax in rectangles:
        first = len(points) + 1
        points.extend((x, y, level + 0.1 * x + 0.2 * y)
                      for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)))
        faces.extend(((first, first + 1, first + 2), (first, first + 2, first + 3)))
    point_xml = "".join(f'<P id="{i + 1}">{x} {y} {z}</P>' for i, (x, y, z) in enumerate(points))
    hidden_attribute = ' i="1"' if hidden else ''
    face_xml = "".join(f'<F{hidden_attribute}>{a} {b} {c}</F>' for a, b, c in faces)
    return f'<Surface name="{name}"><Definition surfType="TIN"><Pnts>{point_xml}</Pnts><Faces>{face_xml}</Faces></Definition></Surface>'


def alignment(name="Road", start=100.5):
    return f'''<Alignment name="{name}" staStart="{start}" length="40">
      <CoordGeom><Line length="40"><Start>0 0</Start><End>40 0</End></Line></CoordGeom>
      <Profile name="Design"><ProfAlign name="Designed PVIs"><PVI>{start} 102</PVI><PVI>{start + 40} 106</PVI></ProfAlign></Profile>
      <Feature name="Original">before<Property label="keep" value="yes"/>after</Feature>
    </Alignment>'''


def document(geometry="", surfaces="", unit="meter", vertical="meter", epsg="32637", datum="TestDatum"):
    kind = "Metric" if unit in ("meter", "metre") else "Imperial"
    vertical_attr = f' verticalLinearUnit="{vertical}"' if vertical else ""
    return f'''<?xml version="1.0"?><LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
      <!-- preserved comment --><?sample keep?>
      <Units><{kind} linearUnit="{unit}"{vertical_attr}/></Units>
      <CoordinateSystem epsgCode="{epsg}" verticalDatum="{datum}"/>
      <Surfaces>{surfaces}</Surfaces><Alignments>{geometry}</Alignments>
    </LandXML>'''


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.QgisProcessingTests.setUpClass()
        cls.algorithms = runtime.QgisProcessingTests.algorithms

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.primary = self.write("geometry.xml", document(alignment()))
        self.existing = self.write("existing.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)])))
        self.proposed = self.write("proposed.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)], 102)))
        self.context = QgsProcessingContext()
        self.feedback = QgsProcessingFeedback()

    def write(self, name, text):
        path = self.folder / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def params(self, **overrides):
        result = {"INPUT": self.primary, "EXISTING_FILE": self.existing, "PROPOSED_FILE": self.proposed,
                  "OUTPUT": str(self.folder / "prepared.xml")}
        result.update(overrides)
        return result

    def run_prepare(self, **overrides):
        return self.algorithms["prepare_landxml"].processAlgorithm(self.params(**overrides), self.context, self.feedback)

    def test_split_exports_round_trip_and_repeat(self):
        original = Path(self.primary).read_bytes()
        result = self.run_prepare()
        self.assertEqual((result["GENERATED_PROFILES"], result["GENERATED_SECTIONS"]), (2, 8))
        self.assertEqual(result["COVERAGE_GAPS"], 0)
        doc = load_document(result["OUTPUT"])
        design, ground = default_pair(read_alignment_profiles(doc.alignments()[0]))
        self.assertEqual(design.name, "Designed PVIs")
        self.assertAlmostEqual(ground.elevation_at(120.5), 102)
        sections, warnings, names, shapes = read_corridor_sections(doc.root)
        self.assertFalse(warnings)
        self.assertFalse(shapes)
        self.assertEqual([s.station for s in sections], [100.5, 120, 140, 140.5])
        points = sections[0].surface("Prepared Existing Ground").points
        self.assertEqual((points[0], points[-1]), ((-50, 110), (50, 90)))
        for offset, elevation in points:
            self.assertAlmostEqual(elevation, 100 - offset * 0.2)
        self.assertEqual(len(names), 2)
        text = Path(result["OUTPUT"]).read_text()
        self.assertIn("preserved comment", text)
        self.assertIn("<?sample keep?>", text)
        self.assertIn('value="yes" />after', text)
        self.assertEqual(Path(self.primary).read_bytes(), original)
        second = self.run_prepare(INPUT=result["OUTPUT"], OUTPUT=str(self.folder / "again.xml"))
        self.assertEqual((second["GENERATED_PROFILES"], second["GENERATED_SECTIONS"]), (0, 0))
        self.assertEqual(Path(second["OUTPUT"]).read_bytes(), Path(result["OUTPUT"]).read_bytes())

    def test_complete_input_needs_no_terrain_and_preserves_data(self):
        result = self.run_prepare()
        prepared = Path(result["OUTPUT"]).read_bytes()
        second = self.run_prepare(INPUT=result["OUTPUT"], EXISTING_FILE="", PROPOSED_FILE="", OUTPUT=str(self.folder / "complete.xml"))
        self.assertEqual(second["GENERATED_PROFILES"], 0)
        self.assertEqual(second["GENERATED_SECTIONS"], 0)
        self.assertEqual(second["PRESERVED_PROFILES"], 3)
        self.assertEqual(second["PRESERVED_SECTIONS"], 8)
        self.assertEqual(Path(second["OUTPUT"]).read_bytes(), prepared)

    def test_original_civil3d_complete_file_passes_through(self):
        result = self.run_prepare(INPUT=runtime.CORRIDOR, EXISTING_FILE="", PROPOSED_FILE="", VERTICAL_UNIT=1, CONFIRM_MATCHING=True)
        self.assertEqual((result["GENERATED_PROFILES"], result["GENERATED_SECTIONS"]), (0, 0))
        before = load_document(runtime.CORRIDOR)
        after = load_document(result["OUTPUT"])
        for tag in ("ProfAlign", "ProfSurf", "CrossSect", "DesignCrossSectSurf", "P"):
            old = [(node.attrib, node.text) for node in descendants(before.root, tag)]
            new = [(node.attrib, node.text) for node in descendants(after.root, tag)]
            self.assertEqual(old, new)

    def test_mixed_input_preserves_original_records(self):
        geometry = alignment().replace('</Alignment>', '''
            <Profile name="Original ground"><ProfSurf name="Existing Ground" state="existing"><PntList2D>100.5 77 140.5 77</PntList2D></ProfSurf></Profile>
            <CrossSects><CrossSect sta="120"><CrossSectSurf name="Terrain"><PntList2D>-50 77 50 77</PntList2D></CrossSectSurf></CrossSect></CrossSects>
            </Alignment>''')
        self.write("geometry.xml", document(geometry))
        result = self.run_prepare(PROPOSED_FILE="")
        self.assertEqual(result["GENERATED_PROFILES"], 0)
        sections, *_ = read_corridor_sections(load_document(result["OUTPUT"]).root)
        original = next(s for s in sections if s.station == 120).surface("Terrain")
        self.assertEqual(original.points, [(-50, 77), (50, 77)])
        self.assertIn("100.5 77 140.5 77", Path(result["OUTPUT"]).read_text())

    def test_holes_split_profiles_and_omit_disconnected_sections(self):
        self.write("geometry.xml", document(alignment(start=100)))
        self.write("existing.xml", document(surfaces=surface("Terrain", [(0, -60, 19.9, 60), (20.1, -60, 40, 60)])))
        result = self.run_prepare(PROPOSED_FILE="")
        profiles = read_alignment_profiles(load_document(result["OUTPUT"]).alignments()[0])
        ground = next(p for p in profiles if p.kind == "surface")
        self.assertEqual(len(ground.parts), 2)
        self.assertIsNone(ground.elevation_at(120))
        self.assertGreater(result["COVERAGE_GAPS"], 0)
        second = self.run_prepare(INPUT=result["OUTPUT"], PROPOSED_FILE="", OUTPUT=str(self.folder / "repeat-gaps.xml"))
        self.assertEqual(second["GENERATED_PROFILES"], 0)
        both = self.run_prepare(OUTPUT=str(self.folder / "gaps-both.xml"))
        sections, *_ = read_corridor_sections(load_document(both["OUTPUT"]).root)
        quantities = corridor_quantities(sections, "Prepared Existing Ground", "Prepared Proposed Terrain")
        self.assertFalse(quantities["intervals"])
        self.assertTrue(any("missing section data" in reason for _station, reason in quantities["skipped"]))
        # A transverse hole is omitted instead of being bridged by the section reader.
        sampler = TinSampler(np.array([[0., -2., 0.], [2., -2., 0.], [2., -0.1, 0.], [0., -0.1, 0.],
                                       [0., 0.1, 0.], [2., 0.1, 0.], [2., 2., 0.], [0., 2., 0.]]),
                             np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]), self.feedback)
        self.assertEqual(len(sampler.segment((1, -2), (1, 2), -2, 2)), 2)

    def test_no_overlap_and_cancellation_leave_existing_output_intact(self):
        output = self.folder / "prepared.xml"
        output.write_text("existing output")
        self.write("existing.xml", document(surfaces=surface("Terrain", [(1000, 1000, 1010, 1010)])))
        with self.assertRaisesRegex(QgsProcessingException, "no usable data"):
            self.run_prepare(PROPOSED_FILE="")
        self.assertEqual(output.read_text(), "existing output")
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException, "cancelled"):
            self.run_prepare()
        self.assertEqual(output.read_text(), "existing output")
        self.assertEqual(list(self.folder.glob("tmp*.xml")), [])

    def test_metadata_and_input_protection(self):
        for key in ("INPUT", "EXISTING_FILE", "PROPOSED_FILE"):
            with self.subTest(input=key), self.assertRaisesRegex(QgsProcessingException, "different from every input"):
                self.run_prepare(OUTPUT=self.params()[key])
        for kwargs, message in (({"unit": "foot", "vertical": "foot"}, "conflicting horizontal"),
                                ({"epsg": "2277"}, "conflicting coordinate"),
                                ({"datum": "DifferentDatum"}, "conflicting vertical datum")):
            self.write("existing.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)]), **kwargs))
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(QgsProcessingException, message):
                self.run_prepare()
        self.write("existing.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)]), vertical=None))
        with self.assertRaisesRegex(QgsProcessingException, "explicit vertical"):
            self.run_prepare()
        with self.assertRaisesRegex(QgsProcessingException, "Confirm"):
            self.run_prepare(VERTICAL_UNIT=1)
        self.run_prepare(VERTICAL_UNIT=1, CONFIRM_MATCHING=True)

    def test_surface_choice_embedded_inputs_and_stale_source(self):
        both = surface("Ground", [(-10, -60, 50, 60)]) + surface("Finished", [(-10, -60, 50, 60)], 102)
        self.write("existing.xml", document(surfaces=both))
        with self.assertRaisesRegex(QgsProcessingException, "Select the existing surface"):
            self.run_prepare(PROPOSED_FILE="")
        self.write("geometry.xml", document(alignment(), both))
        result = self.run_prepare(EXISTING_FILE="", PROPOSED_FILE="", EXISTING_SURFACE="Ground", PROPOSED_SURFACE="Finished")
        self.assertEqual(len(load_document(result["OUTPUT"]).surfaces()), 2)
        result = self.run_prepare(EXISTING_SURFACE="Ground")
        self.write("existing.xml", document(surfaces=surface("Ground", [(-10, -60, 50, 60)], 105)))
        with self.assertRaisesRegex(QgsProcessingException, "different data"):
            self.run_prepare(INPUT=result["OUTPUT"], EXISTING_SURFACE="Ground", OUTPUT=str(self.folder / "stale.xml"))

    def test_duplicate_names_conflicting_declarations_and_unsafe_xml(self):
        self.write("geometry.xml", document(alignment() + alignment()))
        with self.assertRaisesRegex(QgsProcessingException, "duplicated"):
            self.run_prepare()
        self.write("geometry.xml", document(alignment()).replace('</Units>', '<Imperial linearUnit="foot"/></Units>'))
        with self.assertRaisesRegex(QgsProcessingException, "conflicting unit declarations"):
            self.run_prepare(HORIZONTAL_UNIT=1, VERTICAL_UNIT=1, CONFIRM_MATCHING=True)
        self.write("geometry.xml", '<!DOCTYPE LandXML [<!ENTITY bad "no">]><LandXML>&bad;</LandXML>')
        with self.assertRaisesRegex(QgsProcessingException, "DOCTYPE"):
            self.run_prepare()

    def test_atomic_write_failure_and_external_namespace(self):
        self.write("existing.xml", Path(self.existing).read_text().replace('LandXML-1.2', 'LandXML-1.1'))
        output = self.folder / "prepared.xml"
        output.write_text("keep old output")
        with patch("landxml_plugin.landxml.preparation.os.replace", side_effect=OSError("write blocked")):
            with self.assertRaisesRegex(QgsProcessingException, "write blocked"):
                self.run_prepare()
        self.assertEqual(output.read_text(), "keep old output")
        self.assertEqual(list(self.folder.glob("tmp*.xml")), [])
        result = self.run_prepare()
        copied = load_document(result["OUTPUT"]).surfaces()[0]
        self.assertTrue(all(node.tag.startswith('{http://www.landxml.org/schema/LandXML-1.2}') for node in copied.iter()))

    def test_units_multiple_alignments_and_selection(self):
        for unit in ("meter", "usSurveyFoot", "internationalFoot"):
            geometry = alignment() + alignment("Other", 200)
            self.write("geometry.xml", document(geometry, unit=unit, vertical=unit))
            self.write("existing.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)]), unit=unit, vertical=unit))
            with self.subTest(unit=unit):
                result = self.run_prepare(PROPOSED_FILE="", ALIGNMENT="Other")
                doc = load_document(result["OUTPUT"])
                self.assertEqual(doc.horizontal_unit, unit)
                self.assertEqual(result["ALIGNMENT_COUNT"], 1)
                self.assertEqual(len(read_alignment_profiles(doc.alignments()[0])), 1)
                self.assertEqual(len(read_alignment_profiles(doc.alignments()[1])), 2)

    def test_sampler_shared_edges_hidden_faces_and_curved_stationing(self):
        self.assertEqual(regular_stations(103.19, 163.19, 20), [103.19, 120, 140, 160, 163.19])
        curve = '''<Alignment name="Bend" staStart="100" length="15.707963267948966"><CoordGeom>
          <Curve rot="cw" radius="10" length="15.707963267948966"><Start>10 0</Start><End>0 10</End><Center>0 0</Center></Curve>
          </CoordGeom></Alignment>'''
        self.write("geometry.xml", document(curve))
        result = self.run_prepare(PROPOSED_FILE="", LEFT_WIDTH=5, RIGHT_WIDTH=5)
        ground = read_alignment_profiles(load_document(result["OUTPUT"]).alignments()[0])[0]
        self.assertAlmostEqual(ground.station_range[1], 100 + np.pi * 5, places=8)
        self.assertAlmostEqual(ground.elevation_at(100), 101)
        self.assertAlmostEqual(ground.elevation_at(100 + np.pi * 5), 102)
        self.write("existing.xml", document(surfaces=surface("Terrain", [(-10, -60, 50, 60)], hidden=True)))
        with self.assertRaisesRegex(QgsProcessingException, "visible triangular faces"):
            self.run_prepare(PROPOSED_FILE="")

    def test_overlapping_triangles_with_different_elevations_are_rejected(self):
        vertices = np.array([[0., 0., 0.], [2., 0., 2.], [0., 2., 0.],
                             [0., 0., 2.], [2., 0., 0.], [0., 2., 2.]])
        sampler = TinSampler(vertices, np.array([[0, 1, 2], [3, 4, 5]]), self.feedback)
        with self.assertRaisesRegex(ValueError, "Overlapping terrain triangles"):
            sampler.segment((0, 0), (2, 0), 0, 2)

    def test_downstream_reports_sections_quantities_and_rasters(self):
        result = self.run_prepare()
        path = result["OUTPUT"]
        base = {"INPUT": path, "OUTPUT_CRS": runtime.QgsCoordinateReferenceSystem("EPSG:32637"), "METHOD": 0}
        jobs = [
            ("landxml_design_report", {"OUTPUT": str(self.folder / "design.html"), "DESIGN_SPEED": 60, "STANDARD": 1}),
            ("landxml_design_review", {"OUTPUT": "memory:", "DESIGN_SPEED": 60, "REPORT": str(self.folder / "review.html")}),
            ("landxml_surface_cut_fill", {"BASE_SURFACE": "Prepared Existing Ground", "COMPARE_SURFACE": "Prepared Proposed Terrain",
                                         "OUTPUT": str(self.folder / "depth.tif"), "RESOLUTION": 1}),
        ]
        jobs += [
            ("landxml_profile_cut_fill", {"OUTPUT": "memory:", "REPORT": str(self.folder / "profile.html")}),
            ("landxml_corridor_quantities", {"OUTPUT": "memory:", "REPORT": str(self.folder / "quantities.html")}),
            ("landxml_cross_section_sheets", {"OUTPUT": str(self.folder / "sections.pdf"), "REPORT": str(self.folder / "sections.html")}),
        ]
        for name, params in jobs:
            with self.subTest(tool=name):
                output = self.algorithms[name].processAlgorithm({**base, **params}, self.context, self.feedback)
                if name == "landxml_surface_cut_fill":
                    self.assertAlmostEqual(output["fill_volume"], 14400)
                if name in ("landxml_profile_cut_fill", "landxml_corridor_quantities"):
                    self.assertGreater(self.context.getMapLayer(output["OUTPUT"]).featureCount(), 0)
        self.assertTrue((self.folder / "design.html").is_file())
        self.assertTrue((self.folder / "depth.tif").is_file())
        self.assertEqual((self.folder / "sections.pdf").read_bytes()[:5], b"%PDF-")
        from landxml_plugin.gui.profile_viewer import ProfileViewerDock
        dock = ProfileViewerDock(None)
        try:
            self.assertTrue(dock.load(path))
            _values, depth = dock.chart.values_at(120.5)
            self.assertAlmostEqual(depth, 2)
        finally:
            dock.cleanup()
            dock.deleteLater()


if __name__ == "__main__":
    unittest.main()
