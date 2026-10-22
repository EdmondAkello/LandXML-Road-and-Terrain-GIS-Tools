"""Processing's layer-registry callbacks can refresh LandXML name widgets."""

import importlib
from pathlib import Path
import sys
import unittest

from . import test_qgis_processing as runtime


class WidgetRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.QgisProcessingTests.setUpClass()
        sys.path.append(str(Path(runtime.QgsApplication.pkgDataPath()) / "python" / "plugins"))

    def test_framework_refresh_preserves_selected_name(self):
        from processing.gui.AlgorithmDialog import AlgorithmDialog
        from landxml_plugin import processing_widgets
        from landxml_plugin.prepare_algorithm import PrepareLandXMLAlgorithm

        importlib.reload(processing_widgets)
        algorithm = PrepareLandXMLAlgorithm()
        algorithm.initAlgorithm()
        dialog = AlgorithmDialog(algorithm)
        try:
            dialog.setParameters({"INPUT": runtime.CORRIDOR, "ALIGNMENT": "Synthetic Road"})
            wrapper = dialog.mainWidget().wrappers["ALIGNMENT"]
            wrapper.refresh()
            dialog.mainWidget().layerRegistryChanged([])
            self.assertEqual(wrapper.value(), "Synthetic Road")
            self.assertGreaterEqual(wrapper.widget.findText("Synthetic Road"), 0)
        finally:
            dialog.close()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
