"""Road Design Report: plan, curvature, gradient, long-section and
superelevation diagrams with element tables (HTML)."""

from __future__ import annotations

import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
)

from .landxml.catalog import match_name, read_catalog
from .landxml.design_report import design_report_html, design_speeds, horizontal_elements
from .landxml.earthworks import compare_profiles, cut_fill_segments
from .landxml.geometry import read_alignments
from .landxml.longsection import default_pair, read_alignment_profiles
from .landxml.parser import load_document
from .landxml.profile import profile_control_points, read_profile_controls
from .landxml.superelevation import lane_slopes, read_superelevation
from .params import number_param
from .processing_widgets import name_param

AXES = ["Swap: file stores northing, easting (Civil 3D)", "As stored: easting, northing"]


class DesignReportAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return DesignReportAlgorithm()

    def name(self):
        return "landxml_design_report"

    def displayName(self):
        return "Road Design Report (Plan, Curvature, Grades, Superelevation)"

    def group(self):
        return "Utilities"

    def groupId(self):
        return "utilities"

    def shortHelpString(self):
        return (
            "Writes a self-contained HTML report per alignment with a schematic plan (element colours, chainage "
            "ticks, north arrow, scale bar), a curvature diagram with radii, a gradient diagram with vertical "
            "curves, the long-section against existing ground with a cut/fill depth diagram, a superelevation "
            "diagram, and tables of horizontal elements, vertical controls (grades, K) and superelevation "
            "transitions, with design speeds where the file records them.\n\n"
            "LandXML does not store the normal crown rate, so it is an input. The plan is schematic and not "
            "georeferenced; choose how the file orders its coordinates (Civil 3D writes northing first). "
            "Print the page to PDF for a paginated document."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT", "LandXML file", behavior=QgsProcessingParameterFile.Behavior.File, fileFilter="LandXML (*.xml *.landxml)"
            )
        )
        self.addParameter(name_param("ALIGNMENT", "Alignment (blank = all)", "alignments"))
        self.addParameter(number_param("NORMAL_CROWN", "Normal crown cross-fall %", 2.5, 0, 20, decimals=2))
        self.addParameter(
            number_param("STRIP", "Split charts into strips of this length (0 = whole alignment)", 5000, 0, 1e7, decimals=1)
        )
        self.addParameter(QgsProcessingParameterEnum("PLAN_AXES", "Plan coordinate order", options=AXES, defaultValue=0))
        self.addParameter(QgsProcessingParameterString("TITLE", "Report title", defaultValue="", optional=True))
        from .landxml.criteria import list_standards

        self.addParameter(
            QgsProcessingParameterEnum(
                "STANDARD",
                "Include a geometric design review against",
                options=["None"] + [name for _id, name, _path in list_standards()],
                defaultValue=0,
            )
        )
        self.addParameter(number_param("DESIGN_SPEED", "Review: design speed km/h (0 = from file)", 0, 0, 200, decimals=0))
        self.addParameter(
            QgsProcessingParameterString("SPEED_RANGES", "Review: design speed by chainage, e.g. 0-5000:60; 5000-:80", defaultValue="", optional=True)
        )
        self.addParameter(QgsProcessingParameterEnum("TERRAIN", "Review: terrain", options=["flat", "rolling", "mountainous", "escarpment"], defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum("EMAX", "Review: e max", options=["4%", "6%", "8%"], defaultValue=1))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Design report (HTML)", fileFilter="HTML (*.html)"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        requested = self.parameterAsString(p, "ALIGNMENT", c).strip()
        alignment_filter = None
        if requested:
            try:
                alignment_filter = match_name(read_catalog(path).alignments, requested, "alignment")
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
        doc = load_document(path)
        try:
            records, unsupported = read_alignments(path, 2.0)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        for message in unsupported[:20]:
            fb.pushWarning(message)
        elements_by_name = {element.attrib.get("name"): element for element in doc.alignments()}
        normal_crown = self.parameterAsDouble(p, "NORMAL_CROWN", c)
        swap = self.parameterAsEnum(p, "PLAN_AXES", c) == 0
        items = []
        for record in records:
            if alignment_filter and record["name"] != alignment_filter:
                continue
            if fb.isCanceled():
                break
            element = elements_by_name.get(record["name"])
            profiles = read_alignment_profiles(element, 2.0) if element is not None else []
            design, ground = default_pair(profiles)
            controls = []
            if design is not None and design.element is not None:
                try:
                    controls = [
                        point
                        for point in profile_control_points(read_profile_controls(design.element))
                        if point["control_type"] == "VPI"
                    ]
                except ValueError as exc:
                    fb.pushWarning(f"{record['name']}: profile controls skipped ({exc}).")
            runs, transitions = [], []
            if design is not None and ground is not None:
                runs = compare_profiles(design, ground)
                _segments, transitions = cut_fill_segments(runs)
            superelevation = read_superelevation(element) if element is not None else []
            items.append(
                {
                    "name": record["name"],
                    "alignment": record,
                    "elements": horizontal_elements(record),
                    "controls": controls,
                    "profile_name": design.name if design else "",
                    "ground_name": ground.name if ground else "",
                    "runs": runs,
                    "transitions": transitions,
                    "speeds": design_speeds(element, doc.root) if element is not None else [],
                    "superelevation": superelevation,
                    "slopes": lane_slopes(superelevation, normal_crown),
                    "swap": swap,
                }
            )
            fb.pushInfo(
                f"{record['name']}: {len(record['segments'])} elements, {len(controls)} vertical controls, "
                f"{len(superelevation)} superelevation transitions."
            )
        if not items:
            raise QgsProcessingException("No alignment with supported geometry was found.")
        standard_index = self.parameterAsEnum(p, "STANDARD", c)
        if standard_index > 0:
            from .landxml.criteria import list_standards, load_criteria
            from .landxml.review import parse_speed_ranges
            from .review_algorithm import collect_reviews

            terrain = ["flat", "rolling", "mountainous", "escarpment"][self.parameterAsEnum(p, "TERRAIN", c)]
            emax = [4.0, 6.0, 8.0][self.parameterAsEnum(p, "EMAX", c)]
            try:
                criteria = load_criteria(list_standards()[standard_index - 1][2])
                ranges = parse_speed_ranges(self.parameterAsString(p, "SPEED_RANGES", c))
                reviews = collect_reviews(
                    path, doc, alignment_filter, criteria, self.parameterAsDouble(p, "DESIGN_SPEED", c), ranges,
                    terrain, "rural", emax, 0, True, "", "", fb,
                )
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
            settings = f"Terrain {terrain} · e max {emax:g}% · design speed " + (
                self.parameterAsString(p, "SPEED_RANGES", c) or (f"{self.parameterAsDouble(p, 'DESIGN_SPEED', c):g} km/h" if self.parameterAsDouble(p, "DESIGN_SPEED", c) else "from file")
            )
            by_name = {r["name"]: r for r in reviews}
            for item in items:
                if item["name"] in by_name:
                    item["review"] = (by_name[item["name"]], settings)
        output = self.parameterAsFileOutput(p, "OUTPUT", c)
        title = self.parameterAsString(p, "TITLE", c).strip()
        with open(output, "w", encoding="utf-8") as stream:
            stream.write(design_report_html(
                    items,
                    doc.horizontal_unit,
                    title,
                    os.path.basename(path),
                    normal_crown,
                    self.parameterAsDouble(p, "STRIP", c),
                ))
        return {"OUTPUT": output, "ALIGNMENT_COUNT": len(items)}
