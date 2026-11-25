"""Setting-Out Data: geometry schedule, key points, chainage coordinates and levels, feature-line offsets."""

from __future__ import annotations

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingOutputNumber,
)

from .landxml import setting_out as so_
from .landxml.corridor_quantities import write_xlsx
from .params import number_param
from .processing_widgets import name_param
from .project_settings import ProjectSettingsAlgorithm

AXES = ["Choose…", "Northing first (as Civil 3D writes)", "Easting first"]


class SettingOutAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return SettingOutAlgorithm()

    def name(self):
        return "landxml_setting_out"

    def displayName(self):
        return "Setting-Out Data (Alignment and Feature Lines)"

    def group(self):
        return "7. Reports and sheets"

    def groupId(self):
        return "reports"

    def shortHelpString(self):
        return ("Setting-out data for an alignment in a LandXML file (a design from Civil 3D or OpenRoads, or a corridor "
                "built here), as an Excel workbook, an HTML report and a CSV of points for a total station:\n"
                "<b>Horizontal geometry</b>: each line, spiral and curve with chainages, length, radius or A, turn, start "
                "and end coordinates and bearings;\n"
                "<b>Key points</b> (BP, TS, SC, CS, ST, PC, PT, PCC, PRC, EP) and the <b>intersection points</b> with "
                "deflection, radius and spirals;\n"
                "<b>Centreline stations</b> at the interval (key points included): easting, northing, whole-circle bearing, "
                "design and ground level, cut or fill;\n"
                "<b>Feature lines</b> (edges, shoulders, daylight…): their points with chainage and offset, and the offset "
                "and level of each at every station.\n\n"
                "Coordinates are the file's, in its CRS and unit (easting before northing in the tables); choose the axis "
                "order the file stores. Bearings are from grid north. Positions are good to the millimetre. Verify against "
                "the drawings and the survey control before setting out.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("INPUT", "LandXML", fileFilter="LandXML (*.xml *.landxml)"))
        self.addParameter(name_param("ALIGNMENT", "Alignment", "alignments"))
        self.addParameter(QgsProcessingParameterEnum("AXIS_ORDER", "The file stores coordinates", options=AXES,
                                                     defaultValue=0))
        self.addParameter(name_param("PROFILE", "Design profile (blank = the first)", "profiles"))
        self.addParameter(number_param("INTERVAL", "Chainage interval", 20.0, 0.0, 10000.0, decimals=2))
        self.addParameter(QgsProcessingParameterBoolean("FEATURE_LINES", "Include the file's feature lines",
                                                        defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum("DECIMALS", "Decimals", options=["2", "3", "4"], defaultValue=1))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT_XLSX", "Setting-out workbook",
                                                                fileFilter="Excel (*.xlsx)"))
        self.addParameter(QgsProcessingParameterFileDestination("REPORT", "Setting-out report", fileFilter="HTML (*.html)",
                                                                optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT_CSV", "Points for a total station (CSV)",
                                                                fileFilter="CSV (*.csv)", optional=True))
        for key, label in (("STATIONS", "Stations"), ("FEATURE_LINE_COUNT", "Feature lines")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def processAlgorithm(self, p, c, fb):
        axis = self.parameterAsEnum(p, "AXIS_ORDER", c)
        if axis == 0:
            raise QgsProcessingException("Choose the axis order of the file (it is never guessed).")
        try:
            so = so_.setting_out(self.parameterAsFile(p, "INPUT", c), self.parameterAsString(p, "ALIGNMENT", c).strip() or None,
                                 axis == 1, self.parameterAsDouble(p, "INTERVAL", c),
                                 self.parameterAsString(p, "PROFILE", c).strip() or None,
                                 feature_lines=self.parameterAsBoolean(p, "FEATURE_LINES", c),
                                 decimals=2 + self.parameterAsEnum(p, "DECIMALS", c))
        except (OSError, ValueError) as error:
            raise QgsProcessingException(str(error)) from error
        result = {"STATIONS": len(so.rows), "FEATURE_LINE_COUNT": len(so.lines)}
        out = self.parameterAsFileOutput(p, "OUTPUT_XLSX", c)
        write_xlsx(out, so_.sheets(so))
        result["OUTPUT_XLSX"] = out
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            with open(report, "w", encoding="utf-8") as stream:
                stream.write(so_.report_html(so))
            result["REPORT"] = report
        csv_path = self.parameterAsFileOutput(p, "OUTPUT_CSV", c)
        if csv_path:
            so_.write_csv(csv_path, so)
            result["OUTPUT_CSV"] = csv_path
        if not so.declared_crs:
            fb.pushWarning("The file does not declare its coordinate system: state it on the setting-out sheets.")
        fb.pushInfo(f"{len(so.elements)} elements, {len(so.keys)} key points, {len(so.rows)} stations, "
                    f"{len(so.lines)} feature lines.")
        return result
