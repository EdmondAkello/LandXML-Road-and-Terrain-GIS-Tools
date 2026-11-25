"""Profile viewer, chart rendering, exports and map picking (offscreen Qt)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import math
import os

import numpy as np
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.testing import start_app
from qgis.PyQt.QtCore import QRectF
from qgis.PyQt.QtGui import QColor, QImage, QPainter

CORRIDOR = str(ROOT / "tests" / "fixtures" / "civil3d" / "corridor_synthetic.xml")


def application():
    return start_app()


class ProfileViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        from landxml_plugin.gui.profile_viewer import ProfileViewerDock

        cls.Dock = ProfileViewerDock

    def setUp(self):
        self.dock = self.Dock(None)
        self.assertTrue(self.dock.load(CORRIDOR))

    def tearDown(self):
        self.dock.cleanup()
        self.dock.deleteLater()

    def test_defaults_and_analysis(self):
        self.assertEqual(self.dock.alignment_combo.currentText(), "Synthetic Road")
        self.assertIn("Synthetic FRL", self.dock.design_combo.currentText())
        self.assertIn("EG Surface", self.dock.ground_combo.currentText())
        result = self.dock.result
        self.assertEqual(result["summary"]["transitions"], 1)
        self.assertEqual(self.dock.table.rowCount(), 2)
        self.assertIn("1 cut↔fill transitions", self.dock.summary_label.text())
        values, depth = self.dock.chart.values_at(50)
        self.assertAlmostEqual(depth, 0.5)
        self.assertAlmostEqual(dict((n, v) for n, _c, v in values)["Synthetic FRL"], 100.5)
        self.dock.exclude_edit.setText("90-110")
        self.dock.refresh()
        self.assertEqual(len(self.dock.result["runs"]), 2)
        self.dock.exclude_edit.setText("bad")
        self.dock.refresh()
        self.assertIn("b3261e", self.dock.exclude_edit.styleSheet())

    def test_equation_csv_mapping_precedes_station_rounding(self):
        import csv
        from landxml_plugin.landxml.reports import write_csv

        path = str(ROOT / "tests/fixtures/civil3d/station_equations.xml")
        self.assertTrue(self.dock.load(path))
        rows = self.dock.sample_rows(1999.9999)
        before = next(row for row in rows if row[1] == 1999.9999)
        mapping = self.dock.document.station_maps()["Equation road"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stations.csv"
            write_csv(output, ["station_label", "station", "design", "ground", "depth", "kind"],
                      [before], mapping, "meter")
            with output.open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["station_region"], "1")
            self.assertAlmostEqual(float(row["station_engineering"]), 1999.9999)

    def test_chart_renders_shading_and_interaction(self):
        chart = self.dock.chart
        chart.resize(900, 500)
        image = QImage(900, 500, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        chart.render(painter, QRectF(0, 0, 900, 500))
        painter.end()
        colors = {image.pixelColor(x, y).name() for x in range(0, 900, 3) for y in range(0, 500, 3)}
        self.assertGreater(len(colors), 10)
        chart.zoom_to(40, 60)
        low, high = chart.view
        self.assertLess(high - low, 30)
        chart.set_marker(150)
        self.assertLessEqual(chart.view[0], 150)
        self.assertGreaterEqual(chart.view[1], 150)
        chart.fit()
        self.assertEqual(chart.view, (0, 200))
        self.dock.table.selectRow(1)
        self.assertEqual(chart.marker_station, 200)

    def test_profile_design_on_the_chart_and_saved_to_a_copy(self):
        from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
        from qgis.PyQt.QtGui import QMouseEvent
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication

        panel, chart = self.dock.design_panel, self.dock.chart
        chart.resize(900, 500)
        panel.spacing_spin.setValue(100)
        panel.new_from_ground()
        design = panel.design
        self.assertIsNotNone(design)
        self.assertIs(chart.editor, design)                                   # editing is on, on the chart
        self.assertEqual([p[0] for p in design.pvis], [0.0, 100.0, 200.0])
        self.assertEqual(panel.table.rowCount(), 3)
        self.assertTrue(any("(editing)" in line["name"] for line in chart.lines))

        def pixel(index):
            plot, low, high, z0, z1 = chart._frame()
            s, z, _l = design.pvis[index]
            return QPoint(round(plot.left() + (s - low) / (high - low) * plot.width()),
                          round(plot.top() + (z1 - z) / (z1 - z0) * plot.height()))

        start = pixel(1)
        before = design.pvis[1][1]
        QTest.mousePress(chart, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, start)
        for dy in (-10, -20, -30):
            # Qt 5's QTest.mouseMove only moves the cursor (no event on the offscreen platform)
            # and drops the modifiers, so send the drag moves as real events on both Qt versions.
            QApplication.sendEvent(chart, QMouseEvent(
                QEvent.Type.MouseMove, QPointF(start + QPoint(0, dy)), Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))
        QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, start + QPoint(0, -30))
        self.assertGreater(design.pvis[1][1], before)                          # dragged up
        self.assertEqual(design.pvis[1][0], 100.0)                             # Shift: up and down only
        self.assertEqual(chart.selected_pvi, 1)
        panel.undo_button.click()
        self.assertAlmostEqual(design.pvis[1][1], before)                      # the whole drag is one undo step

        plot = chart._frame()[0]
        QTest.mouseDClick(chart, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                          QPoint(round(plot.left() + plot.width() * 0.25), round(plot.center().y())))
        self.assertEqual(len(design.pvis), 4)                                  # double-click adds a PVI
        panel.apply_menu(1, "curve", 30.0)
        self.assertEqual(design.pvis[1][2], 30.0)
        panel.table.item(2, 1).setText("101.5")                                # typed level
        self.assertEqual(design.pvis[2][1], 101.5)
        panel.grade_spin.setValue(0.1)
        self.assertGreater(panel.checks_list.count(), 0)

        out = os.path.join(tempfile.mkdtemp(), "with_design.xml")
        panel.name_edit.setText("New FRL")
        self.assertEqual(panel.save(out), out)
        self.assertEqual(self.dock.path, out)                                  # the copy is opened
        self.assertIn("New FRL", self.dock.design_combo.currentText())
        self.assertIsNone(panel.save(CORRIDOR))                                # never over the source file

    def test_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            png = Path(directory) / "chart.png"
            self.assertTrue(self.dock.write_png(str(png), 800, 400))
            self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            svg = Path(directory) / "chart.svg"
            self.dock.write_svg(str(svg), 800, 400)
            self.assertIn("<svg", svg.read_text(encoding="utf-8"))
            pdf = Path(directory) / "chart.pdf"
            self.dock.write_pdf(str(pdf))
            self.assertEqual(pdf.read_bytes()[:5], b"%PDF-")
        rows = self.dock.sample_rows(50)
        self.assertEqual([row[0] for row in rows], ["0+000.00", "0+050.00", "0+100.00", "0+150.00", "0+200.00"])
        self.assertEqual(rows[3][-1], "cut")

    def test_map_pick_station_and_marker(self):
        canvas = QgsMapCanvas()
        canvas.resize(400, 400)
        crs = QgsCoordinateReferenceSystem("EPSG:32637")
        canvas.setDestinationCrs(crs)
        layer = QgsVectorLayer(
            "LineString?crs=EPSG:32637&field=alignment_name:string&field=station_start:double",
            "alignments",
            "memory",
        )
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(5000, 1000), QgsPointXY(5000, 1200)]))
        feature.setAttributes(["Synthetic Road", 0.0])
        layer.dataProvider().addFeatures([feature])
        canvas.setLayers([layer])
        canvas.setExtent(QgsRectangle(4800, 1000, 5200, 1200))
        from landxml_plugin.gui.map_tool import AlignmentPickTool

        tool = AlignmentPickTool(canvas)
        picked = tool.nearest(QgsPointXY(5000.4, 1050))
        self.assertIsNotNone(picked)
        picked_layer, picked_feature, layer_point = picked
        self.assertIsNone(tool.nearest(QgsPointXY(4850, 1050)))
        self.dock.canvas = canvas
        layer.setCustomProperty("landxml/source_path", CORRIDOR)
        self.dock.picked(picked_layer, picked_feature, layer_point)
        self.assertAlmostEqual(self.dock.chart.marker_station, 50, places=3)
        point = self.dock._map_point(150)
        self.assertAlmostEqual(point.x(), 5000)
        self.assertAlmostEqual(point.y(), 1150)
        self.dock.show_map_marker(150)
        self.assertTrue(self.dock.marker.isVisible())
        self.dock.cleanup()
        self.dock.canvas = None

    def test_profile_click_finds_alignment_line_without_a_pick(self):
        from qgis.core import QgsProject

        canvas = QgsMapCanvas()
        canvas.resize(400, 400)
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32637"))
        canvas.setExtent(QgsRectangle(4800, 900, 5200, 1100))
        whole = QgsVectorLayer("LineString?crs=EPSG:32637&field=alignment_name:string&field=station_start:double&field=station_end:double",
                               "alignments", "memory")
        element = QgsVectorLayer("LineString?crs=EPSG:32637&field=alignment_name:string&field=sta_start:double&field=sta_end:double",
                                 "alignment_elements", "memory")
        for layer, geometry, start, end in ((whole, [(5000, 1000), (5000, 1200)], 0.0, 200.0),
                                            (element, [(5000, 1000), (5000, 1050)], 0.0, 50.0)):
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*xy) for xy in geometry]))
            feature.setAttributes(["Synthetic Road", start, end])
            layer.dataProvider().addFeatures([feature])
            layer.setCustomProperty("landxml/source_path", CORRIDOR)
        QgsProject.instance().addMapLayers([whole, element])
        try:
            self.dock.canvas = canvas
            self.dock.link = None
            point = self.dock._map_point(150)
            self.assertAlmostEqual(point.y(), 1150)
            # A pick on one element does not stop clicks beyond it from reaching the map.
            picked = next(element.getFeatures())
            self.dock.picked(element, picked, QgsPointXY(5000, 1020))
            self.assertAlmostEqual(self.dock._map_point(30).y(), 1030)
            self.assertAlmostEqual(self.dock._map_point(180).y(), 1180)
            self.dock.center_map(180)
            self.assertAlmostEqual(canvas.center().y(), 1180, places=3)
        finally:
            QgsProject.instance().removeMapLayers([whole.id(), element.id()])
            self.dock.cleanup()
            self.dock.canvas = None

    def test_foot_section_area_labels_in_shared_renderer(self):
        from landxml_plugin.gui.section_chart import section_svg, SectionStyle

        self.dock.tabs.setCurrentIndex(1)
        view = self.dock.section_panel.chart.current()
        svg = section_svg(
            view, SectionStyle(unit="USSurveyFoot", vertical_unit="USSurveyFoot")
        )
        self.assertIn("ftUS²", svg)
        self.assertNotIn("m²", svg)
        svg = section_svg(view, SectionStyle(unit="foot", vertical_unit="meter"))
        self.assertIn("ft·m", svg)
        svg = section_svg(view, SectionStyle(unit="foot"))
        self.assertIn("ft·unknown", svg)

    def test_cross_section_tab_and_exports(self):
        self.assertTrue(self.dock.tabs.isTabEnabled(1))
        self.dock.tabs.setCurrentIndex(1)
        panel = self.dock.section_panel
        self.assertEqual(panel.ground_combo.currentText(), "EG Surface")
        self.assertEqual(panel.design_combo.currentText(), "Synthetic Corridor TOP")
        self.assertEqual(len(panel.chart.views), 5)
        self.dock.chart.stationClicked.emit(140.0)
        self.assertEqual(panel.chart.current()["station"], 150)
        panel.chart.step(1)
        self.assertEqual(self.dock.chart.marker_station, 200)
        with tempfile.TemporaryDirectory() as directory:
            png = Path(directory) / "section.png"
            self.assertTrue(panel.write_png(str(png)))
            self.assertEqual(png.read_bytes()[:4], b"\x89PNG")
            svg = Path(directory) / "section.svg"
            self.assertTrue(panel.write_svg(str(svg)))
            self.assertIn("CH 0+200.00", svg.read_text(encoding="utf-8"))
            pdf = Path(directory) / "sheets.pdf"
            self.assertEqual(panel.write_sheets(str(pdf), per_page=4), 2)

    def test_click_outside_section_coverage(self):
        self.dock.tabs.setCurrentIndex(1)
        panel = self.dock.section_panel
        self.dock.chart.set_marker(150.0)
        self.dock.chart.stationClicked.emit(150.0)
        self.assertIn("(4/5)", panel.station_label.text())
        # Far beyond the last section: the nearest section is shown, the
        # label says there is no section there, and the marker is not pulled back.
        self.dock.chart.set_marker(900.0)
        self.dock.chart.stationClicked.emit(900.0)
        self.assertEqual(panel.chart.current()["station"], 200)
        self.assertIn("No section at", panel.station_label.text())
        self.assertEqual(self.dock.chart.marker_station, 900.0)

    def test_section_hover_uses_the_drawn_range(self):
        # The viewer leaves the style's x range unset, so hover must use the
        # range the chart drew with (regression: TypeError on mouse move).
        from qgis.PyQt.QtCore import QPointF

        self.dock.tabs.setCurrentIndex(1)
        chart = self.dock.section_panel.chart
        self.assertIsNone(chart.style.x_range)
        chart.resize(700, 400)
        image = QImage(700, 400, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        chart.render(image)
        plot, _x = chart._mappers

        class Move:
            def position(self):
                return QPointF(plot.center().x(), plot.center().y())

        chart.mouseMoveEvent(Move())
        self.assertIsNotNone(chart._hover)
        from landxml_plugin.gui.section_chart import auto_range

        low, high = auto_range(chart.current())
        self.assertAlmostEqual(chart._hover, (low + high) / 2, places=6)

    def test_name_dropdown_source(self):
        from landxml_plugin.processing_widgets import names_for

        self.assertEqual(names_for(CORRIDOR, "surfaces"), ["EG", "Design"])
        self.assertEqual(names_for("", "surfaces"), [])


class PluginGuiTests(unittest.TestCase):
    def test_plugin_actions_with_interface_stub(self):
        application()
        from landxml_plugin.landxml_tin_to_geotiff import LandXMLTinToGeoTIFFPlugin
        from qgis.PyQt.QtWidgets import QMainWindow

        class Interface:
            def __init__(self):
                self.window = QMainWindow()
                self.canvas = QgsMapCanvas()
                self.menus, self.icons, self.docks = [], [], []

            def mainWindow(self):
                return self.window

            def mapCanvas(self):
                return self.canvas

            def addPluginToMenu(self, menu, action):
                self.menus.append(action)

            def removePluginMenu(self, menu, action):
                self.menus.remove(action)

            def addToolBarIcon(self, action):
                self.icons.append(action)

            def removeToolBarIcon(self, action):
                if action in self.icons:
                    self.icons.remove(action)

            def addDockWidget(self, area, dock):
                self.docks.append(dock)
                self.window.addDockWidget(area, dock)

            def removeDockWidget(self, dock):
                self.docks.remove(dock)
                self.window.removeDockWidget(dock)

        iface = Interface()
        plugin = LandXMLTinToGeoTIFFPlugin(iface)
        plugin.initGui()
        self.assertEqual(len(iface.menus), 8)          # workflow, viewer, pick, plan, section, grading, locator, guide
        self.assertIn("User Guide", [a.text() for a in iface.menus])
        self.assertEqual([a.text() for a in iface.icons], ["LandXML Road && Terrain"])      # one button with a menu
        self.assertEqual([a.text() for a in plugin.toolbar_menu.actions() if not a.isSeparator()],
                         [a.text() for a in iface.menus])
        self.assertFalse(any(a.icon().isNull() for a in iface.menus))
        editor = plugin.show_section_editor()
        window = editor.pop_out()                                           # own window, content moved
        self.assertTrue(editor.popped_out)
        self.assertIs(editor._content.parent(), window)
        window.close()                                                      # closing it docks back
        self.assertFalse(editor.popped_out)
        self.assertIs(editor.widget(), editor._content)
        from qgis.PyQt.QtWidgets import QDockWidget
        self.assertFalse(editor.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        editor.pop_out()                                                    # left popped out: unload docks it back
        from qgis.core import QgsSettings
        from landxml_plugin.project_settings import plugin_version
        self.assertEqual(QgsSettings().value("LandXMLRoadTerrain/welcomed_version"), plugin_version())   # shown once
        from qgis.gui import QgsMessageBar
        bar = QgsMessageBar()
        iface.messageBar = lambda: bar
        plugin._welcome_once()
        self.assertEqual(len(bar.items()), 0)                  # not again for the same version
        QgsSettings().setValue("LandXMLRoadTerrain/welcomed_version", "0.0")
        plugin._welcome_once()
        self.assertEqual(len(bar.items()), 1)                  # a new version is welcomed, with its buttons
        buttons = [w.text() for w in bar.items()[0].findChildren(__import__("qgis.PyQt.QtWidgets", fromlist=["QPushButton"]).QPushButton)]
        self.assertEqual(buttons, ["Open Profile Viewer", "User Guide"])
        plugin.show_locator()
        self.assertIsNotNone(plugin.locator)
        self.assertIsNotNone(plugin.show_section_editor())
        self.assertIsNotNone(plugin.show_plan_editor())
        self.assertIsNotNone(plugin.show_grading_editor())
        plugin._record("plan", "/tmp/plan.xml", "Road A")
        workflow = plugin.show_workflow()
        self.assertIn("plan.xml", workflow.labels["plan"].text())               # results replayed into the panel
        plugin._record("corridor", "/tmp/corridor.xml", "Road A")
        self.assertEqual(workflow.latest_road(), ("/tmp/corridor.xml", "Road A"))
        plugin.pick_on_map()
        self.assertIs(iface.canvas.mapTool(), plugin.viewer.tool)
        plugin.unload()
        self.assertEqual(iface.menus, [])
        self.assertEqual(iface.docks, [])
        self.assertEqual(iface.icons, [])
        self.assertIsNone(plugin.provider)


class SectionEditorTests(unittest.TestCase):
    """The Corridor Section Editor dock on a synthetic road (offscreen Qt)."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()
        from landxml_plugin.gui.section_editor import SectionEditorDock
        from tests.test_section_editor import curved_road

        cls.Dock = SectionEditorDock
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = curved_road(os.path.join(cls.tmp.name, "curved.xml"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dock = self.Dock(None)
        self.dock.standard_combo.setCurrentIndex(self.dock.standard_combo.findData("kenya_rdm13_2025_sections"))
        self.dock.template_combo.setCurrentText("R5 single carriageway")
        self.dock.set_file(self.path)
        self.dock.axis_combo.setCurrentIndex(1)
        self.dock.load_context()

    def tearDown(self):
        self.dock.cleanup()
        self.dock.deleteLater()

    def test_ground_from_a_project_raster_layer_and_remembered_standard(self):
        from osgeo import gdal, osr
        from qgis.core import QgsProject, QgsRasterLayer

        dem = os.path.join(self.tmp.name, "dem4326.tif")
        ds = gdal.GetDriverByName("GTiff").Create(dem, 10, 10, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((38.99, 0.002, 0, -0.88, 0, -0.002))
        ref = osr.SpatialReference()
        ref.ImportFromEPSG(4326)
        ds.SetProjection(ref.ExportToWkt())
        ds = None
        layer = QgsRasterLayer(dem, "Kenya DEM")
        QgsProject.instance().addMapLayer(layer)
        try:
            dock = self.Dock(None)
            self.assertEqual(dock.standard_combo.currentData(), "kenya_rdm13_2025_sections")   # remembered
            dock.set_file(self.path)
            kinds = [dock.ground_combo.itemData(i)[0] for i in range(dock.ground_combo.count())]
            self.assertEqual(kinds[:2], ["tin", "layer"])
            self.assertEqual(dock.ground_combo.currentData()[0], "tin")       # the file's EG surface first
            dock.ground_combo.setCurrentIndex(1)
            spec = dock._ground_spec()
            self.assertEqual(spec["type"], "dem")
            self.assertEqual(spec["crs"], "EPSG:4326")
            dock.axis_combo.setCurrentIndex(1)
            dock.crs_widget.setCrs(QgsCoordinateReferenceSystem())
            dock.load_context()                                              # a DEM in another CRS needs the design CRS
            self.assertIsNone(dock.context)
            self.assertIn("design CRS", dock.status.text())
            dock.deleteLater()
        finally:
            QgsProject.instance().removeMapLayer(layer.id())

    def test_axis_order_must_be_chosen(self):
        dock = self.Dock(None)
        dock.set_file(self.path)
        dock.load_context()
        self.assertIsNone(dock.context)
        self.assertIn("axis order", dock.status.text())
        dock.deleteLater()

    def test_live_section_edit_undo_and_why(self):
        dock = self.dock
        self.assertIsNotNone(dock.section)
        self.assertEqual(dock.element_list.count(), 4)                     # lane, shoulder, side drain, daylight
        dock.set_station(260.0)
        lane_width = float(dock._items()[0]["width"])
        etw_before = next(p for p in dock.section["top"] if p.code == "ETW" and p.d > 0)
        dock.element_list.setCurrentRow(0)
        dock._set_element("width", 4.0)
        etw = next(p for p in dock.section["top"] if p.code == "ETW" and p.d > 0)
        self.assertAlmostEqual(etw.d - etw_before.d, 4.0 - lane_width, places=6)
        dock.undo()
        etw = next(p for p in dock.section["top"] if p.code == "ETW" and p.d > 0)
        self.assertAlmostEqual(etw.d, etw_before.d, places=6)
        dock.redo()
        self.assertEqual(dock._items()[0]["width"], 4.0)
        # a drag is one undo step
        steps = len(dock.undo_stack)
        for w in (4.1, 4.2, 4.3):
            dock._drag_value("right", "lane1", "width", w)
        dock._drag_finished()
        self.assertEqual(len(dock.undo_stack), steps + 1)
        dock._set_path(("superelevation", "source"), "standard")
        etw = next(p for p in dock.section["top"] if p.code == "ETW" and p.d > 0)
        dock._point_clicked(etw)
        self.assertIn("Superelevation on the right", dock.why_text.toPlainText())
        self.assertIn("computed from Kenya RDM", etw.why)
        # add a kerb before the side drain and a barrier; departures are reported, never blocking
        dock.element_list.setCurrentRow(1)
        dock.add_element("kerb")
        self.assertTrue(any(p.code == "KERB_T" for p in dock.section["top"]))
        dock.element_list.setCurrentRow(0)
        dock._set_element("width", 2.5)
        texts = [dock.checks_list.item(i).text() for i in range(dock.checks_list.count())]
        self.assertTrue(any("Table 4.1" in t for t in texts))

    def test_render_regions_and_build(self):
        from qgis.PyQt.QtCore import QRectF

        dock = self.dock
        image = QImage(1000, 500, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        dock.chart.resize(1000, 500)
        dock.chart.render(painter, QRectF(0, 0, 1000, 500))
        painter.end()
        colors = {image.pixelColor(x, y).name() for x in range(0, 1000, 5) for y in range(0, 500, 5)}
        self.assertGreater(len(colors), 8)
        dock.set_station(300.0)
        dock.split_region()
        self.assertEqual(len(dock.project["regions"]), 2)
        out = os.path.join(self.tmp.name, "built.xml")
        from qgis.PyQt.QtWidgets import QFileDialog
        original = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        try:
            dock.build()
        finally:
            QFileDialog.getSaveFileName = original
        self.assertTrue(os.path.isfile(out))
        self.assertTrue(os.path.isfile(out[:-4] + "_quantities.xlsx"))
        self.assertTrue(os.path.isfile(out[:-4] + "_report.html"))

    def test_adopts_a_profile_saved_in_the_profile_viewer(self):
        from landxml_plugin.landxml.writer import VerticalProfile
        from landxml_plugin.landxml.profile_design import splice_profile

        dock = self.dock
        text = Path(self.path).read_text(encoding="utf-8")
        copy_path = os.path.join(self.tmp.name, "with_new_frl.xml")
        new = VerticalProfile("New FRL", [(0, 1502.0, 0), (dock.context["frame"].end, 1510.0, 0)])
        Path(copy_path).write_text(splice_profile(text, "Curved", new), encoding="utf-8")
        self.assertFalse(dock.adopt_profile(copy_path, "Other", "New FRL"))
        self.assertTrue(dock.adopt_profile(copy_path, "Curved", "New FRL"))
        self.assertEqual(dock.context["profile"].name, "New FRL")
        self.assertAlmostEqual(dock.context["profile"].elevation(0.0), 1502.0)

    def test_features_build_into_the_section(self):
        dock = self.dock
        dock.set_station(100.0)
        dock.feature_type.setCurrentIndex(dock.feature_type.findData("bus_bay"))
        dock.add_feature()
        self.assertEqual(dock.feature_list.count(), 1)
        dock.set_station(115.0)
        self.assertTrue(any(p.code == "KERB_T" for p in dock.section["top"]))
        self.assertEqual(dock.section["features"][0]["type"], "bus_bay")
        dock._set_feature(0, "depth", 5.0)
        bay = [p for p in dock.section["top"] if p.eid == "feat_bus_bay_1"]
        self.assertTrue(bay)
        dock.undo()
        dock.undo()
        self.assertEqual(dock.project["features"], [])


class GradingEditorTests(unittest.TestCase):
    """The Grading Editor: draw a pad and a line on the map, set levels, preview, build (offscreen canvas)."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_draw_edit_preview_and_build(self):
        from osgeo import gdal, osr
        from qgis.core import QgsProject, QgsRasterLayer
        from qgis.gui import QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, Qt
        from landxml_plugin.gui.grading_editor import GradingEditorDock

        class Iface:
            def __init__(self):
                self.canvas = QgsMapCanvas()
                self.canvas.resize(800, 600)
                self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32737"))
                self.canvas.setExtent(QgsRectangle(500000, 9900000, 500200, 9900150))

            def mapCanvas(self):
                return self.canvas

            def mainWindow(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            tif = os.path.join(directory, "eg.tif")
            out = gdal.GetDriverByName("GTiff").Create(tif, 300, 300, 1, gdal.GDT_Float32)
            out.SetGeoTransform((499950, 1, 0, 9900200, 0, -1))
            ref = osr.SpatialReference()
            ref.ImportFromEPSG(32737)
            out.SetProjection(ref.ExportToWkt())
            cols = np.arange(300)[None, :].repeat(300, 0)
            out.GetRasterBand(1).WriteArray(100.0 + 0.03 * cols)
            out = None
            layer = QgsRasterLayer(tif, "EG")
            QgsProject.instance().addMapLayer(layer)
            iface = Iface()
            dock = GradingEditorDock(iface)
            try:
                dock.ground.setLayer(layer)
                dock.pad_button.setChecked(True)
                self.assertEqual(dock.design_crs.authid(), "EPSG:32737")      # the projected project CRS
                tool = iface.canvas.mapTool()

                def event(x, y, button=Qt.MouseButton.LeftButton, kind=QEvent.Type.MouseButtonPress,
                          modifiers=Qt.KeyboardModifier.NoModifier):
                    pixel = iface.canvas.getCoordinateTransform().transform(QgsPointXY(x, y))
                    return QgsMapMouseEvent(iface.canvas, kind, QPoint(round(pixel.x()), round(pixel.y())), button, button,
                                            modifiers)

                for x, y in ((500050, 9900050), (500110, 9900050), (500110, 9900100), (500050, 9900100)):
                    tool.canvasPressEvent(event(x, y))
                tool.canvasPressEvent(event(500050, 9900100, Qt.MouseButton.RightButton))   # finish
                self.assertEqual(len(dock.objects), 1)
                pad = dock.objects[0]
                self.assertEqual(len(pad["vertices"]), 4)
                self.assertAlmostEqual(pad["vertices"][0][2], 103.0, delta=0.1)          # first vertex: the ground
                self.assertEqual({v[2] for v in pad["vertices"]}, {pad["vertices"][0][2]})  # a level pad to start
                self.assertTrue(dock.edit_button.isChecked())                            # straight into editing
                # type a level in the table: vertex 3 raised
                dock.table.item(2, 2).setText("105.5")
                self.assertEqual(pad["vertices"][2][2], 105.5)
                self.assertIn("+", dock.table.item(2, 4).text())
                # drag vertex 2 east by 10 m: one undo step
                tool.canvasPressEvent(event(500110, 9900050))
                tool.canvasMoveEvent(event(500120, 9900050, kind=QEvent.Type.MouseMove))
                tool.canvasReleaseEvent(event(500120, 9900050, kind=QEvent.Type.MouseButtonRelease))
                self.assertAlmostEqual(pad["vertices"][1][0], 500120, delta=0.5)
                dock.undo()
                self.assertAlmostEqual(dock.objects[0]["vertices"][1][0], 500110, delta=0.5)
                # insert a vertex on an edge with Ctrl+click
                tool.canvasPressEvent(event(500080, 9900050, modifiers=Qt.KeyboardModifier.ControlModifier))
                self.assertEqual(len(dock.objects[0]["vertices"]), 5)
                # a graded line (ramp) up to the pad
                dock.line_button.setChecked(True)
                for x, y in ((500010, 9900075), (500050, 9900075)):
                    tool.canvasPressEvent(event(x, y))
                tool.canvasDoubleClickEvent(event(500050, 9900075, kind=QEvent.Type.MouseButtonDblClick))
                self.assertEqual([o["kind"] for o in dock.objects], ["pad", "line"])
                dock.update_preview()
                self.assertIsNotNone(dock.result)
                self.assertIn("Cut", dock.totals.text())
                xml = os.path.join(directory, "site.xml")
                self.assertEqual(dock.build(xml), xml)
                self.assertTrue(os.path.isfile(os.path.join(directory, "site_quantities.html")))
                from landxml_plugin.landxml.catalog import read_catalog
                self.assertEqual(read_catalog(xml).surfaces, ["Existing ground", "Design"])
                # reopen the objects
                again = GradingEditorDock(iface)
                again.open_objects(os.path.join(directory, "site_objects.json"))
                self.assertEqual(len(again.objects), 2)
                again.cleanup()
            finally:
                dock.cleanup()
                QgsProject.instance().removeMapLayer(layer.id())


class PlanEditorTests(unittest.TestCase):
    """The plan editor dock and its map tool on an offscreen canvas."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_draw_drag_refuse_and_save(self):
        from qgis.core import QgsMapLayerProxyModel  # noqa: F401 - imported by the dock
        from qgis.gui import QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, Qt
        from landxml_plugin.gui.plan_editor import PlanEditorDock, PlanEditTool

        class Iface:
            def __init__(self):
                self.canvas = QgsMapCanvas()
                self.canvas.resize(800, 600)
                self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32737"))
                self.canvas.setExtent(QgsRectangle(0, 0, 1000, 750))

            def mapCanvas(self):
                return self.canvas

            def activeLayer(self):
                return None

        iface = Iface()
        dock = PlanEditorDock(iface)
        dock.radius.setValue(150)
        dock.draw_button.setChecked(True)
        self.assertIsInstance(iface.canvas.mapTool(), PlanEditTool)
        tool = iface.canvas.mapTool()

        def click(mx, my, button=Qt.MouseButton.LeftButton, kind=QEvent.Type.MouseButtonPress):
            pixel = iface.canvas.getCoordinateTransform().transform(QgsPointXY(mx, my))
            return QgsMapMouseEvent(iface.canvas, kind, QPoint(round(pixel.x()), round(pixel.y())), button, button,
                                    Qt.KeyboardModifier.NoModifier)

        for x, y in ((100, 100), (500, 100), (700, 400)):
            tool.canvasPressEvent(click(x, y))
        design = dock.design
        self.assertEqual(len(design.pis), 3)
        self.assertIsNotNone(design.alignment)
        self.assertEqual(design.curves[0]["radius"], 150)
        self.assertGreater(dock.elements.rowCount(), 2)
        # drag the middle PI: one undo step
        before = list(design.pis[1])
        tool.canvasPressEvent(click(*before))
        for dx in (5, 10, 20):
            tool.canvasMoveEvent(click(before[0] + dx, before[1], kind=QEvent.Type.MouseMove))
        tool.canvasReleaseEvent(click(before[0] + 20, before[1], kind=QEvent.Type.MouseButtonRelease))
        self.assertGreater(design.pis[1][0], before[0])
        design.undo()
        self.assertAlmostEqual(design.pis[1][0], before[0], places=3)
        # a radius that cannot fit is refused with the reason, the design is kept
        dock.edit(lambda: design.set_curve(1, radius=100000))
        self.assertIn("overlap", dock.status.text())
        self.assertEqual(design.curves[0]["radius"], 150)
        # typed values in the table
        dock.table.item(1, 4).setText("30")
        self.assertEqual(design.curves[0]["spiral_in"], 30.0)
        self.assertTrue(dock.handles())
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "plan.xml")
            self.assertIsNone(dock.save(path))                     # the axis order must be chosen
            dock.axis.setCurrentIndex(1)
            seen = []
            dock.saved.connect(lambda p, n: seen.append(p))
            self.assertEqual(dock.save(path), path)
            self.assertEqual(seen, [path])
            from landxml_plugin.landxml.geometry import read_alignments
            records, _bad = read_alignments(path, 1.0)
            self.assertAlmostEqual(records[0]["vertex_distances"][-1], design.alignment.length, places=3)
        dock.cleanup()

    def test_profile_viewer_opens_ready_to_design(self):
        """Plan Editor → 'Design the profile': the viewer opens on the Design tab with a profile from the ground."""
        from landxml_plugin.gui.profile_viewer import ProfileViewerDock
        from landxml_plugin.landxml.writer import GroundProfile, HorizontalAlignment, Line, write_landxml

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "plan.xml")
            road = HorizontalAlignment("New road", start=(1000.0, 5000.0), bearing=30.0, elements=[Line(600.0)])
            ground = GroundProfile("New road ground", [(s, 100 + 5 * math.sin(s / 80)) for s in range(0, 601, 10)])
            write_landxml(path, [(road, [ground])], northing_first=False)
            viewer = ProfileViewerDock(None)
            self.assertTrue(viewer.design(path, "New road"))
            self.assertIs(viewer.tabs.currentWidget(), viewer.design_panel)
            self.assertIsNotNone(viewer.design_panel.design)
            self.assertTrue(viewer.design_panel.edit_button.isChecked())
            viewer.cleanup()

    def test_design_crs_over_a_geographic_map_and_layers(self):
        """A map in degrees: the design keeps its projected CRS, clicks are transformed, the saved file declares it."""
        from qgis.core import QgsProject
        from qgis.gui import QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, Qt
        from landxml_plugin.gui import plan_editor
        from landxml_plugin.gui.plan_editor import PlanEditorDock

        utm = QgsCoordinateReferenceSystem("EPSG:32737")
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")

        class Iface:
            def __init__(self):
                self.canvas = QgsMapCanvas()
                self.canvas.resize(800, 600)
                self.canvas.setDestinationCrs(wgs)
                self.canvas.setExtent(QgsRectangle(36.28, -0.92, 36.32, -0.89))

            def mapCanvas(self):
                return self.canvas

            def mainWindow(self):
                return None

            def activeLayer(self):
                return None

        iface = Iface()
        dock = PlanEditorDock(iface)
        asked = []
        original = plan_editor.ask_crs
        plan_editor.ask_crs = lambda *a, **k: asked.append(1) or utm      # the user's choice
        try:
            dock.draw_button.setChecked(True)                               # geographic map: asks once
            self.assertEqual(asked, [1])
            self.assertEqual(dock.design_crs.authid(), "EPSG:32737")
            tool = iface.canvas.mapTool()

            def click(lon, lat):
                pixel = iface.canvas.getCoordinateTransform().transform(QgsPointXY(lon, lat))
                return QgsMapMouseEvent(iface.canvas, QEvent.Type.MouseButtonPress, QPoint(round(pixel.x()), round(pixel.y())),
                                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

            for lon, lat in ((36.285, -0.915), (36.30, -0.90), (36.315, -0.912)):
                tool.canvasPressEvent(click(lon, lat))
            design = dock.design
            self.assertEqual(len(design.pis), 3)
            self.assertGreater(design.pis[0][0], 100000)                    # stored in metres (UTM), not degrees
            back = dock.to_map(*design.pis[1])
            self.assertAlmostEqual(back.x(), 36.30, delta=1e-4)
            # map layers in the design CRS, kept in sync
            layers = [QgsProject.instance().mapLayer(i) for i in dock.layers.values()]
            self.assertEqual(len(layers), 2)
            self.assertTrue(all(layer.crs() == utm for layer in layers))
            pis = QgsProject.instance().mapLayer(dock.layers["pi"])
            self.assertEqual(pis.featureCount(), 3)
            # a declared geographic CRS that contradicts metre coordinates is not trusted: the user is asked
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "plan.xml")
                dock.axis.setCurrentIndex(2)
                self.assertEqual(dock.save(path), path)
                with open(path, encoding="utf-8") as handle:
                    self.assertIn('epsgCode="32737"', handle.read())
                bad = os.path.join(directory, "bad.xml")
                with open(path, encoding="utf-8") as handle:
                    text = handle.read().replace('epsgCode="32737"', 'epsgCode="4326"')
                with open(bad, "w", encoding="utf-8") as handle:
                    handle.write(text)
                asked.clear()
                self.assertTrue(dock._crs_for_file(bad, [design.pis[0][0]], [design.pis[0][1]]))
                self.assertEqual(asked, [1])
                asked.clear()
                self.assertTrue(dock._crs_for_file(path, [design.pis[0][0]], [design.pis[0][1]]))
                self.assertEqual(asked, [])                                 # a fitting declared CRS is used
        finally:
            plan_editor.ask_crs = original
            for layer_id in dock.layers.values():
                QgsProject.instance().removeMapLayer(layer_id)
            dock.cleanup()


if __name__ == "__main__":
    unittest.main()
