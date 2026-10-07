"""Profile viewer, chart rendering, exports and map picking (offscreen Qt)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import os
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
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QRectF
from qgis.PyQt.QtGui import QColor, QImage, QPainter

CORRIDOR = str(ROOT / "tests" / "fixtures" / "civil3d" / "corridor_synthetic.xml")


_APPLICATION = []


def application():
    app = QgsApplication.instance()
    if app is None:
        app = QgsApplication([], True)
        app.initQgis()
    _APPLICATION[:] = [app]  # keep a reference so it is never collected
    return app


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
        self.assertEqual(len(iface.menus), 2)
        plugin.pick_on_map()
        self.assertIs(iface.canvas.mapTool(), plugin.viewer.tool)
        plugin.unload()
        self.assertEqual(iface.menus, [])
        self.assertEqual(iface.docks, [])
        self.assertIsNone(plugin.provider)


if __name__ == "__main__":
    unittest.main()
