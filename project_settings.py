"""Remember successful Processing dialog inputs inside the QGIS project."""

import json

from qgis.core import QgsProcessingAlgorithm, QgsProject, QgsVariantUtils


class ProjectSettingsAlgorithm(QgsProcessingAlgorithm):
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
                self._project.cleared.connect(self._project_cleared)
                self.algorithmFinished.connect(self._remember)

            def _project_cleared(self):
                self._project_active = False
                self._saved = {}

            def setParameters(self, parameters):
                # Processing may supply explicit inputs (e.g. history) after creating the dialog.
                super().setParameters({**self._saved, **parameters})

            def _remember(self, successful, _results):
                if not successful or not self._project_active or (self.feedback and self.feedback.isCanceled()):
                    return
                context = self.mainWidget().processing_context
                saved = {}
                for name, parameter in self._inputs.items():
                    wrapper = self.mainWidget().wrappers[name]
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
