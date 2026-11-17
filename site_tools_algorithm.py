"""Site-grading tools that work from a design and an existing-ground surface: edge analysis and 3D scene export."""

from __future__ import annotations

import csv
import os

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from .compat import FIELD_DOUBLE, FIELD_STRING
from .core import transform_vertices
from .earthworks_algorithm import _input_param, _resolve, _write_text
from .grading_algorithm import (
    GROUP,
    GROUP_ID,
    HORIZONTAL_UNITS,
    _depth_surface,
    _existing_file,
    _grading_units,
    _vertical_unit_param,
)
from .landxml.catalog import read_catalog
from .landxml.edges import DEFAULT_BANDS, edge_analysis, edges_report_html
from .landxml.quantity_units import LINEAR_UNITS
from .params import number_param
from .processing_common import coordinate_choices
from .processing_widgets import name_param
from .project_settings import ProjectSettingsAlgorithm
from .road_features import _param_coordinates

EDGE_FIELDS = (("edge", FIELD_STRING), ("side", FIELD_STRING), ("height_band", FIELD_STRING), ("length", FIELD_DOUBLE),
               ("height_max", FIELD_DOUBLE), ("height_mean", FIELD_DOUBLE), ("face_area", FIELD_DOUBLE))
STEP_FIELDS = (("step", FIELD_STRING), ("type", FIELD_STRING), ("height", FIELD_DOUBLE), ("top", FIELD_DOUBLE),
               ("bottom", FIELD_DOUBLE), ("length", FIELD_DOUBLE))


def _design_and_existing_params(algorithm):
    _input_param(algorithm)
    algorithm.addParameter(name_param("DESIGN_SURFACE", "Design (finished-level) surface", "surfaces"))
    algorithm.addParameter(QgsProcessingParameterFile(
        "EXISTING_FILE", "Existing-ground LandXML (blank = the same file; read only where it meets the design)",
        extension="xml", optional=True))
    algorithm.addParameter(name_param("EXISTING_SURFACE", "Existing-ground surface", "surfaces",
                                      file_param="EXISTING_FILE", fallback_file_param="INPUT"))


def _design_and_existing(algorithm, p, c, fb, path, doc, method, params):
    """(design name, existing name, existing path, levels xyz (output coordinates), faces, depth) of the site."""
    existing_path, existing_doc = _existing_file(algorithm, p, c, doc, fb)
    catalog = read_catalog(path)
    design = _resolve(catalog.surfaces, algorithm.parameterAsString(p, "DESIGN_SURFACE", c), "design surface")
    existing = _resolve(read_catalog(existing_path or path).surfaces,
                        algorithm.parameterAsString(p, "EXISTING_SURFACE", c), "existing-ground surface")
    if not design or not existing:
        raise QgsProcessingException("Choose the design surface and the existing-ground surface.")
    keep = {}
    _xyz, faces, _meta = _depth_surface(path, design, existing, method, params, fb, existing_path, keep)
    levels = keep["raw"].copy()
    levels[:, 2] = keep["design_z"]
    return design, existing, existing_path, existing_doc, transform_vertices(levels, method=method, **params), faces, keep["raw"][:, 2]


def _bands(text):
    if not text.strip():
        return DEFAULT_BANDS
    try:
        values = tuple(float(part) for part in text.replace(";", ",").split(",") if part.strip())
    except ValueError as exc:
        raise QgsProcessingException(f"Height bands must be numbers separated by commas: {text}") from exc
    if not values or any(v <= 0 for v in values) or list(values) != sorted(set(values)):
        raise QgsProcessingException("Height bands must be positive and increasing.")
    return values


class EdgeAnalysisAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return EdgeAnalysisAlgorithm()

    def name(self):
        return "landxml_grading_edges"

    def displayName(self):
        return "Grading Edge Analysis (Walls and Batters)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Schedules the edges of a graded site from its design and existing-ground surfaces, without a grid: "
            "retained edges (walls, or where a batter is missing) and batters around the earthworks footprint, by "
            "height band, cut or fill, and the walls and batters between platforms inside it.\n\n"
            "The footprint is where design and existing ground differ by more than 0.01 vertical units; its edge is "
            "walked triangle by triangle, so lengths are true plan lengths. Where the edge stands more than the "
            "tie-in tolerance above or below existing ground, it is a retained edge with that height. Where it ties "
            "in, the triangle behind it decides: near-vertical (steeper than 250 %) is a retained edge, a batter "
            "(30 % to 250 %) a batter, anything flatter a natural tie-in; the height is the design level at the edge "
            "minus the nearest flat (platform) level, cut when the ground rises above the platform. Internal steps are "
            "edge-connected near-vertical or batter faces that do not reach the footprint edge, with their height "
            "(level range) and length.\n\n"
            "Outputs are the edge runs as 3D lines (type, side, height band, length, heights, face area), the "
            "internal steps as polygons, an HTML report and a CSV schedule (length per height band, total, maximum "
            "height, length and face area over 1 vertical unit). Heights come from the model: confirm wall types and "
            "heights against the drawings.")

    def initAlgorithm(self, config=None):
        _design_and_existing_params(self)
        self.addParameter(number_param("TIE_TOLERANCE", "Tie-in tolerance (source vertical units)", 0.1, 0.0, 100.0, decimals=3))
        self.addParameter(number_param("FLAT_PCT", "Flat (platform) threshold (% slope)", 2.0, 0.01, 30.0, decimals=2))
        self.addParameter(number_param("MIN_STEP", "Smallest internal step height (source vertical units)", 0.5, 0.0, 1000.0, decimals=2))
        self.addParameter(QgsProcessingParameterString(
            "HEIGHT_BANDS", "Height band limits (source vertical units)", defaultValue="1, 2, 3, 4, 5, 6, 7", optional=True))
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", "Edge runs", type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "STEPS", "Internal steps", type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            "SCHEDULE_CSV", "Edge schedule (CSV)", fileFilter="CSV (*.csv)", optional=True))
        for name, label in (("RETAINED_LENGTH", "Retained edge length"), ("BATTER_LENGTH", "Batter length"),
                            ("MAX_RETAINED_HEIGHT", "Highest retained edge"), ("STEP_COUNT", "Internal steps")):
            self.addOutput(QgsProcessingOutputNumber(name, label))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Edge analysis requires an output CRS in metres, feet or US survey feet.")
        design, existing, _epath, edoc, levels, faces, depth = _design_and_existing(self, p, c, fb, path, doc, method, params)
        units, vertical = _grading_units(self, p, c, [doc] + ([edoc] if edoc else []), fb, horizontal)
        vertical_factor = LINEAR_UNITS[vertical.lower()][0] / LINEAR_UNITS[horizontal.lower()][0]
        bands = _bands(self.parameterAsString(p, "HEIGHT_BANDS", c))
        try:
            result = edge_analysis(levels, faces, depth, vertical_factor,
                                   tie=self.parameterAsDouble(p, "TIE_TOLERANCE", c),
                                   flat=self.parameterAsDouble(p, "FLAT_PCT", c) / 100.0, bands=bands,
                                   min_step=self.parameterAsDouble(p, "MIN_STEP", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if fb.isCanceled():
            raise QgsProcessingException("Cancelled.")

        fields = QgsFields()
        for name, kind in EDGE_FIELDS:
            fields.append(QgsField(name, kind))
        sink, dest = self.parameterAsSink(p, "OUTPUT", c, fields, QgsWkbTypes.Type.LineStringZ, crs)
        if sink is None:
            raise QgsProcessingException("Could not create the edge runs output.")
        for run in result["runs"]:
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromPolyline([QgsPoint(*map(float, row)) for row in run["points"]]))
            feature.setAttributes([run["type"], run["kind"], run["band"], run["length"], run["height_max"],
                                   run["height_mean"], run["face_area"]])
            sink.addFeature(feature)
        steps_dest = ""
        if p.get("STEPS"):
            step_fields = QgsFields()
            for name, kind in STEP_FIELDS:
                step_fields.append(QgsField(name, kind))
            step_sink, steps_dest = self.parameterAsSink(p, "STEPS", c, step_fields, QgsWkbTypes.Type.MultiPolygon, crs)
            if step_sink is not None:
                for step in result["steps"]:
                    feature = QgsFeature(step_fields)
                    parts = [[[QgsPointXY(float(x), float(y)) for x, y in ring[:, :2]] + [QgsPointXY(float(ring[0][0]), float(ring[0][1]))]
                              for ring in [outer, *holes]] for outer, holes in step["polygons"]]
                    feature.setGeometry(QgsGeometry.fromMultiPolygonXY(parts))
                    feature.setAttributes([step["id"], step["type"], step["height"], step["top"], step["bottom"], step["length"]])
                    step_sink.addFeature(feature)

        sched = result["schedule"]
        retained = sum(v["total"] for k, v in sched.items() if k.startswith("retained"))
        batters = sum(v["total"] for k, v in sched.items() if k.startswith("batter"))
        highest = max([v["max"] for k, v in sched.items() if k.startswith("retained")] or [0.0])
        for key, entry in sched.items():
            fb.pushInfo(f"{key.replace('_', ' ')}: {entry['total']:,.1f} {units.length}, up to {entry['max']:.2f} {units.depth}; "
                        f"{entry['over_1']:,.1f} {units.length} over 1 {units.depth}.")
        fb.pushInfo(f"Footprint edge {result['perimeter']:,.1f} {units.length} (tie-ins {result['tie_in_length']:,.1f}); "
                    f"{len(result['steps'])} internal steps.")
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, edges_report_html(result, units, f"design {design} on {existing}", os.path.basename(path)))
        table = self.parameterAsFileOutput(p, "SCHEDULE_CSV", c)
        if table:
            try:
                with open(table, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    labels = list(next(iter(sched.values()))["bands"]) if sched else []
                    writer.writerow(["edge", "side", f"total_{units.length}", f"max_height_{units.depth}",
                                     *[f"{label} {units.depth}" for label in labels],
                                     f"over_1_{units.depth}_{units.length}", f"face_over_1_{units.area}"])
                    for key, entry in sched.items():
                        writer.writerow([*key.split("_"), f"{entry['total']:.3f}", f"{entry['max']:.3f}",
                                         *[f"{value:.3f}" for value in entry["bands"].values()],
                                         f"{entry['over_1']:.3f}", f"{entry['face_over_1']:.3f}"])
            except OSError as exc:
                raise QgsProcessingException(f"Could not write '{table}': {exc}") from exc
        return {"OUTPUT": dest, "STEPS": steps_dest or "", "REPORT": report or "", "SCHEDULE_CSV": table or "",
                "RETAINED_LENGTH": retained, "BATTER_LENGTH": batters, "MAX_RETAINED_HEIGHT": highest,
                "STEP_COUNT": len(result["steps"])}
