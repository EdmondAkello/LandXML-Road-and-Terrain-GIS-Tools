"""Read-only LandXML inspection before coordinate assignment or import."""

from __future__ import annotations

import json
import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
)

from .landxml.parser import load_document


class InspectLandXMLAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return InspectLandXMLAlgorithm()

    def name(self):
        return "inspect_landxml"

    def displayName(self):
        return "Inspect LandXML"

    def group(self):
        return "Utilities"

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return (
            "Reports source metadata, units, CRS declaration, entities and raw bounds. "
            "It never swaps axes, assigns a CRS, or converts coordinates."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "OUTPUT",
                "JSON inspection report",
                fileFilter="JSON (*.json)",
                optional=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        path = self.parameterAsFile(parameters, "INPUT", context)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException(
                "Choose an existing LandXML file for inspection."
            )
        try:
            report = load_document(path).inspect()
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        formatted = json.dumps(report, indent=2, ensure_ascii=False)
        for line in formatted.splitlines():
            feedback.pushInfo(line)
        for warning in report["warnings"]:
            feedback.pushWarning(warning)
        output = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        if output:
            try:
                with open(output, "w", encoding="utf-8") as stream:
                    stream.write(formatted + "\n")
            except OSError as exc:
                raise QgsProcessingException(
                    f"Could not write inspection report '{output}': {exc}"
                ) from exc
        return {"OUTPUT": output or "", "REPORT": formatted}
