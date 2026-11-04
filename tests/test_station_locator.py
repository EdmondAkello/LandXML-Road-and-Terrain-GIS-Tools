"""Station locator map interaction and coordinate handling in Qt 5 and Qt 6."""

from pathlib import Path
import tempfile
import unittest

from tests.test_gui import application
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvas, QgsMapToolPan
from qgis.PyQt.QtCore import Qt

from landxml_plugin.gui.station_locator import StationOffsetDock

FIXTURE = str(Path(__file__).parent / "fixtures" / "generic" / "station_locator.xml")


class StationLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32615"))

        class Interface:
            def mapCanvas(_self):
                return self.canvas

        self.dock = StationOffsetDock(Interface())
        self.assertTrue(self.dock.load(FIXTURE))
        self.dock.method_combo.setCurrentIndex(1)
        self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32615"))

    def tearDown(self):
        self.dock.cleanup()
        self.dock.deleteLater()
        self.canvas.deleteLater()

    def test_click_anywhere_and_manual_location(self):
        self.dock.map_clicked(QgsPointXY(500025, 4500010))
        self.assertEqual(self.dock.station_edit.text(), "1+025.000")
        self.assertAlmostEqual(float(self.dock.offset_edit.text()), -10)
        self.assertIn("left", self.dock.result_label.text())
        foot = self.dock.markers[0].center()
        self.assertAlmostEqual(foot.x(), 500025)
        self.assertAlmostEqual(foot.y(), 4500000)
        self.dock.station_edit.setText("1075")
        self.dock.offset_edit.setText("5")
        self.dock.locate_entered()
        self.assertEqual(self.dock.station_edit.text(), "1+075.000")
        target = self.dock.markers[1].center()
        self.assertAlmostEqual(target.x(), 500075)
        self.assertAlmostEqual(target.y(), 4499995)
        self.assertIn("right", self.dock.result_label.text())
        # Outside the endpoint, offset remains perpendicular, distance does not.
        self.dock.map_clicked(QgsPointXY(499990, 4500005))
        self.assertEqual(self.dock.station_edit.text(), "1+000.000")
        self.assertEqual(float(self.dock.offset_edit.text()), -5)
        self.assertIn("11.180", self.dock.result_label.text())

    def test_swap_and_helmert_keep_source_units_and_physical_sides(self):
        self.dock.method_combo.setCurrentIndex(2)
        self.dock.map_clicked(QgsPointXY(4500020, 500025))
        self.assertEqual(self.dock.station_edit.text(), "1+025.000")
        self.assertEqual(float(self.dock.offset_edit.text()), 20)
        self.dock.method_combo.setCurrentIndex(5)
        self.dock.numbers["ROTATION"].setValue(90)
        self.dock.numbers["SCALE"].setValue(2)
        self.dock.numbers["DX"].setValue(100)
        self.dock.numbers["DY"].setValue(200)
        self.dock.station_edit.setText("1025")
        self.dock.offset_edit.setText("10")
        self.dock.locate_entered()
        point = self.dock.markers[1].center()
        self.assertAlmostEqual(point.x(), -9000000 + 100 + 20)
        self.assertAlmostEqual(point.y(), 1000050 + 200)
        self.dock.map_clicked(point)
        self.assertEqual(self.dock.station_edit.text(), "1+025.000")
        self.assertAlmostEqual(float(self.dock.offset_edit.text()), 10)

    def test_click_fields_round_to_three_decimals_without_moving_markers(self):
        point = QgsPointXY(500025.359468623, 4499991.748775319)
        self.dock.map_clicked(point)
        self.assertEqual(self.dock.station_edit.text(), "1+025.359")
        self.assertEqual(self.dock.offset_edit.text(), "8.251")
        self.assertIn("1+025.359", self.dock.result_label.text())
        self.assertNotIn("1025.359", self.dock.result_label.text())
        target = self.dock.markers[1].center()
        self.assertAlmostEqual(target.x(), point.x(), places=7)
        self.assertAlmostEqual(target.y(), point.y(), places=7)

    def test_manual_engineering_station_input_in_metres_and_feet(self):
        self.dock.station_edit.setText("1+025.359")
        self.dock.offset_edit.setText("8.251")
        self.dock.locate_entered()
        self.assertEqual(self.dock.station_edit.text(), "1+025.359")
        self.assertAlmostEqual(self.dock.markers[1].center().x(), 500025.359)
        self.assertIn("1+025.359", self.dock.result_label.text())
        self.assertTrue(self.dock.load(str(Path(FIXTURE).with_name("station_locator_feet.xml"))))
        crs = QgsCoordinateReferenceSystem("EPSG:2277")
        self.canvas.setDestinationCrs(crs)
        self.dock.output_crs.setCrs(crs)
        self.dock.station_edit.setText("50+25.359")
        self.dock.locate_entered()
        self.assertEqual(self.dock.station_edit.text(), "50+25.359")
        self.assertAlmostEqual(self.dock.markers[1].center().x(), 3000025.359)
        self.assertIn("50+25.359", self.dock.result_label.text())
        self.dock.station_edit.setText("50+100.000")
        self.dock.locate_entered()
        self.assertFalse(self.dock.markers[0].isVisible())
        self.assertIn("less than 100", self.dock.result_label.text())

    def test_map_crs_and_reprojection(self):
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.dock.method_combo.setCurrentIndex(6)
        self.dock.source_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:32615"))
        self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.dock.station_edit.setText("1025")
        self.dock.offset_edit.setText("12.5")
        self.dock.locate_entered()
        point = self.dock.markers[1].center()
        self.assertTrue(-94 < point.x() < -92)
        self.assertTrue(40 < point.y() < 42)
        self.dock.map_clicked(point)
        self.assertEqual(self.dock.station_edit.text(), "1+025.000")
        self.assertAlmostEqual(float(self.dock.offset_edit.text()), 12.5, places=5)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.assertIsNone(self.dock.polyline)
        self.assertFalse(self.dock.markers[0].isVisible())

    def test_invalid_equations_are_reported_without_loading_alignment(self):
        path = Path(FIXTURE).parent / "invalid_station_equation.xml"
        self.assertFalse(self.dock.load(str(path)))
        self.assertIn("staBack disagrees", self.dock.result_label.text())
        self.assertIsNone(self.dock.polyline)

    def test_equation_regions_metric_and_us_survey_feet(self):
        for vendor, crs, label in (("civil3d", "EPSG:32615", "1+950.000"),
                                   ("openroads", "EPSG:2277", "19+50.000")):
            with self.subTest(vendor=vendor):
                path = Path(FIXTURE).parents[1] / vendor / "station_equations.xml"
                self.assertTrue(self.dock.load(str(path)))
                self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem(crs))
                self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem(crs))
                self.dock.station_edit.setText(label)
                self.dock.locate_entered()
                self.assertIn("ambiguous", self.dock.result_label.text())
                self.dock.region_combo.setCurrentIndex(2)
                self.dock.locate_entered()
                self.assertAlmostEqual(self.dock.markers[0].center().x(), 501050)
                self.assertIn("R2", self.dock.result_label.text())
                self.dock.map_clicked(QgsPointXY(501050, 4500005))
                self.assertEqual(self.dock.station_edit.text(), label)
                self.assertIn("internal 2050", self.dock.result_label.text())
                self.dock.station_edit.setText("2450")
                self.dock.locate_entered()
                self.assertIn("does not exist", self.dock.result_label.text())
                self.assertFalse(self.dock.markers[0].isVisible())
                self.dock.map_clicked(QgsPointXY(501000, 4500000))
                self.assertIn("equation back", self.dock.result_label.text())
                self.assertIn("ahead", self.dock.result_label.text())

    def test_invalid_input_clears_old_markers(self):
        self.dock.map_clicked(QgsPointXY(500025, 4500010))
        for station, offset in (("999", "0"), ("1101", "0"), ("nan", "0"), ("1025", "inf"), ("bad", "0")):
            with self.subTest(station=station, offset=offset):
                self.dock.station_edit.setText(station)
                self.dock.offset_edit.setText(offset)
                self.dock.locate_entered()
                self.assertFalse(self.dock.markers[0].isVisible())
        for name, error in (("No station", "start station"),):
            self.dock.alignment_combo.setCurrentText(name)
            self.dock.map_clicked(QgsPointXY(25, 5))
            self.assertIn(error, self.dock.result_label.text())
        self.assertEqual(self.dock.alignment_combo.findText("Unsupported"), -1)

    def test_explicit_settings_and_unit_mismatch(self):
        self.dock.method_combo.setCurrentIndex(0)
        self.dock.pick_button.setChecked(True)
        self.assertFalse(self.dock.pick_button.isChecked())
        self.dock.method_combo.setCurrentIndex(1)
        self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem())
        self.dock.map_clicked(QgsPointXY(0, 0))
        self.assertIn("CRS", self.dock.result_label.text())
        self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:2277"))
        self.dock.map_clicked(QgsPointXY(0, 0))
        self.assertIn("No implicit", self.dock.result_label.text())
        self.dock.output_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.dock.map_clicked(QgsPointXY(0, 0))
        self.assertIsNone(self.dock.polyline)

    def test_feet_keep_numeric_station_offset_and_labels(self):
        self.assertTrue(self.dock.load(str(Path(FIXTURE).with_name("station_locator_feet.xml"))))
        crs = QgsCoordinateReferenceSystem("EPSG:2277")
        self.canvas.setDestinationCrs(crs)
        self.dock.output_crs.setCrs(crs)
        self.dock.map_clicked(QgsPointXY(3000025, 10000010))
        self.assertEqual(self.dock.station_edit.text(), "50+25.000")
        self.assertEqual(float(self.dock.offset_edit.text()), -10)
        self.assertIn("50+25.00", self.dock.result_label.text())
        self.assertIn("USSurveyFoot", self.dock.result_label.text())
        self.dock.locate_entered()
        self.assertAlmostEqual(self.dock.markers[1].center().y(), 10000010)

    def test_rejects_ambiguous_names_and_clears_failed_load(self):
        self.assertEqual(self.dock.alignment_combo.findText("Disconnected"), -1)
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.xml"
            duplicate.write_text(Path(FIXTURE).read_text().replace('name="Circle"', 'name="Tangent"'))
            self.assertFalse(self.dock.load(str(duplicate)))
            self.assertIn("unique", self.dock.result_label.text())
        self.assertIsNone(self.dock.document)
        self.assertEqual(self.dock.alignment_combo.count(), 0)

    def test_offset_methods_and_alignment_switch(self):
        for method, x, y in ((3, 500125, 4500205), (4, 4500105, 500225)):
            with self.subTest(method=method):
                self.dock.method_combo.setCurrentIndex(method)
                self.dock.numbers["DX"].setValue(100)
                self.dock.numbers["DY"].setValue(200)
                self.dock.map_clicked(QgsPointXY(x, y))
                self.assertEqual(self.dock.station_edit.text(), "1+025.000")
                self.assertAlmostEqual(float(self.dock.offset_edit.text()), -5 if method == 3 else 5)
        self.dock.alignment_combo.setCurrentText("Circle")
        self.assertIsNone(self.dock.polyline)
        self.assertFalse(self.dock.markers[0].isVisible())

    def test_tool_lifecycle_and_project_clear(self):
        previous = QgsMapToolPan(self.canvas)
        other = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(previous)
        self.dock.pick_button.setChecked(True)
        self.assertIs(self.canvas.mapTool(), self.dock.tool)
        self.canvas.setMapTool(other)
        self.assertFalse(self.dock.pick_button.isChecked())
        self.assertIs(self.canvas.mapTool(), other)
        self.dock.pick_button.setChecked(True)
        self.dock.pick_button.setChecked(False)
        self.assertIs(self.canvas.mapTool(), other)
        self.dock.show()
        self.dock.pick_button.setChecked(True)
        self.dock.map_clicked(QgsPointXY(500025, 4500010), Qt.MouseButton.RightButton)
        self.assertEqual(self.dock.markers, [])
        self.dock.tool.canvasClicked.emit(QgsPointXY(500025, 4500010), Qt.MouseButton.LeftButton)
        self.assertTrue(self.dock.markers[0].isVisible())
        self.dock.hide()
        self.assertFalse(self.dock.markers[0].isVisible())
        self.assertIs(self.canvas.mapTool(), other)
        QgsProject.instance().clear()
        self.assertIsNone(self.dock.document)
        self.assertEqual(self.dock.alignment_combo.count(), 0)


if __name__ == "__main__":
    unittest.main()
