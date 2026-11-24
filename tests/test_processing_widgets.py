"""Processing's layer-registry callbacks can refresh LandXML name widgets."""

import importlib
from pathlib import Path
import sys
import unittest

from . import test_qgis_processing as runtime



def panel_wrappers(panel):
    """Dialog wrappers by name: an attribute up to QGIS 4.2, a method in 4.4 (as compat.panel_wrappers)."""
    wrappers = panel.wrappers
    wrappers = wrappers() if callable(wrappers) else wrappers
    if isinstance(wrappers, dict):
        return wrappers
    return {wrapper.parameterDefinition().name(): wrapper for wrapper in wrappers}      # QGIS 4.4: a list

class WidgetRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.QgisProcessingTests.setUpClass()
        sys.path.append(str(Path(runtime.QgsApplication.pkgDataPath()) / "python" / "plugins"))

    def test_framework_refresh_preserves_selected_name(self):
        try:
            from processing.gui.AlgorithmDialog import AlgorithmDialog  # QGIS 3
        except ImportError:  # QGIS 4
            from processing.gui.algorithm_widget import AlgorithmWidget as AlgorithmDialog
        from landxml_plugin import processing_widgets
        from landxml_plugin.prepare_algorithm import PrepareLandXMLAlgorithm

        importlib.reload(processing_widgets)
        algorithm = PrepareLandXMLAlgorithm()
        algorithm.initAlgorithm()
        dialog = AlgorithmDialog(algorithm)
        try:
            dialog.setParameters({"INPUT": runtime.CORRIDOR, "ALIGNMENT": "Synthetic Road"})
            wrapper = panel_wrappers(dialog.mainWidget())["ALIGNMENT"]
            wrapper.refresh()
            dialog.mainWidget().layerRegistryChanged([])
            self.assertEqual(wrapper.value(), "Synthetic Road")
            self.assertGreaterEqual(wrapper.widget.findText("Synthetic Road"), 0)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_scene_dialog_pickers_styles_and_saved_styles(self):
        try:
            from processing.gui.AlgorithmDialog import AlgorithmDialog  # QGIS 3
        except ImportError:  # QGIS 4
            from processing.gui.algorithm_widget import AlgorithmWidget as AlgorithmDialog
        from landxml_plugin import processing_widgets
        from landxml_plugin import scene_algorithm

        importlib.reload(processing_widgets)
        importlib.reload(scene_algorithm)
        algorithm = scene_algorithm.Scene3DAlgorithm()
        algorithm.initAlgorithm()
        dialog = AlgorithmDialog(algorithm)
        try:
            dialog.setParameters({"INPUT": runtime.CORRIDOR, "SYNTHETIC": "A; B", "SURFACES": "EG = plain #cfd8c4; FG = depth over EG",
                                  "RASTER_STYLE": "slope: 1, 5, 30", "CORRIDOR": "Synthetic Road"})
            wrappers = panel_wrappers(dialog.mainWidget())
            self.assertEqual(wrappers["SYNTHETIC"].value(), "A; B")
            self.assertEqual(wrappers["SURFACES"].value(), "EG = plain #cfd8c4; FG = depth over EG")
            self.assertEqual(wrappers["RASTER_STYLE"].value(), "slope: 1, 5, 30")
            self.assertIn("Synthetic Road", processing_widgets.names_for(runtime.CORRIDOR, "alignments"))
            self.assertEqual(wrappers["CORRIDOR"].value(), "Synthetic Road")
        finally:
            dialog.close()
            dialog.deleteLater()
        processing_widgets.save_user_style("My terrain", "elevation: equal 5 | greys")
        try:
            self.assertIn(("★ My terrain", "elevation: equal 5 | greys"), processing_widgets.style_choices())
            with self.assertRaises(ValueError):
                processing_widgets.save_user_style("Broken", "colourful")
        finally:
            processing_widgets.save_user_style("My terrain", "")
        self.assertNotIn("My terrain", processing_widgets.user_styles())
        for _label, text in processing_widgets.BUILT_IN_STYLES:       # every built-in style can be read
            from landxml_plugin.landxml.surface_style import parse_style

            parse_style(text)


if __name__ == "__main__":
    unittest.main()
