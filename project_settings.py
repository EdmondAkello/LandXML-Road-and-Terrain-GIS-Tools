"""Remember successful Processing dialog inputs inside the QGIS project."""

import json

from qgis.core import QgsProcessingAlgorithm, QgsProject, QgsVariantUtils

from .compat import panel_wrappers


# Rarely changed coordinate adjustments sit in the dialog's Advanced section in every tool.
ADVANCED_EVERYWHERE = {"DX", "DY", "ROTATION", "SCALE"}


# The user guide ships inside the plugin; each tool's Help opens its section offline.
GUIDE_SECTIONS = {
    "inspect_landxml": "inspect", "prepare_landxml": "export", "landxml_complete_road_design": "complete",
    "landxml_tin_to_geotiff": "terrain", "landxml_surface_boundary": "terrain", "landxml_alignments_to_vector": "alignments",
    "landxml_station_points": "alignments", "landxml_3d_centerlines": "alignments", "landxml_profiles_to_vector": "profiles",
    "landxml_cross_sections": "sections", "landxml_breaklines": "sections", "landxml_feature_lines": "sections",
    "landxml_profile_cut_fill": "cutfill", "landxml_corridor_quantities": "corridor", "landxml_surface_cut_fill": "tin-diff",
    "landxml_grading_quantities": "grading", "landxml_platform_levels": "platforms", "landxml_earthwork_balance": "balance",
    "landxml_grading_revision_comparison": "revisions", "landxml_design_shapes": "design-shapes", "landxml_dem_to_tin": "dem-to-tin", "landxml_basin_storage": "basins", "landxml_grading_edges": "edges",
    "landxml_surface_style": "styles", "landxml_grading_report": "site-report", "landxml_export_scene": "scene",
    "landxml_3d_scene": "scene-viewer", "landxml_cross_section_sheets": "sheets", "landxml_design_report": "report",
    "landxml_design_review": "review-run",
}


def guide_path():
    """The user guide shipped with the plugin (None when it is missing, e.g. a trimmed build)."""
    import os

    path = os.path.join(os.path.dirname(__file__), "docs", "user-guide", "user-guide.html")
    return path if os.path.isfile(path) else None


def guide_url(section=""):
    from qgis.PyQt.QtCore import QUrl

    path = guide_path()
    if path is None:
        return "https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/blob/main/docs/user-guide/user-guide.html"
    url = QUrl.fromLocalFile(path)
    if section:
        url.setFragment(section)
    return url.toString()


def open_guide(section=""):
    """Open the user guide (at ``section``) in the system's browser; returns whether it opened."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

    return QDesktopServices.openUrl(QUrl(guide_url(section)))


def plugin_version():
    """The plugin's version from metadata.txt ('' when it cannot be read)."""
    import configparser
    import os

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf-8")
    return parser.get("general", "version", fallback="")


class ProjectSettingsAlgorithm(QgsProcessingAlgorithm):
    def helpUrl(self):
        return guide_url(GUIDE_SECTIONS.get(self.name(), ""))

    def addParameter(self, definition, createOutput=True):
        from .input_units import BASE_LABELS, unit_basis

        basis = unit_basis(self.name(), definition.name())
        if basis:
            definition.setDescription(definition.description() + f" [{BASE_LABELS[basis]}]")
        if definition.name() in ADVANCED_EVERYWHERE:
            from .compat import mark_advanced_definition

            mark_advanced_definition(definition)
        return super().addParameter(definition, createOutput)

    def createCustomParametersWidget(self, parent=None):
        # Import the GUI only when opening a dialog; scripts and models use normal defaults.
        try:
            from processing.gui.AlgorithmDialog import AlgorithmDialog  # QGIS 3
        except ImportError:  # QGIS 4 replaced the dialog with a dockable widget
            from processing.gui.algorithm_widget import AlgorithmWidget as AlgorithmDialog

        class ProjectSettingsDialog(AlgorithmDialog):
            def __init__(self, algorithm, parent):
                self._project = QgsProject.instance()
                self._project_active = True
                self._saved = {}
                self._key = "processing/" + algorithm.name()
                self._inputs = {
                    parameter.name(): parameter for parameter in algorithm.parameterDefinitions()
                    if not parameter.isDestination() and parameter.name() != "CONFIRM_MATCHING"
                    and parameter.type() in ("file", "string", "number", "enum", "boolean", "crs")
                }
                super().__init__(algorithm, parent=parent)
                raw, _ = self._project.readEntry("LandXML", self._key, "{}")
                try:
                    saved = json.loads(raw)
                except (ValueError, TypeError):
                    saved = {}
                self._saved = {
                    name: value for name, value in (saved.items() if isinstance(saved, dict) else ())
                    if name in self._inputs and isinstance(value, (str, int, float, bool, list, type(None)))
                }
                for name, value in self._saved.items():
                    if self._inputs[name].type() == "file" and isinstance(value, str) and value:
                        self._saved[name] = self._project.pathResolver().readPath(value)
                self.setParameters({})
                from .input_units import connect_unit_labels
                connect_unit_labels(self, algorithm)
                hook = getattr(algorithm, "connect_dialog", None)
                if hook is not None:
                    hook(self)
                self._project.cleared.connect(self._project_cleared)
                self.algorithmFinished.connect(self._remember)

            def _project_cleared(self):
                self._project_active = False
                self._saved = {}

            def setParameters(self, parameters):
                # Processing may supply explicit inputs (e.g. history) after creating the dialog.
                values = {**self._saved, **parameters}
                complete = getattr(self.algorithm(), "dialog_parameters", None)
                super().setParameters(complete(values) if complete is not None else values)

            def _remember(self, successful, _results):
                if not successful or not self._project_active or (self.feedback and self.feedback.isCanceled()):
                    return
                context = self.mainWidget().processing_context
                saved = {}
                for name, parameter in self._inputs.items():
                    wrapper = panel_wrappers(self.mainWidget())[name]
                    value = parameter.valueAsJsonObject(wrapper.parameterValue(), context)
                    if QgsVariantUtils.isNull(value):
                        value = None
                    if parameter.type() == "file" and isinstance(value, str) and value:
                        value = self._project.pathResolver().writePath(value)
                    saved[name] = value
                encoded = json.dumps(saved, allow_nan=False)
                previous, _ = self._project.readEntry("LandXML", self._key, "{}")
                if encoded != previous:
                    self._project.writeEntry("LandXML", self._key, encoded)
                    self._project.setDirty(True)

        return ProjectSettingsDialog(self, parent)
