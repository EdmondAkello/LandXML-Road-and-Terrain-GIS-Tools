"""Earthworks and quantity Processing tools.

* Profile Cut/Fill Analysis — design vs existing-ground profiles along the
  centreline: depths, cut/fill segments, transitions and an optional
  level-section estimate.
* Corridor Section Quantities — Civil 3D sample-line sections: cut/fill
  areas and volumes, corridor material shapes, side-slope (grassing) areas,
  mass haul, with map polygons along the alignment.
* Surface Cut/Fill — TIN-to-TIN difference raster and volumes.
"""

from __future__ import annotations

import math
import os
import re

import numpy as np
from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsRasterShader,
    QgsRendererCategory,
    QgsSingleBandPseudoColorRenderer,
    QgsSymbol,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.PyQt.QtGui import QColor

from .compat import FIELD_DOUBLE, FIELD_INT, FIELD_STRING
from .core import read_tin, surface_difference, transform_vertices, write_geotiff
from .landxml.corridor import (
    guess_surfaces,
    read_corridor_sections,
    roadway_surface_refs,
)
from .landxml.earthworks import (
    compare_profiles,
    corridor_quantities,
    cut_fill_segments,
    exclude_ranges,
    parse_station_ranges,
    regular_rows,
    summarize_segments,
    template_estimate,
)
from .landxml.geometry import read_alignments
from .landxml.longsection import default_pair, find_profile, read_alignment_profiles
from .landxml.parser import load_document
from .landxml.reports import (
    CUT_COLOR,
    FILL_COLOR,
    corridor_report_html,
    profile_report_html,
    station_label,
    surface_report_html,
)
from .landxml.stationing import StationedPolyline
from .params import number_param
from .processing_common import attach_post_processor, coordinate_choices
from .road_features import _param_coordinates

GROUP = "Earthworks and quantities"
GROUP_ID = "earthworks_quantities"
KIND_COLORS = {"cut": CUT_COLOR, "fill": FILL_COLOR, "grade": "#8a94a6", "mixed": "#b07cc6"}


def _input_param(algorithm):
    algorithm.addParameter(
        QgsProcessingParameterFile(
            "INPUT",
            "LandXML file",
            behavior=QgsProcessingParameterFile.Behavior.File,
            fileFilter="LandXML (*.xml *.landxml)",
        )
    )


def _string(algorithm, name, label, default=""):
    algorithm.addParameter(
        QgsProcessingParameterString(name, label, defaultValue=default, optional=True)
    )


def _exclusions(algorithm, parameters, context):
    text = algorithm.parameterAsString(parameters, "EXCLUDE", context)
    try:
        return parse_station_ranges(text)
    except ValueError as exc:
        raise QgsProcessingException(str(exc)) from exc


def _write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
    except OSError as exc:
        raise QgsProcessingException(f"Could not write report '{path}': {exc}") from exc


def _style_output(context, destination, apply, source_path=None):
    attach_post_processor(context, destination, apply, source_path)


def categorized_by_kind(layer, field="kind"):
    categories = []
    for value, color in KIND_COLORS.items():
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        if layer.geometryType() == Qgis.GeometryType.Line:
            symbol.setWidth(1.4)
        elif layer.geometryType() == Qgis.GeometryType.Polygon:
            symbol.setOpacity(0.55)
        categories.append(QgsRendererCategory(value, symbol, value))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, categories))


def cut_fill_raster_style(layer, limit=None):
    if limit is None:
        stats = layer.dataProvider().bandStatistics(1)
        limit = max(abs(stats.minimumValue), abs(stats.maximumValue), 0.01)
    shader = QgsColorRampShader()
    shader.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    shader.setColorRampItemList(
        [
            QgsColorRampShader.ColorRampItem(-limit, QColor(CUT_COLOR), f"Cut {limit:.2f}"),
            QgsColorRampShader.ColorRampItem(0.0, QColor("#f7f7f7"), "0"),
            QgsColorRampShader.ColorRampItem(limit, QColor(FILL_COLOR), f"Fill {limit:.2f}"),
        ]
    )
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader))


class _MapPlacer:
    """Station/offset → output CRS coordinates using the explicit interpretation."""

    def __init__(self, polyline, method, params):
        self.polyline = polyline
        self.method = method
        self.params = params

    def _transform(self, points):
        array = transform_vertices(
            np.asarray([[x, y, 0.0] for x, y in points], dtype=float),
            method=self.method,
            **self.params,
        )
        return [QgsPointXY(float(x), float(y)) for x, y in array[:, :2]]

    def stations_between(self, start, end, step=5.0):
        stations = [start]
        stations += [s for s in self.polyline.stations if start < s < end]
        count = max(1, math.ceil((end - start) / step))
        stations += [start + (end - start) * i / count for i in range(1, count)]
        stations.append(end)
        return sorted(set(stations))

    def line(self, start, end):
        points = [self.polyline.point_at(s) for s in self.stations_between(start, end)]
        points = [p for p in points if p is not None]
        if len(points) < 2:
            return None
        return QgsGeometry.fromPolylineXY(self._transform(points))

    def point(self, station, offset=0.0):
        point = self.polyline.point_at(station, offset)
        return None if point is None else QgsGeometry.fromPointXY(self._transform([point])[0])

    def band(self, start, end, inner, outer):
        """Polygon between two offset edges varying linearly from start to end."""
        stations = self.stations_between(start, end)
        span = (end - start) or 1.0

        def edge(offsets):
            result = []
            for s in stations:
                t = (s - start) / span
                point = self.polyline.point_at(s, offsets[0] + t * (offsets[1] - offsets[0]))
                if point is None:
                    return None
                result.append(point)
            return result

        first, second = edge(inner), edge(outer)
        if first is None or second is None:
            return None
        ring = self._transform(first + list(reversed(second)))
        if len(ring) < 3:
            return None
        ring.append(ring[0])
        geometry = QgsGeometry.fromPolygonXY([ring])
        return geometry if not geometry.isEmpty() else None


def _placers(path, alignment_names, method, params, feedback):
    alignments, unsupported = read_alignments(path, 2.0)
    for message in unsupported[:20]:
        feedback.pushWarning(message)
    result = {}
    for alignment in alignments:
        if alignment["name"] in alignment_names and alignment["name"] not in result:
            polyline = StationedPolyline.from_alignment(alignment)
            if polyline is None:
                feedback.pushWarning(
                    f"Map output skipped for '{alignment['name']}': no start station."
                )
                continue
            result[alignment["name"]] = _MapPlacer(polyline, method, params)
    return result


class ProfileCutFillAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return ProfileCutFillAlgorithm()

    def name(self):
        return "landxml_profile_cut_fill"

    def displayName(self):
        return "Profile Cut/Fill Analysis (Existing vs Design)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Compares a design vertical profile (ProfAlign, e.g. the FRL) with an existing-ground surface "
            "profile (ProfSurf) along each alignment. Outputs a station table (depth = design − ground; "
            "positive fill, negative cut), cut/fill segments with maximum depths, and cut↔fill transition "
            "points. Map outputs follow the alignment with the selected coordinate interpretation and CRS.\n\n"
            "Blank profile names choose the first design profile and the longest existing-ground profile. "
            "Exclude structures (bridges, culverts) with station ranges such as '1200-1450; 2010-2030'. "
            "A positive formation width adds a level-section earthworks and side-slope estimate — a "
            "preliminary flat-ground approximation; use Corridor Section Quantities where sections exist."
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        _string(self, "ALIGNMENT", "Alignment name (blank = all)")
        _string(self, "DESIGN_PROFILE", "Design profile name (blank = first ProfAlign)")
        _string(self, "GROUND_PROFILE", "Existing-ground profile name (blank = longest ProfSurf)")
        self.addParameter(number_param("INTERVAL", "Table station interval", 20, 0.01, 100000, decimals=2))
        self.addParameter(number_param("TOLERANCE", "On-grade tolerance (vertical units)", 0, 0, 100, decimals=3))
        self.addParameter(number_param("HIGH_FILL", "Flag fills deeper than", 3, 0, 1000, decimals=2))
        self.addParameter(number_param("DEEP_CUT", "Flag cuts deeper than", 3, 0, 1000, decimals=2))
        _string(self, "EXCLUDE", "Exclude station ranges, e.g. bridges (1200-1450; 2010-2030)")
        self.addParameter(
            number_param(
                "FORMATION_WIDTH",
                "Level-section estimate: formation width (0 = skip)",
                0,
                0,
                1000,
                decimals=2,
            )
        )
        self.addParameter(number_param("FILL_SLOPE", "Level-section estimate: fill slope 1:n (H per 1 V)", 2, 0, 100, decimals=2))
        self.addParameter(number_param("CUT_SLOPE", "Level-section estimate: cut slope 1:n (H per 1 V)", 1.5, 0, 100, decimals=2))
        _param_coordinates(self, output_optional=True)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", "Cut/fill station table", type=QgsProcessing.SourceType.TypeVector
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "SEGMENTS",
                "Cut/fill segments along alignment (map)",
                type=QgsProcessing.SourceType.TypeVectorLine,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "TRANSITIONS",
                "Cut/fill transition points (map)",
                type=QgsProcessing.SourceType.TypeVectorPoint,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        doc = load_document(path)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        design_name = self.parameterAsString(p, "DESIGN_PROFILE", c).strip()
        ground_name = self.parameterAsString(p, "GROUND_PROFILE", c).strip()
        interval = self.parameterAsDouble(p, "INTERVAL", c)
        tolerance = self.parameterAsDouble(p, "TOLERANCE", c)
        excluded = _exclusions(self, p, c)
        width = self.parameterAsDouble(p, "FORMATION_WIDTH", c)
        fill_slope = self.parameterAsDouble(p, "FILL_SLOPE", c)
        cut_slope = self.parameterAsDouble(p, "CUT_SLOPE", c)
        map_requested = bool(p.get("SEGMENTS") or p.get("TRANSITIONS"))
        if not doc.vertical_unit:
            fb.pushWarning("Vertical unit is undeclared; depths are reported in the source elevation numbers.")

        results = []
        warnings = []
        for alignment in doc.alignments():
            name = alignment.attrib.get("name") or ""
            if alignment_filter and name != alignment_filter:
                continue
            profiles = read_alignment_profiles(alignment, 5.0, warnings)
            design, ground = default_pair(profiles)
            try:
                if design_name:
                    design = find_profile(profiles, design_name)
                if ground_name:
                    ground = find_profile(profiles, ground_name)
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
            if design is None or ground is None:
                fb.pushInfo(f"Alignment '{name}': no design/ground profile pair; skipped.")
                continue
            runs = exclude_ranges(compare_profiles(design, ground), excluded)
            if not runs:
                fb.pushWarning(f"Alignment '{name}': profiles '{design.name}' and '{ground.name}' do not overlap.")
                continue
            segments, transitions = cut_fill_segments(
                runs,
                tolerance,
                self.parameterAsDouble(p, "HIGH_FILL", c),
                self.parameterAsDouble(p, "DEEP_CUT", c),
            )
            item = {
                "alignment": name,
                "design": design.name,
                "ground": ground.name,
                "runs": runs,
                "segments": segments,
                "transitions": transitions,
                "summary": summarize_segments(segments),
                "excluded": "; ".join(f"{a:g}-{b:g}" for a, b in excluded),
            }
            if width > 0:
                item["template"] = dict(
                    template_estimate(runs, width, fill_slope, cut_slope),
                    formation_width=width,
                    fill_slope=fill_slope,
                    cut_slope=cut_slope,
                )
            results.append(item)
            summary = item["summary"]
            fb.pushInfo(
                f"{name}: compared {summary['covered_length']:.1f}; cut {summary['cut_length']:.1f} "
                f"({summary['cut_percent']:.0f}%), fill {summary['fill_length']:.1f} ({summary['fill_percent']:.0f}%), "
                f"max cut {summary['max_cut']:.2f}, max fill {summary['max_fill']:.2f}, "
                f"{summary['transitions']} transitions."
            )
            if "template" in item:
                t = item["template"]
                fb.pushInfo(
                    f"{name} level-section estimate: cut {t['cut_volume']:,.0f}, fill {t['fill_volume']:,.0f}, "
                    f"embankment slopes {t['fill_slope_area']:,.0f}, cut slopes {t['cut_slope_area']:,.0f}."
                )
        for warning in warnings[:20]:
            fb.pushWarning(warning)
        if not results:
            raise QgsProcessingException(
                "No alignment has both a design profile and an existing-ground profile to compare."
            )

        fields = QgsFields()
        for field in ("alignment_name", "design_profile", "ground_profile", "station_label", "kind"):
            fields.append(QgsField(field, FIELD_STRING, len=254))
        for field in ("station", "design_elev", "ground_elev", "depth"):
            fields.append(QgsField(field, FIELD_DOUBLE))
        sink, dest = self.parameterAsSink(
            p, "OUTPUT", c, fields, QgsWkbTypes.Type.NoGeometry, QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException("Could not create the station table.")
        for item in results:
            for row in regular_rows(item["runs"], interval):
                feature = QgsFeature(fields)
                kind = "fill" if row["depth"] > tolerance else "cut" if row["depth"] < -tolerance else "grade"
                feature.setAttributes(
                    [
                        item["alignment"],
                        item["design"],
                        item["ground"],
                        station_label(row["station"], doc.horizontal_unit),
                        kind,
                        row["station"],
                        row["design"],
                        row["ground"],
                        row["depth"],
                    ]
                )
                sink.addFeature(feature)

        outputs = {"OUTPUT": dest, "SEGMENTS": "", "TRANSITIONS": "", "REPORT": ""}
        if map_requested:
            method, crs, _src, params, _doc = coordinate_choices(self, p, c, fb, path)
            placers = _placers(path, {item["alignment"] for item in results}, method, params, fb)
            if p.get("SEGMENTS"):
                seg_fields = QgsFields()
                for field in ("alignment_name", "kind", "severity", "from_label", "to_label", "label_text"):
                    seg_fields.append(QgsField(field, FIELD_STRING, len=254))
                for field in ("sta_start", "sta_end", "length", "max_depth", "max_station", "mean_depth"):
                    seg_fields.append(QgsField(field, FIELD_DOUBLE))
                seg_sink, seg_dest = self.parameterAsSink(
                    p, "SEGMENTS", c, seg_fields, QgsWkbTypes.Type.LineString, crs
                )
                for item in results:
                    placer = placers.get(item["alignment"])
                    if placer is None:
                        continue
                    for segment in item["segments"]:
                        geometry = placer.line(segment["start"], segment["end"])
                        if geometry is None:
                            continue
                        feature = QgsFeature(seg_fields)
                        feature.setGeometry(geometry)
                        feature.setAttributes(
                            [
                                item["alignment"],
                                segment["kind"],
                                segment["severity"],
                                station_label(segment["start"], doc.horizontal_unit),
                                station_label(segment["end"], doc.horizontal_unit),
                                f"{segment['kind']} {segment['max_depth']:.1f}",
                                segment["start"],
                                segment["end"],
                                segment["length"],
                                segment["max_depth"],
                                segment["max_station"],
                                segment["mean_depth"],
                            ]
                        )
                        seg_sink.addFeature(feature)
                outputs["SEGMENTS"] = seg_dest
                _style_output(c, seg_dest, categorized_by_kind, path)
            if p.get("TRANSITIONS"):
                tr_fields = QgsFields()
                for field in ("alignment_name", "transition", "station_label"):
                    tr_fields.append(QgsField(field, FIELD_STRING, len=254))
                for field in ("station", "elevation"):
                    tr_fields.append(QgsField(field, FIELD_DOUBLE))
                tr_sink, tr_dest = self.parameterAsSink(
                    p, "TRANSITIONS", c, tr_fields, QgsWkbTypes.Type.Point, crs
                )
                for item in results:
                    placer = placers.get(item["alignment"])
                    if placer is None:
                        continue
                    for transition in item["transitions"]:
                        geometry = placer.point(transition["station"])
                        if geometry is None:
                            continue
                        feature = QgsFeature(tr_fields)
                        feature.setGeometry(geometry)
                        feature.setAttributes(
                            [
                                item["alignment"],
                                transition["label"],
                                station_label(transition["station"], doc.horizontal_unit),
                                transition["station"],
                                transition["elevation"],
                            ]
                        )
                        tr_sink.addFeature(feature)
                outputs["TRANSITIONS"] = tr_dest
                _style_output(c, tr_dest, None, path)
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(
                report,
                profile_report_html(results, doc.horizontal_unit, doc.vertical_unit, os.path.basename(path)),
            )
            outputs["REPORT"] = report
        outputs["ALIGNMENT_COUNT"] = len(results)
        outputs["CUT_LENGTH"] = sum(item["summary"]["cut_length"] for item in results)
        outputs["FILL_LENGTH"] = sum(item["summary"]["fill_length"] for item in results)
        outputs["TRANSITION_COUNT"] = sum(len(item["transitions"]) for item in results)
        return outputs


def _field_name(prefix, name, used):
    base = prefix + re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()[:40]
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


class CorridorQuantitiesAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return CorridorQuantitiesAlgorithm()

    def name(self):
        return "landxml_corridor_quantities"

    def displayName(self):
        return "Corridor Section Quantities (Cut/Fill, Materials, Side Slopes)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Computes quantities from LandXML corridor cross-sections (Civil 3D sample lines exported with "
            "CrossSectSurf and DesignCrossSectSurf):\n"
            "• cut and fill areas between the existing-ground and design section lines, average-end-area "
            "volumes and a mass-haul ordinate;\n"
            "• corridor material volumes from closed shapes (pavement, base, subbase…);\n"
            "• embankment and cut side-slope surface areas between the formation hinge and daylight, for "
            "grassing/topsoiling, with slopes steeper than 1:n flagged for stabilisation.\n\n"
            "Blank surface names use source evidence: the existing-ground section surface named in a "
            "ProfSurf, and the corridor surface named by Roadway surfaceRefs. 'Use Datum' measures earthworks "
            "to the Datum links (bottom of pavement), keeping the design surface outside them. Segments "
            "flatter than 1:flat ratio (V:H) are not counted as side slopes. Intervals longer than the "
            "maximum spacing are not integrated. Map outputs (side-slope bands and earthworks footprint) "
            "follow the alignment using the selected coordinate interpretation and CRS."
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        _string(self, "ALIGNMENT", "Alignment name (blank = all with cross-sections)")
        _string(self, "GROUND_SURFACE", "Existing-ground section surface (blank = detect)")
        _string(self, "DESIGN_SURFACE", "Design section surface (blank = detect)")
        _string(self, "DESIGN_PROFILE", "Design profile for Datum elevations (blank = first ProfAlign)")
        self.addParameter(
            QgsProcessingParameterBoolean("USE_DATUM", "Measure earthworks to the Datum where exported", defaultValue=True)
        )
        self.addParameter(number_param("FLAT_RATIO", "Ignore slopes flatter than 1:n (H per 1 V)", 10, 1, 1000, decimals=2))
        self.addParameter(number_param("STEEP_RATIO", "Flag slopes steeper than 1:n for stabilisation", 1.5, 0.01, 100, decimals=2))
        self.addParameter(number_param("MAX_SPACING", "Maximum section spacing to integrate (0 = no limit)", 100, 0, 100000, decimals=2))
        self.addParameter(number_param("CUT_FACTOR", "Cut-to-fill factor for mass haul (shrink/bulk)", 1, 0.01, 10, decimals=3))
        _string(self, "EXCLUDE", "Exclude station ranges, e.g. bridges (1200-1450; 2010-2030)")
        _param_coordinates(self, output_optional=True)
        self.addParameter(
            QgsProcessingParameterFeatureSink("OUTPUT", "Section quantities table", type=QgsProcessing.SourceType.TypeVector)
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "VOLUMES", "Interval volumes table", type=QgsProcessing.SourceType.TypeVector, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "SIDE_SLOPES",
                "Side-slope areas for grassing (map polygons)",
                type=QgsProcessing.SourceType.TypeVectorPolygon,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "FOOTPRINT",
                "Earthworks footprint by interval (map polygons)",
                type=QgsProcessing.SourceType.TypeVectorPolygon,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        doc = load_document(path)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        ground_override = self.parameterAsString(p, "GROUND_SURFACE", c).strip()
        design_override = self.parameterAsString(p, "DESIGN_SURFACE", c).strip()
        profile_override = self.parameterAsString(p, "DESIGN_PROFILE", c).strip()
        use_datum = self.parameterAsBoolean(p, "USE_DATUM", c)
        excluded = _exclusions(self, p, c)
        sections, warnings, surface_names, shape_names = read_corridor_sections(doc.root, alignment_filter or None)
        for warning in warnings[:20]:
            fb.pushWarning(warning)
        if not sections:
            raise QgsProcessingException(
                "No station-relative corridor cross-sections (CrossSect) were found. Export sample-line "
                "sections from the design software, or use Profile Cut/Fill Analysis for a centreline estimate."
            )
        fb.pushInfo(f"Section surfaces: {', '.join(surface_names) or 'none'}; shapes: {', '.join(shape_names) or 'none'}.")
        refs = roadway_surface_refs(doc.root)
        by_alignment = {}
        for section in sections:
            by_alignment.setdefault(section.alignment_name, []).append(section)
        results = []
        for alignment in doc.alignments():
            name = alignment.attrib.get("name")
            if name not in by_alignment:
                continue
            group = by_alignment[name]
            profiles = read_alignment_profiles(alignment, 2.0)
            design_profile, _ground = default_pair(profiles)
            if profile_override:
                try:
                    design_profile = find_profile(profiles, profile_override, "design")
                except ValueError as exc:
                    raise QgsProcessingException(str(exc)) from exc
                if design_profile is None:
                    raise QgsProcessingException(f"{name}: design profile '{profile_override}' was not found.")
            ground_guess, design_guess = guess_surfaces(group, [x for x in profiles if x.kind == "surface"], refs)
            ground_surface = ground_override or ground_guess
            design_surface = design_override or design_guess
            for label, value in (("ground", ground_surface), ("design", design_surface)):
                if value and value not in surface_names:
                    raise QgsProcessingException(f"Section surface '{value}' was not found; available: {', '.join(surface_names)}.")
            if not ground_surface or not design_surface:
                fb.pushWarning(
                    f"{name}: could not identify the {'ground' if not ground_surface else 'design'} section surface "
                    f"from source evidence; enter it explicitly (available: {', '.join(surface_names)}). "
                    "Material quantities are still reported."
                )
            fb.pushInfo(f"{name}: ground section '{ground_surface or '–'}', design section '{design_surface or '–'}'.")
            quantities = corridor_quantities(
                group,
                ground_surface,
                design_surface,
                design_profile.elevation_at if design_profile is not None else None,
                use_datum,
                self.parameterAsDouble(p, "FLAT_RATIO", c),
                self.parameterAsDouble(p, "STEEP_RATIO", c),
                self.parameterAsDouble(p, "MAX_SPACING", c),
                self.parameterAsDouble(p, "CUT_FACTOR", c),
                excluded,
            )
            if use_datum and not any(row["datum_used"] for row in quantities["rows"]):
                fb.pushInfo(f"{name}: no usable Datum links; earthworks are measured to the design surface.")
            totals = quantities["totals"]
            fb.pushInfo(
                f"{name}: cut {totals['cut_volume']:,.1f}, fill {totals['fill_volume']:,.1f}, embankment slopes "
                f"{totals['fill_slope_area']:,.1f}, cut slopes {totals['cut_slope_area']:,.1f} over "
                f"{totals['length']:,.1f} ({totals['earthwork_sections']} of {totals['sections']} sections)."
            )
            for material, volume in quantities["materials"].items():
                fb.pushInfo(f"{name}: material '{material}' {volume:,.2f}")
            results.append(
                {
                    "alignment": name,
                    "ground": ground_surface,
                    "design": design_surface,
                    "quantities": quantities,
                    "settings": (
                        f"Datum {'on' if use_datum else 'off'} · flat 1:{self.parameterAsDouble(p, 'FLAT_RATIO', c):g} · "
                        f"steep 1:{self.parameterAsDouble(p, 'STEEP_RATIO', c):g} · max spacing "
                        f"{self.parameterAsDouble(p, 'MAX_SPACING', c):g} · cut factor {self.parameterAsDouble(p, 'CUT_FACTOR', c):g}"
                    ),
                }
            )
        if not results:
            raise QgsProcessingException("No cross-sections belong to the selected alignment.")

        used = set()
        material_fields = {name: _field_name("m_", name, used) for name in shape_names}
        fields = QgsFields()
        for field in ("alignment_name", "station_label", "section_name", "ground_surface", "design_surface"):
            fields.append(QgsField(field, FIELD_STRING, len=254))
        numeric = (
            "station",
            "cut_area",
            "fill_area",
            "centre_depth",
            "left_daylight",
            "right_daylight",
            "left_hinge",
            "right_hinge",
            "fill_slope_len",
            "cut_slope_len",
            "steep_len",
        )
        for field in numeric:
            fields.append(QgsField(field, FIELD_DOUBLE))
        fields.append(QgsField("datum_used", FIELD_INT))
        for field in material_fields.values():
            fields.append(QgsField(field, FIELD_DOUBLE))
        sink, dest = self.parameterAsSink(p, "OUTPUT", c, fields, QgsWkbTypes.Type.NoGeometry, QgsCoordinateReferenceSystem())
        if sink is None:
            raise QgsProcessingException("Could not create the section table.")
        for item in results:
            for row in item["quantities"]["rows"]:
                values = [
                    item["alignment"],
                    station_label(row["station"], doc.horizontal_unit),
                    row["name"],
                    item["ground"],
                    item["design"],
                    row["station"],
                    row.get("cut_area"),
                    row.get("fill_area"),
                    row.get("centre_depth"),
                    row.get("left_daylight"),
                    row.get("right_daylight"),
                    row.get("left_hinge"),
                    row.get("right_hinge"),
                    row.get("fill_slope_length"),
                    row.get("cut_slope_length"),
                    (row.get("steep_fill_length") or 0.0) + (row.get("steep_cut_length") or 0.0)
                    if row["has_earthworks"]
                    else None,
                    int(row["datum_used"]),
                ] + [row["materials"].get(name) for name in material_fields]
                feature = QgsFeature(fields)
                feature.setAttributes(values)
                sink.addFeature(feature)
        outputs = {"OUTPUT": dest, "VOLUMES": "", "SIDE_SLOPES": "", "FOOTPRINT": "", "REPORT": ""}

        if p.get("VOLUMES"):
            volume_fields = QgsFields()
            for field in ("alignment_name", "from_label", "to_label"):
                volume_fields.append(QgsField(field, FIELD_STRING, len=254))
            for field in (
                "sta_start",
                "sta_end",
                "length",
                "cut_volume",
                "fill_volume",
                "fill_slope_area",
                "cut_slope_area",
                "steep_area",
                "mass_ordinate",
            ):
                volume_fields.append(QgsField(field, FIELD_DOUBLE))
            volume_sink, volume_dest = self.parameterAsSink(
                p, "VOLUMES", c, volume_fields, QgsWkbTypes.Type.NoGeometry, QgsCoordinateReferenceSystem()
            )
            for item in results:
                for interval in item["quantities"]["intervals"]:
                    feature = QgsFeature(volume_fields)
                    feature.setAttributes(
                        [
                            item["alignment"],
                            station_label(interval["start"], doc.horizontal_unit),
                            station_label(interval["end"], doc.horizontal_unit),
                            interval["start"],
                            interval["end"],
                            interval["length"],
                            interval["cut_volume"],
                            interval["fill_volume"],
                            interval["fill_slope_area"],
                            interval["cut_slope_area"],
                            interval["steep_fill_area"] + interval["steep_cut_area"],
                            interval["mass_ordinate"],
                        ]
                    )
                    volume_sink.addFeature(feature)
            outputs["VOLUMES"] = volume_dest

        if p.get("SIDE_SLOPES") or p.get("FOOTPRINT"):
            method, crs, _src, params, _doc = coordinate_choices(self, p, c, fb, path)
            placers = _placers(path, {item["alignment"] for item in results}, method, params, fb)
            if p.get("SIDE_SLOPES"):
                slope_fields = QgsFields()
                for field in ("alignment_name", "side", "kind", "from_label", "to_label"):
                    slope_fields.append(QgsField(field, FIELD_STRING, len=254))
                for field in ("sta_start", "sta_end", "fill_slope_area", "cut_slope_area", "steep_area", "plan_area"):
                    slope_fields.append(QgsField(field, FIELD_DOUBLE))
                slope_sink, slope_dest = self.parameterAsSink(
                    p, "SIDE_SLOPES", c, slope_fields, QgsWkbTypes.Type.Polygon, crs
                )
                for item in results:
                    placer = placers.get(item["alignment"])
                    if placer is None:
                        continue
                    for interval in item["quantities"]["intervals"]:
                        for side in ("left", "right"):
                            data = interval["sides"][side]
                            if data["fill_area"] + data["cut_area"] <= 0:
                                continue
                            geometry = placer.band(interval["start"], interval["end"], data["hinge"], data["daylight"])
                            if geometry is None:
                                continue
                            kind = (
                                "fill"
                                if data["cut_area"] <= 0
                                else "cut"
                                if data["fill_area"] <= 0
                                else "mixed"
                            )
                            feature = QgsFeature(slope_fields)
                            feature.setGeometry(geometry)
                            feature.setAttributes(
                                [
                                    item["alignment"],
                                    side,
                                    kind,
                                    station_label(interval["start"], doc.horizontal_unit),
                                    station_label(interval["end"], doc.horizontal_unit),
                                    interval["start"],
                                    interval["end"],
                                    data["fill_area"],
                                    data["cut_area"],
                                    data["steep_area"],
                                    geometry.area(),
                                ]
                            )
                            slope_sink.addFeature(feature)
                outputs["SIDE_SLOPES"] = slope_dest
                _style_output(c, slope_dest, categorized_by_kind, path)
            if p.get("FOOTPRINT"):
                foot_fields = QgsFields()
                for field in ("alignment_name", "kind", "from_label", "to_label"):
                    foot_fields.append(QgsField(field, FIELD_STRING, len=254))
                for field in ("sta_start", "sta_end", "cut_volume", "fill_volume", "net_volume", "plan_area"):
                    foot_fields.append(QgsField(field, FIELD_DOUBLE))
                foot_sink, foot_dest = self.parameterAsSink(
                    p, "FOOTPRINT", c, foot_fields, QgsWkbTypes.Type.Polygon, crs
                )
                for item in results:
                    placer = placers.get(item["alignment"])
                    if placer is None:
                        continue
                    for interval in item["quantities"]["intervals"]:
                        geometry = placer.band(
                            interval["start"],
                            interval["end"],
                            interval["sides"]["left"]["daylight"],
                            interval["sides"]["right"]["daylight"],
                        )
                        if geometry is None:
                            continue
                        cut, fill = interval["cut_volume"], interval["fill_volume"]
                        kind = "fill" if fill >= 3 * cut else "cut" if cut >= 3 * fill else "mixed"
                        feature = QgsFeature(foot_fields)
                        feature.setGeometry(geometry)
                        feature.setAttributes(
                            [
                                item["alignment"],
                                kind,
                                station_label(interval["start"], doc.horizontal_unit),
                                station_label(interval["end"], doc.horizontal_unit),
                                interval["start"],
                                interval["end"],
                                cut,
                                fill,
                                fill - cut,
                                geometry.area(),
                            ]
                        )
                        foot_sink.addFeature(feature)
                outputs["FOOTPRINT"] = foot_dest
                _style_output(c, foot_dest, categorized_by_kind, path)

        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, corridor_report_html(results, doc.horizontal_unit, os.path.basename(path)))
            outputs["REPORT"] = report
        totals = [item["quantities"]["totals"] for item in results]
        outputs.update(
            CUT_VOLUME=sum(t["cut_volume"] for t in totals),
            FILL_VOLUME=sum(t["fill_volume"] for t in totals),
            FILL_SLOPE_AREA=sum(t["fill_slope_area"] for t in totals),
            CUT_SLOPE_AREA=sum(t["cut_slope_area"] for t in totals),
        )
        return outputs


class SurfaceCutFillAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return SurfaceCutFillAlgorithm()

    def name(self):
        return "landxml_surface_cut_fill"

    def displayName(self):
        return "Surface Cut/Fill (TIN Difference)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Grids two LandXML TIN surfaces on a shared raster and writes compare − base "
            "(positive fill, negative cut) as a GeoTIFF, with cut, fill and net volumes. Triangles hidden by a "
            "source surface boundary are excluded. When the LandXML records a Civil 3D SurfVolume for the same "
            "pair, its volumes are listed for comparison. Volumes are in output horizontal units squared × "
            "source vertical units."
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(QgsProcessingParameterString("BASE_SURFACE", "Base (existing) surface name"))
        self.addParameter(QgsProcessingParameterString("COMPARE_SURFACE", "Comparison (design) surface name"))
        self.addParameter(number_param("RESOLUTION", "Grid cell size (output CRS units)", 1, 0.01, 10000, decimals=3))
        self.addParameter(number_param("NODATA", "NoData value", -9999, -3.4e38, 3.4e38, decimals=3))
        _param_coordinates(self)
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", "Cut/fill depth raster"))
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        base_name = self.parameterAsString(p, "BASE_SURFACE", c).strip()
        compare_name = self.parameterAsString(p, "COMPARE_SURFACE", c).strip()
        resolution = self.parameterAsDouble(p, "RESOLUTION", c)
        nodata = self.parameterAsDouble(p, "NODATA", c)
        surfaces = []
        for name in (base_name, compare_name):
            try:
                xyz, faces, meta = read_tin(path, name, cancel=fb.isCanceled)
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
            if meta["invisible_face_count"]:
                fb.pushInfo(f"{name}: skipped {meta['invisible_face_count']:,} invisible triangles.")
            surfaces.append((transform_vertices(xyz, method=method, **params), faces))
        try:
            difference, xmin, ymax, stats = surface_difference(
                surfaces[0],
                surfaces[1],
                resolution,
                nodata,
                progress=lambda value, message: fb.setProgress(value),
                cancel=fb.isCanceled,
            )
        except (ValueError, RuntimeError) as exc:
            raise QgsProcessingException(str(exc)) from exc
        output = self.parameterAsOutputLayer(p, "OUTPUT", c)
        write_geotiff(output, difference, xmin, ymax, resolution, nodata, wkt=crs.toWkt())
        for key, value in stats.items():
            fb.pushInfo(f"{key.replace('_', ' ')}: {value:,.3f}")
        civil3d = next(
            (
                item
                for item in doc.inspect()["surface_volumes"]
                if item.get("base_surface") == base_name and item.get("compare_surface") == compare_name
            ),
            None,
        )
        if civil3d:
            fb.pushInfo(
                f"Civil 3D SurfVolume '{civil3d['name']}': cut {civil3d['cut_volume']:,.3f}, "
                f"fill {civil3d['fill_volume']:,.3f} (source units)."
            )
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(
                report,
                surface_report_html(
                    stats, base_name, compare_name, resolution, QgsUnitTypes.toString(crs.mapUnits()), civil3d, os.path.basename(path)
                ),
            )
        limit = max(stats["max_cut"], stats["max_fill"], 0.01)
        _style_output(c, output, lambda layer: cut_fill_raster_style(layer, limit))
        return dict(stats, OUTPUT=output, REPORT=report or "")
