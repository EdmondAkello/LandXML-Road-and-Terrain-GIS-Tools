"""Processing entry point for separate geometry and terrain exports."""

from __future__ import annotations

from .project_settings import ProjectSettingsAlgorithm

from qgis.core import (
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
)

from .landxml.preparation import prepare_landxml
from .params import number_param
from .processing_widgets import name_param

UNIT_VALUES = (None, "meter", "foot", "usSurveyFoot")


class PrepareLandXMLAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return PrepareLandXMLAlgorithm()

    def name(self):
        return "prepare_landxml"

    def displayName(self):
        return "Prepare LandXML from Geometry and Terrains"

    def group(self):
        return "1. Inspect and prepare"

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return (
            "Combines a primary geometry/profile LandXML with optional existing and proposed TIN exports. "
            "For an embedded terrain, leave its file blank and choose its surface. For a separate terrain file, "
            "a single surface is selected automatically; otherwise choose its name. Leaving both role fields "
            "blank supplies no terrain for that role. Complete inputs need no separate terrain files.\n\n"
            "Existing profiles and sections are preserved. Missing surface profiles and terrain cross-sections "
            "are sampled from the original visible triangles, without rasterization. Holes are not bridged. "
            "Profiles retain disconnected runs; disconnected section surfaces are omitted with a warning. "
            "Sections stop at terrain boundaries, which can limit quantities. Distances use source length units.\n\n"
            "Inputs must already share coordinates, horizontal/vertical units and vertical datum. Missing unit "
            "declarations require explicit unit selections. Confirm coordinate/datum agreement when metadata "
            "is missing. No axis swapping, reprojection or unit conversion is performed. The output must differ "
            "from every input. Generated terrain samples do not reconstruct design PVIs or pavement layers."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("INPUT", "Primary geometry/profile LandXML", fileFilter="LandXML (*.xml *.landxml)"))
        self.addParameter(name_param("ALIGNMENT", "Alignment (blank = all supported alignments)", "alignments"))
        for prefix, label in (("EXISTING", "Existing-ground"), ("PROPOSED", "Proposed")):
            self.addParameter(QgsProcessingParameterFile(
                prefix + "_FILE", label + " terrain LandXML (blank = use primary file)",
                fileFilter="LandXML (*.xml *.landxml)", optional=True,
            ))
            self.addParameter(name_param(
                prefix + "_SURFACE", label + " surface (blank = detect in supplied terrain file)", "surfaces",
                file_param=prefix + "_FILE", fallback_file_param="INPUT",
            ))
        for name, label, default in (("PROFILE_INTERVAL", "Profile sampling interval", 5),
                                     ("SECTION_INTERVAL", "Cross-section station interval", 20),
                                     ("LEFT_WIDTH", "Cross-section width left", 50),
                                     ("RIGHT_WIDTH", "Cross-section width right", 50)):
            self.addParameter(number_param(name, label + " (source units)", default, 0.001, 1e9, decimals=3))
        for name, label in (("HORIZONTAL_UNIT", "Shared horizontal units"), ("VERTICAL_UNIT", "Shared vertical units")):
            self.addParameter(QgsProcessingParameterEnum(
                name, label, options=["From input declarations", "Metres", "International feet", "US survey feet"], defaultValue=0,
            ))
        self.addParameter(QgsProcessingParameterBoolean(
            "CONFIRM_MATCHING", "I verified that all inputs share coordinates, units and vertical datum", defaultValue=False,
        ))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Prepared LandXML", fileFilter="LandXML (*.xml)"))
        for name, label in (("ALIGNMENT_COUNT", "Supported alignments"), ("GENERATED_PROFILES", "Generated surface profiles"),
                            ("GENERATED_SECTIONS", "Generated section surfaces"), ("PRESERVED_PROFILES", "Preserved profile records"),
                            ("PRESERVED_SECTIONS", "Preserved section surfaces"), ("COVERAGE_GAPS", "Coverage warnings")):
            self.addOutput(QgsProcessingOutputNumber(name, label))

    def processAlgorithm(self, p, c, fb):
        terrains = {
            role: (self.parameterAsFile(p, prefix + "_FILE", c), self.parameterAsString(p, prefix + "_SURFACE", c).strip())
            for role, prefix in (("existing", "EXISTING"), ("proposed", "PROPOSED"))
        }
        try:
            return prepare_landxml(
                self.parameterAsFile(p, "INPUT", c), self.parameterAsFileOutput(p, "OUTPUT", c), terrains,
                self.parameterAsString(p, "ALIGNMENT", c).strip(),
                self.parameterAsDouble(p, "PROFILE_INTERVAL", c), self.parameterAsDouble(p, "SECTION_INTERVAL", c),
                self.parameterAsDouble(p, "LEFT_WIDTH", c), self.parameterAsDouble(p, "RIGHT_WIDTH", c),
                UNIT_VALUES[self.parameterAsEnum(p, "HORIZONTAL_UNIT", c)], UNIT_VALUES[self.parameterAsEnum(p, "VERTICAL_UNIT", c)],
                self.parameterAsBool(p, "CONFIRM_MATCHING", c), fb,
            )
        except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
            raise QgsProcessingException(str(exc)) from exc
