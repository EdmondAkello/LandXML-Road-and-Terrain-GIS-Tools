"""Geometric Design Review: phasing, sight distance, alignment standards and
roadside barrier warrants against a selectable design standard."""

from __future__ import annotations

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from .compat import FIELD_DOUBLE, FIELD_STRING
from .landxml.catalog import match_name, read_catalog
from .landxml.corridor import guess_surfaces, read_corridor_sections, roadway_surface_refs
from .landxml.criteria import list_standards, load_criteria
from .landxml.design_report import design_speeds
from .landxml.geometry import read_alignments
from .landxml.longsection import default_pair, read_alignment_profiles
from .landxml.parser import load_document
from .landxml.profile import profile_control_points, read_profile_controls
from .landxml.reports import _document, station_label
from .landxml.review import civil3d_profile_text, parse_speed_ranges, review_alignment
from .landxml.review_report import recommended_landxml, review_section_html
from .landxml.section_view import build_section_view
from .landxml.superelevation import read_superelevation
from .params import number_param
from .processing_common import attach_post_processor, coordinate_choices
from .processing_widgets import name_param
from .road_features import _param_coordinates

TERRAINS = ["flat", "rolling", "mountainous", "escarpment"]
ROAD_TYPES = ["rural", "urban"]
EMAX = [4.0, 6.0, 8.0]


def standard_options():
    return list_standards()


def collect_reviews(path, doc, alignment_filter, criteria, design_speed, speed_ranges, terrain, road_type, emax, max_grade,
                    use_sections=True, ground_surface="", design_surface="", feedback=None, max_change=1.0):
    """Run the review for every (or one) alignment of a document."""
    records, unsupported = read_alignments(path, 2.0)
    elements = {element.attrib.get("name"): element for element in doc.alignments()}
    sections = []
    surface_names = []
    if use_sections:
        sections, _warnings, surface_names, _shapes = read_corridor_sections(doc.root, alignment_filter or None)
    results = []
    for record in records:
        if alignment_filter and record["name"] != alignment_filter:
            continue
        element = elements.get(record["name"])
        if element is None:
            continue
        profiles = read_alignment_profiles(element, 2.0)
        design, ground = default_pair(profiles)
        controls = []
        if design is not None and design.element is not None:
            controls = [c for c in profile_control_points(read_profile_controls(design.element)) if c["control_type"] == "VPI"]
        recorded = design_speeds(element, doc.root)
        speeds = speed_ranges or recorded
        if feedback is not None and recorded:
            distinct = sorted({v for _s, v in recorded})
            source = "ignored: speed ranges entered" if speed_ranges else ("ignored: one design speed entered" if design_speed else "used")
            feedback.pushInfo(
                f"{record['name']}: file design speeds {', '.join(f'{v:g}' for v in distinct)} km/h at "
                f"{len(recorded)} station(s) ({source})."
            )
        if not speeds and not design_speed:
            raise QgsProcessingException(
                f"{record['name']}: the file records no design speed. Define design speeds in Civil 3D (Alignment Properties → Design Criteria) before exporting, or enter a design speed or speed ranges."
            )
        views = []
        mine = [s for s in sections if s.alignment_name == record["name"]]
        if mine:
            ground_name, design_name = guess_surfaces(mine, [p for p in profiles if p.kind == "surface"], roadway_surface_refs(doc.root))
            ground_name = match_name(surface_names, ground_surface, "ground section surface") if ground_surface else ground_name
            design_name = match_name(surface_names, design_surface, "design section surface") if design_surface else design_name
            if ground_name and design_name:
                views = [
                    build_section_view(s, ground_name, design_name, design.elevation_at(s.station) if design else None)
                    for s in mine
                    if s.surface(ground_name) is not None
                ]
        result = review_alignment(
            record,
            controls,
            criteria,
            speeds,
            None if speed_ranges else (design_speed or None),
            emax,
            road_type,
            terrain,
            max_grade or None,
            read_superelevation(element),
            ground,
            views,
            max_change=max_change,
        )
        result.update(name=record["name"], record=record, element=element, design=design, sections=len(views))
        results.append(result)
        if feedback is not None:
            counts = {s: sum(1 for f in result["findings"] if f["severity"] == s) for s in ("High", "Medium", "Low")}
            feedback.pushInfo(
                f"{record['name']}: {counts['High']} high, {counts['Medium']} medium, {counts['Low']} low findings; "
                f"{len(result['applied'])} profile changes proposed; {len(views)} cross-sections checked."
            )
    return results


class DesignReviewAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return DesignReviewAlgorithm()

    def name(self):
        return "landxml_design_review"

    def displayName(self):
        return "Geometric Design Review (Phasing, Sight Distance, Standards)"

    def group(self):
        return "Design review"

    def groupId(self):
        return "design_review"

    def shortHelpString(self):
        return (
            "Checks the alignment, profile, superelevation and corridor sections against a selectable design standard "
            "(Kenya RDM 1.3 2025, or AASHTO 2018 which underlies Civil 3D's default design criteria, or your own criteria "
            "file) and recommends corrections to apply in the design software:\n"
            "• horizontal/vertical phasing (hidden curves beyond crests, overlapping curve ends, broken-back curves, "
            "crest–sag common ends, rolling grade lines) with computed PVI stations, elevations and curve lengths;\n"
            "• crest and sag K values, minimum curve lengths, stopping sight distance along the profile;\n"
            "• grades (maximum, length of grade, minimum for drainage in cut), combined grade;\n"
            "• radius, transitions, tangents, curve lengths, superelevation rate and run-off;\n"
            "• with corridor sections: embankment barrier warrants with barrier ranges, and cut slopes that block "
            "sight lines on curves;\n"
            "• design speed changes: steps larger than the standard allows (with intermediate sections to add), short "
            "transition and oscillating speed sections, and sharp curves just past a speed reduction.\n\n"
            "Design speed varies along the road as recorded in the file: define the design speeds in Civil 3D "
            "(Alignment Properties → Design Criteria, speed stations) before exporting the LandXML. Each element is "
            "checked at the highest speed over its length. Leave the design speed at 0 to use the file; a single speed "
            "or ranges such as '0-5000:60; 5000-:80' override it. Outputs: findings table, optional map points, HTML report, and "
            "the recommended profile as a Civil 3D 'Create Profile from File' text file and as LandXML."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT", "LandXML file", behavior=QgsProcessingParameterFile.Behavior.File, fileFilter="LandXML (*.xml *.landxml)"
            )
        )
        self.addParameter(name_param("ALIGNMENT", "Alignment (blank = all)", "alignments"))
        standards = standard_options()
        self.addParameter(
            QgsProcessingParameterEnum("STANDARD", "Design standard", options=[name for _id, name, _p in standards], defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterFile(
                "CRITERIA_FILE", "Custom criteria file (overrides the standard)", behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="JSON (*.json)", optional=True,
            )
        )
        self.addParameter(number_param("DESIGN_SPEED", "Design speed km/h (0 = from file)", 0, 0, 200, decimals=0))
        self.addParameter(
            QgsProcessingParameterString("SPEED_RANGES", "Design speed by chainage, e.g. 0-5000:60; 5000-:80", defaultValue="", optional=True)
        )
        self.addParameter(QgsProcessingParameterEnum("TERRAIN", "Terrain", options=TERRAINS, defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum("ROAD_TYPE", "Road type (grade limits)", options=ROAD_TYPES, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("EMAX", "Maximum superelevation e max", options=["4%", "6%", "8%"], defaultValue=1))
        self.addParameter(number_param("MAX_GRADE", "Maximum grade % override (0 = standard)", 0, 0, 30, decimals=2))
        self.addParameter(number_param("MAX_CHANGE", "Apply profile fixes changing levels by at most (m)", 1.0, 0, 100, decimals=2))
        self.addParameter(QgsProcessingParameterBoolean("USE_SECTIONS", "Use corridor cross-sections (barriers, sight on curves)", defaultValue=True))
        self.addParameter(name_param("GROUND_SURFACE", "Existing-ground section surface (blank = detect)", "section_surfaces"))
        self.addParameter(name_param("DESIGN_SURFACE", "Design section surface (blank = detect)", "section_surfaces"))
        _param_coordinates(self, output_optional=True)
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Findings table", type=QgsProcessing.SourceType.TypeVector))
        self.addParameter(
            QgsProcessingParameterFeatureSink("MAP_OUTPUT", "Findings on the map (points)", type=QgsProcessing.SourceType.TypeVectorPoint, optional=True)
        )
        self.addParameter(QgsProcessingParameterFileDestination("REPORT", "HTML review report", fileFilter="HTML (*.html)", optional=True))
        self.addParameter(
            QgsProcessingParameterFileDestination("PROFILE_TXT", "Recommended profile for Civil 3D (station elevation length)", fileFilter="Text (*.txt)", optional=True)
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("PROFILE_LANDXML", "Recommended profile as LandXML", fileFilter="LandXML (*.xml)", optional=True)
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        custom = self.parameterAsFile(p, "CRITERIA_FILE", c)
        standards = standard_options()
        try:
            criteria = load_criteria(custom) if custom else load_criteria(standards[self.parameterAsEnum(p, "STANDARD", c)][2])
            speed_ranges = parse_speed_ranges(self.parameterAsString(p, "SPEED_RANGES", c))
            requested = self.parameterAsString(p, "ALIGNMENT", c).strip()
            alignment = match_name(read_catalog(path).alignments, requested, "alignment") if requested else ""
        except (ValueError, IndexError) as exc:
            raise QgsProcessingException(str(exc)) from exc
        doc = load_document(path)
        if (doc.horizontal_unit or "meter").lower() not in ("meter", "metre"):
            raise QgsProcessingException("Design review criteria are metric; this file declares " f"'{doc.horizontal_unit}'.")
        terrain = TERRAINS[self.parameterAsEnum(p, "TERRAIN", c)]
        road_type = ROAD_TYPES[self.parameterAsEnum(p, "ROAD_TYPE", c)]
        emax = EMAX[self.parameterAsEnum(p, "EMAX", c)]
        design_speed = self.parameterAsDouble(p, "DESIGN_SPEED", c)
        max_grade = self.parameterAsDouble(p, "MAX_GRADE", c)
        if not criteria.max_grade(road_type, terrain) and not max_grade:
            fb.pushWarning(f"{criteria.name} has no maximum-grade table; enter a maximum grade to check grades.")
        fb.pushInfo(f"Standard: {criteria.name}. Terrain {terrain}, {road_type}, e max {emax:g}%.")
        try:
            results = collect_reviews(
                path, doc, alignment, criteria, design_speed, speed_ranges, terrain, road_type, emax, max_grade,
                self.parameterAsBoolean(p, "USE_SECTIONS", c),
                self.parameterAsString(p, "GROUND_SURFACE", c).strip(),
                self.parameterAsString(p, "DESIGN_SURFACE", c).strip(),
                fb,
                self.parameterAsDouble(p, "MAX_CHANGE", c),
            )
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if not results:
            raise QgsProcessingException("No alignment with supported geometry was found.")
        fields = QgsFields()
        for name in ("alignment_name", "severity", "category", "code", "from_label", "to_label", "side", "issue", "detail",
                     "recommendation", "reference"):
            fields.append(QgsField(name, FIELD_STRING, len=1000))
        for name in ("sta_start", "sta_end", "value", "required"):
            fields.append(QgsField(name, FIELD_DOUBLE))
        sink, dest = self.parameterAsSink(p, "OUTPUT", c, fields, QgsWkbTypes.Type.NoGeometry, QgsCoordinateReferenceSystem())

        def attributes(name, f):
            return [name, f["severity"], f["category"], f["code"], station_label(f["start"], doc.horizontal_unit),
                    station_label(f["end"], doc.horizontal_unit), f["side"], f["title"], f["detail"], f["recommendation"],
                    f["reference"], f["start"], f["end"],
                    f["value"] if isinstance(f["value"], (int, float)) else None,
                    f["required"] if isinstance(f["required"], (int, float)) else None]

        for result in results:
            for f in result["findings"]:
                feature = QgsFeature(fields)
                feature.setAttributes(attributes(result["name"], f))
                sink.addFeature(feature)
        outputs = {"OUTPUT": dest, "MAP_OUTPUT": "", "REPORT": "", "PROFILE_TXT": "", "PROFILE_LANDXML": ""}
        if p.get("MAP_OUTPUT"):
            from .earthworks_algorithm import _placers

            method, crs, _src, params, _doc = coordinate_choices(self, p, c, fb, path)
            placers = _placers(path, {r["name"] for r in results}, method, params, fb)
            map_sink, map_dest = self.parameterAsSink(p, "MAP_OUTPUT", c, fields, QgsWkbTypes.Type.Point, crs)
            for result in results:
                placer = placers.get(result["name"])
                if placer is None:
                    continue
                for f in result["findings"]:
                    geometry = placer.point((f["start"] + f["end"]) / 2.0)
                    if geometry is None:
                        continue
                    feature = QgsFeature(fields)
                    feature.setGeometry(geometry)
                    feature.setAttributes(attributes(result["name"], f))
                    map_sink.addFeature(feature)
            outputs["MAP_OUTPUT"] = map_dest
            attach_post_processor(c, map_dest, None, path)
        settings = (
            f"Terrain {terrain} · {road_type} · e max {emax:g}% · design speed "
            + (self.parameterAsString(p, "SPEED_RANGES", c) or (f"{design_speed:g} km/h" if design_speed else "from file"))
        )
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            body = "".join(
                f"<section class='alignment'><h2>{r['name']}</h2>{review_section_html(r['name'], r, doc.horizontal_unit, settings)}</section>"
                for r in results
            )
            with open(report, "w", encoding="utf-8") as stream:
                stream.write(_document("Geometric design review", f"<p class='meta'>Source: {os.path.basename(path)}</p>" + body))
            outputs["REPORT"] = report
        reviewed = [r for r in results if r["recommended"] is not None]
        text_path = self.parameterAsFileOutput(p, "PROFILE_TXT", c)
        if text_path and reviewed:
            target = reviewed[0]
            if len(reviewed) > 1:
                fb.pushWarning(f"Recommended profile text written for '{target['name']}' only; choose an alignment for others.")
            with open(text_path, "w", encoding="utf-8") as stream:
                stream.write(civil3d_profile_text(target["recommended"]))
            outputs["PROFILE_TXT"] = text_path
        xml_path = self.parameterAsFileOutput(p, "PROFILE_LANDXML", c)
        if xml_path and reviewed:
            target = reviewed[0]
            name = f"{target['design'].name if target['design'] else 'Profile'} (review {criteria.id})"
            with open(xml_path, "w", encoding="utf-8") as stream:
                stream.write(recommended_landxml(target["element"], target["recommended"], name, doc.horizontal_unit))
            outputs["PROFILE_LANDXML"] = xml_path
        outputs.update(
            HIGH=sum(1 for r in results for f in r["findings"] if f["severity"] == "High"),
            MEDIUM=sum(1 for r in results for f in r["findings"] if f["severity"] == "Medium"),
            LOW=sum(1 for r in results for f in r["findings"] if f["severity"] == "Low"),
            PROFILE_CHANGES=sum(len(r["applied"]) for r in results),
        )
        return outputs
