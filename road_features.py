from bisect import bisect_left
import math
import os
from .landxml.common import descendants, first_descendant
from .landxml.parser import load_document
from .landxml.features import read_line_features
from .landxml.profile import (
    ProfileMapPlacement,
    read_profile_controls,
    profile_control_points,
)
from .landxml.sections import read_cross_sections
from .processing_common import BoundsTracker, coordinate_choices
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingParameterFile,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsPalLayerSettings,
    QgsProcessingLayerPostProcessorInterface,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor
from .compat import FIELD_STRING, FIELD_DOUBLE, FIELD_INT
from .core import transform_vertices
from .landxml.geometry import read_alignments, regular_station_distances
from .core import read_tin
from .landxml.profile import read_vertical_profile
from .params import number_param

METHODS = [
    "Use stored coordinates",
    "Swap X / Y",
    "Apply X / Y offset",
    "Swap X / Y + offset",
    "2-D Helmert (similarity)",
    "Reproject from source CRS",
]


def _xy(text):
    v = [float(x) for x in (text or "").split()[:2]]
    if len(v) < 2:
        raise ValueError("Expected X Y coordinates")
    return v[0], v[1]


def _param_common(a):
    a.addParameter(
        QgsProcessingParameterString(
            "ALIGNMENT", "Alignment name (blank = all)", defaultValue="", optional=True
        )
    )
    _param_coordinates(a)


def _param_coordinates(a, output_optional=False):
    a.addParameter(
        QgsProcessingParameterEnum(
            "METHOD", "Coordinate interpretation", options=METHODS, defaultValue=0
        )
    )
    a.addParameter(number_param("DX", "Delta X / Easting", 0, -1e9, 1e9, decimals=4))
    a.addParameter(number_param("DY", "Delta Y / Northing", 0, -1e9, 1e9, decimals=4))
    a.addParameter(
        number_param("ROTATION", "Rotation (degrees)", 0, -360, 360, decimals=8)
    )
    a.addParameter(number_param("SCALE", "Scale factor", 1, 1e-8, 1000, decimals=10))
    a.addParameter(
        QgsProcessingParameterCrs(
            "SOURCE_CRS",
            "Source CRS (for reprojection)",
            defaultValue=None,
            optional=True,
        )
    )
    a.addParameter(
        QgsProcessingParameterCrs(
            "OUTPUT_CRS",
            "Output CRS (required for map overlay)"
            if output_optional
            else "Output CRS (result layer / raster)",
            defaultValue=None,
            optional=output_optional,
        )
    )


def _method_map(i):
    return ["stored", "swap", "offset", "swap_offset", "helmert", "reproject"][i]


def _transform_params(parameters, context, algorithm, feedback, path):
    method, out, source, params, _document = coordinate_choices(
        algorithm, parameters, context, feedback, path
    )
    return method, out, source, params


def _sink(a, parameters, context, fields, wkb):
    return a.parameterAsSink(
        parameters,
        "OUTPUT",
        context,
        fields,
        wkb,
        a.parameterAsCrs(parameters, "OUTPUT_CRS", context),
    )


def _line_chain(points):
    return QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in points])


def _alignment_parts(path, segment_length=5.0, alignment_filter=""):
    alignments, unsupported = read_alignments(path, segment_length)
    result = []
    for alignment in alignments:
        if alignment_filter and alignment["name"] != alignment_filter:
            continue
        result.append(
            (
                alignment["name"],
                alignment["desc"],
                alignment["sta_start"],
                alignment["sta_end"],
                [segment["points"] for segment in alignment["segments"]],
                unsupported,
            )
        )
    return result


def _profile_station_label(station):
    rounded = round(station, 2)
    hundreds = math.floor(rounded / 100)
    return f"{hundreds}+{rounded - 100 * hundreds:05.2f}"


def _profile_control_label(point):
    lines = [
        f"{point['control_type']} {_profile_station_label(point['station'])}",
        f"Elev {point['elevation']:.2f}",
    ]
    if point["grade_in"] is not None:
        lines.append(f"G1 {point['grade_in'] * 100:+.2f}%")
    if point["grade_out"] is not None:
        lines.append(f"G2 {point['grade_out'] * 100:+.2f}%")
    if point["control_type"] == "VPI" and point["k_value"] is not None:
        lines.append(f"K {point['k_value']:.2f}")
    return "\n".join(lines)


class _ProfileLabelPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, placement):
        super().__init__()
        self.placement = placement

    def postProcessLayer(self, layer, context, feedback):
        settings = QgsPalLayerSettings()
        settings.fieldName = "label_text"
        settings.placement = self.placement
        text_format = QgsTextFormat()
        text_format.setSize(9)
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor("white"))
        text_format.setBuffer(buffer)
        settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()
        processors = getattr(context, "_landxml_profile_label_processors", None)
        if processors is not None and self in processors:
            processors.remove(self)


def _label_profile_output(context, destination, name, placement):
    if not destination:
        return
    if not context.willLoadLayerOnCompletion(destination):
        context.addLayerToLoadOnCompletion(
            destination, QgsProcessingContext.LayerDetails(name, context.project())
        )
    details = context.layerToLoadOnCompletionDetails(destination)
    processor = _ProfileLabelPostProcessor(placement)
    processors = getattr(context, "_landxml_profile_label_processors", [])
    processors.append(processor)
    context._landxml_profile_label_processors = processors
    details.setPostProcessor(processor)


class ProfileAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return ProfileAlgorithm()

    def name(self):
        return "landxml_profiles_to_vector"

    def displayName(self):
        return "Extract LandXML Profiles"

    def group(self):
        return "Road design extraction"

    def groupId(self):
        return "road_design_extraction"

    def shortHelpString(self):
        return "Extract station/elevation profile graphs and labeled VPC/VPI/VPT controls. Optional map layers draw a schematic profile beside its matching alignment using an explicit coordinate interpretation and output CRS."

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
            QgsProcessingParameterString(
                "ALIGNMENT",
                "Alignment name (blank = all)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                "PROFILE", "Profile name (blank = all)", defaultValue="", optional=True
            )
        )
        self.addParameter(
            number_param(
                "POINT_INTERVAL",
                "Profile sampling interval (source station units)",
                5,
                0.01,
                10000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "VERTICAL_EXAGGERATION",
                "Vertical exaggeration (graph Y and map profile offset)",
                1,
                0.01,
                1000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "MAP_OFFSET",
                "Map profile offset left of alignment (source horizontal units)",
                100,
                -100000,
                100000,
                decimals=2,
            )
        )
        _param_coordinates(self, output_optional=True)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT",
                "Profile graph lines (station/elevation, no map CRS)",
                type=QgsProcessing.SourceType.TypeVectorLine,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "CONTROL_POINTS",
                "Profile control points (station/elevation, no map CRS)",
                type=QgsProcessing.SourceType.TypeVectorPoint,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "MAP_OUTPUT",
                "Map profile lines (schematic, beside alignment)",
                type=QgsProcessing.SourceType.TypeVectorLine,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "MAP_CONTROL_POINTS",
                "Map profile control points (schematic, beside alignment)",
                type=QgsProcessing.SourceType.TypeVectorPoint,
                optional=True,
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        profile_filter = self.parameterAsString(p, "PROFILE", c).strip()
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        doc = load_document(path)
        exaggeration = self.parameterAsDouble(p, "VERTICAL_EXAGGERATION", c)
        map_requested = bool(p.get("MAP_OUTPUT") or p.get("MAP_CONTROL_POINTS"))
        placements = {}
        map_crs = None
        if map_requested:
            method, map_crs, _source, transform_params, _document = coordinate_choices(
                self, p, c, fb, path
            )
            alignments, unsupported = read_alignments(path, 5.0)
            for message in unsupported:
                fb.pushWarning(message)
            for alignment in alignments:
                name = alignment["name"]
                if name in placements:
                    raise QgsProcessingException(
                        f"Alignment name '{name}' is repeated; cannot place its profile unambiguously."
                    )
                if not alignment["sta_start"]:
                    fb.pushWarning(
                        f"Map profile skipped for '{name}': no alignment start station."
                    )
                    continue
                source_xy = alignment["points"]
                transformed = transform_vertices(
                    [[x, y, 0.0] for x, y in source_xy],
                    method=method,
                    **transform_params,
                )
                placements[name] = (
                    source_xy,
                    transformed[:, :2],
                    float(alignment["sta_start"]),
                )
            fb.pushInfo(
                "Map profile layers are schematic: station follows the alignment and elevation "
                "changes its left-side offset. They are not surveyed road geometry."
            )
        fb.pushInfo(
            f"Profile graph X is station; plot Y is source elevation × {exaggeration:g}. "
            "Elevation attributes and labels retain source values; no map CRS is assigned."
        )
        if not doc.horizontal_unit or not doc.vertical_unit:
            fb.pushWarning(
                "Horizontal or vertical unit is undeclared. Grade percentages and K values assume "
                "elevation and station use compatible linear units."
            )
        elif doc.horizontal_unit.lower() != doc.vertical_unit.lower():
            fb.pushWarning(
                "Horizontal and vertical units differ. Grade percentages and K values "
                "use the source numbers without unit conversion; verify before design use."
            )
        fields = QgsFields()
        for n in (
            "alignment_name",
            "profile_name",
            "source_vendor",
            "source_file",
            "source_name",
            "source_description",
            "label_text",
        ):
            fields.append(QgsField(n, FIELD_STRING, len=254))
        for n in ("station_start", "station_end", "vertical_exaggeration"):
            fields.append(QgsField(n, FIELD_DOUBLE))
        sink, dest = self.parameterAsSink(
            p,
            "OUTPUT",
            c,
            fields,
            QgsWkbTypes.Type.LineString,
            QgsCoordinateReferenceSystem(),
        )
        if sink is None:
            raise QgsProcessingException("Could not create profile graph layer.")
        control_fields = QgsFields()
        for name in (
            "alignment_name",
            "profile_name",
            "control_type",
            "source_control_type",
            "curve_type",
            "source_vendor",
            "source_file",
            "source_name",
            "source_description",
            "label_text",
        ):
            control_fields.append(QgsField(name, FIELD_STRING, len=254))
        for name in (
            "station",
            "elevation",
            "plot_elevation",
            "grade_in",
            "grade_out",
            "curve_length",
            "k_value",
        ):
            control_fields.append(QgsField(name, FIELD_DOUBLE))
        control_sink, control_dest = (None, None)
        if p.get("CONTROL_POINTS"):
            control_sink, control_dest = self.parameterAsSink(
                p,
                "CONTROL_POINTS",
                c,
                control_fields,
                QgsWkbTypes.Type.Point,
                QgsCoordinateReferenceSystem(),
            )
            if control_sink is None:
                raise QgsProcessingException(
                    "Could not create profile control point layer."
                )
        map_sink, map_dest = (None, None)
        if p.get("MAP_OUTPUT"):
            map_sink, map_dest = self.parameterAsSink(
                p, "MAP_OUTPUT", c, fields, QgsWkbTypes.Type.LineString, map_crs
            )
            if map_sink is None:
                raise QgsProcessingException("Could not create map profile line layer.")
        map_control_sink, map_control_dest = (None, None)
        if p.get("MAP_CONTROL_POINTS"):
            map_control_sink, map_control_dest = self.parameterAsSink(
                p,
                "MAP_CONTROL_POINTS",
                c,
                control_fields,
                QgsWkbTypes.Type.Point,
                map_crs,
            )
            if map_control_sink is None:
                raise QgsProcessingException(
                    "Could not create map profile control point layer."
                )
        n = 0
        interval = max(self.parameterAsDouble(p, "POINT_INTERVAL", c), 0.01)
        for alignment_name, prof in doc.profiles():
            alignment_name = alignment_name or ""
            if alignment_filter and alignment_name != alignment_filter:
                continue
            if fb.isCanceled():
                raise QgsProcessingException("Profile extraction cancelled.")
            if profile_filter and prof.attrib.get("name", "") != profile_filter:
                continue
            # ProfAlign is a flat PVI/ParaCurve/CircCurve sequence, not a
            # ProfileGeom/Line/Curve wrapper -- see landxml.profile.
            pts = read_vertical_profile(prof, interval)
            if len(pts) < 2:
                continue
            sta, end = pts[0][0], pts[-1][0]
            # Represent profile as station,elevation pairs in GIS coordinate space; horizontal X=station, Y=elevation.
            geom = QgsGeometry.fromPolylineXY(
                [QgsPointXY(x, y * exaggeration) for x, y in pts]
            )
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes(
                [
                    alignment_name,
                    prof.attrib.get("name"),
                    doc.vendor,
                    os.path.basename(path),
                    prof.attrib.get("name"),
                    prof.attrib.get("desc"),
                    prof.attrib.get("name") or alignment_name or "Profile",
                    sta,
                    end,
                    exaggeration,
                ]
            )
            sink.addFeature(feat)
            placement = None
            if map_requested:
                alignment = placements.get(alignment_name)
                if alignment is None:
                    fb.pushWarning(
                        f"Map profile '{prof.attrib.get('name', '')}' skipped: "
                        f"no usable horizontal alignment named '{alignment_name}'."
                    )
                else:
                    try:
                        placement = ProfileMapPlacement(
                            *alignment,
                            elevation_datum=pts[0][1],
                            offset=self.parameterAsDouble(p, "MAP_OFFSET", c),
                            exaggeration=exaggeration,
                        )
                    except ValueError as exc:
                        fb.pushWarning(f"Map profile '{alignment_name}' skipped: {exc}")
            if map_sink is not None and placement is not None:
                mapped = [
                    placement.point(station, elev)
                    for station, elev in placement.clipped_samples(pts, interval)
                ]
                if len(mapped) >= 2:
                    map_feature = QgsFeature(fields)
                    map_feature.setGeometry(_line_chain(mapped))
                    map_feature.setAttributes(feat.attributes())
                    map_sink.addFeature(map_feature)
                else:
                    fb.pushWarning(
                        f"Map profile '{alignment_name}' skipped: profile stations do not "
                        "overlap the horizontal alignment."
                    )
            if control_sink is not None or map_control_sink is not None:
                for control in profile_control_points(read_profile_controls(prof)):
                    attrs = [
                        alignment_name,
                        prof.attrib.get("name"),
                        control["control_type"],
                        control["source_control_type"],
                        control["curve_type"],
                        doc.vendor,
                        os.path.basename(path),
                        prof.attrib.get("name"),
                        prof.attrib.get("desc"),
                        _profile_control_label(control),
                        control["station"],
                        control["elevation"],
                        control["elevation"] * exaggeration,
                        control["grade_in"],
                        control["grade_out"],
                        control["curve_length"],
                        control["k_value"],
                    ]
                    if control_sink is not None:
                        point = QgsFeature(control_fields)
                        point.setGeometry(
                            QgsGeometry.fromPointXY(
                                QgsPointXY(
                                    control["station"],
                                    control["elevation"] * exaggeration,
                                )
                            )
                        )
                        point.setAttributes(attrs)
                        control_sink.addFeature(point)
                    if map_control_sink is not None and placement is not None:
                        mapped = placement.point(control["station"], control["elevation"])
                        if mapped is not None:
                            map_point = QgsFeature(control_fields)
                            map_point.setGeometry(
                                QgsGeometry.fromPointXY(QgsPointXY(*mapped))
                            )
                            map_point.setAttributes(attrs)
                            map_control_sink.addFeature(map_point)
            n += 1
        _label_profile_output(
            c, dest, "Profile graph", QgsPalLayerSettings.Placement.Line
        )
        _label_profile_output(
            c,
            control_dest,
            "Profile control points",
            QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint,
        )
        _label_profile_output(
            c, map_dest, "Map profile (schematic)", QgsPalLayerSettings.Placement.Line
        )
        _label_profile_output(
            c,
            map_control_dest,
            "Map profile controls (schematic)",
            QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint,
        )
        return {
            "OUTPUT": dest,
            "CONTROL_POINTS": control_dest or "",
            "MAP_OUTPUT": map_dest or "",
            "MAP_CONTROL_POINTS": map_control_dest or "",
            "PROFILE_COUNT": n,
        }


class Centerline3DAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return Centerline3DAlgorithm()

    def name(self):
        return "landxml_3d_centerlines"

    def displayName(self):
        return "Create 3D Road Centerlines"

    def group(self):
        return "Road design extraction"

    def groupId(self):
        return "road_design_extraction"

    def shortHelpString(self):
        return "Creates 3D centerlines where a matching LandXML vertical profile covers the horizontal alignment station range. Partial profile coverage creates a partial centerline."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common(self)
        self.addParameter(
            number_param(
                "SEGMENT",
                "Maximum geometry segment length (source horizontal units)",
                5,
                0.01,
                10000,
                decimals=2,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", "3D centerlines", type=QgsProcessing.SourceType.TypeVectorLine
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        m, out, src, tp = _transform_params(p, c, self, fb, path)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        seg = self.parameterAsDouble(p, "SEGMENT", c)
        fields = QgsFields()
        for n, ln in (
            ("alignment", 254),
            ("sta_start", 80),
            ("sta_end", 80),
            ("z_source", 80),
        ):
            fields.append(QgsField(n, FIELD_STRING, len=ln))
        sink, dest = _sink(self, p, c, fields, QgsWkbTypes.Type.LineStringZ)
        root = load_document(path).root
        # Build sampled vertical profiles keyed by alignment name. ProfAlign
        # is a flat PVI/ParaCurve/CircCurve sequence -- see landxml.profile.
        # A ProfAlign's own `name` is a profile label (e.g. "frl" = finish
        # road level), not the owning alignment's name: real LandXML nests
        # <Profile><ProfAlign> inside the <Alignment> it belongs to, it does
        # not cross-reference it by an `alignment=` attribute equal to a
        # ProfAlign name. Associate by walking each Alignment's own subtree
        # first; fall back to an explicit alignment= reference if an exporter
        # uses that pattern instead.
        profiles = {}
        for a_el in descendants(root, "Alignment"):
            key = a_el.attrib.get("name", "")
            if key in profiles:
                continue
            pa = first_descendant(a_el, "ProfAlign")
            if pa is not None:
                samples = read_vertical_profile(pa, seg)
                if samples:
                    profiles[key] = samples
        for prof in descendants(root, "ProfAlign"):
            ref = prof.attrib.get("alignment")
            if ref and ref not in profiles:
                samples = read_vertical_profile(prof, seg)
                if samples:
                    profiles[ref] = samples

        def z_at(samples, sta):
            if not samples or sta < samples[0][0] or sta > samples[-1][0]:
                return None
            for i in range(1, len(samples)):
                st0, z0 = samples[i - 1]
                st1, z1 = samples[i]
                if sta <= st1:
                    q = (sta - st0) / max(st1 - st0, 1e-12)
                    return z0 + q * (z1 - z0)
            return samples[-1][1]

        parts_all = _alignment_parts(path, seg, alignment_filter)
        for ai, (name, desc, sta0, sta1, parts, unsupported) in enumerate(parts_all):
            if fb.isCanceled():
                break
            pts = []
            for part in parts:
                if pts and part and pts[-1] == part[0]:
                    pts.extend(part[1:])
                else:
                    pts.extend(part)
            if len(pts) < 2:
                continue
            import numpy as np

            raw = np.asarray([[x, y, 0] for x, y in pts], dtype=float)
            cum = [0.0]
            for i in range(1, len(raw)):
                cum.append(
                    cum[-1]
                    + math.hypot(raw[i, 0] - raw[i - 1, 0], raw[i, 1] - raw[i - 1, 1])
                )
            if not sta0:
                fb.pushWarning(
                    f"Skipped 3D centerline '{name}': alignment start station is missing."
                )
                continue
            s0 = float(sta0)
            prof = profiles.get(name)
            if not prof:
                fb.pushWarning(
                    f"Skipped 3D centerline '{name}': no matching vertical profile; Z was not fabricated."
                )
                continue
            start = max(s0, prof[0][0])
            end = min(s0 + cum[-1], prof[-1][0])
            if end <= start:
                fb.pushWarning(
                    f"Skipped 3D centerline '{name}': profile does not overlap the alignment station range."
                )
                continue

            def xy_at(station):
                distance = station - s0
                i = max(1, bisect_left(cum, distance))
                if i >= len(cum):
                    return raw[-1, :2]
                fraction = (distance - cum[i - 1]) / max(
                    cum[i] - cum[i - 1], 1e-12
                )
                return raw[i - 1, :2] + fraction * (raw[i, :2] - raw[i - 1, :2])

            stations = [start]
            stations.extend(s0 + d for d in cum if start < s0 + d < end)
            stations.append(end)
            clipped = np.asarray(
                [[*xy_at(station), 0] for station in stations], dtype=float
            )
            z = [z_at(prof, station) for station in stations]
            arr = transform_vertices(clipped, method=m, **tp)
            xy = arr[:, :2]
            points3 = [
                QgsPoint(float(x), float(y), float(zz)) for (x, y), zz in zip(xy, z)
            ]
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPolyline(points3))
            feat.setAttributes([name, str(start), str(end), "Vertical profile"])
            sink.addFeature(feat)
            if start > s0 or end < s0 + cum[-1]:
                fb.pushWarning(
                    f"Created partial 3D centerline '{name}' from station {start:.3f} to {end:.3f}; "
                    "the profile does not cover the remaining alignment."
                )
            fb.setProgress(int(100 * (ai + 1) / max(1, len(parts_all))))
        return {"OUTPUT": dest}


class SurfaceBoundaryAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return SurfaceBoundaryAlgorithm()

    def name(self):
        return "landxml_surface_boundary"

    def displayName(self):
        return "Extract TIN Surface Boundary"

    def group(self):
        return "Surface extraction"

    def groupId(self):
        return "surface_extraction"

    def shortHelpString(self):
        return "Extracts the outer boundary of the LandXML TIN as a polygon/line."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common(self)
        self.addParameter(
            QgsProcessingParameterString(
                "SURFACE",
                "Surface name (blank = first)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT",
                "Surface boundary",
                type=QgsProcessing.SourceType.TypeVectorLine,
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        surf = self.parameterAsString(p, "SURFACE", c) or None
        xyz, faces, meta = read_tin(path, surf)
        m, out, src, tp = _transform_params(p, c, self, fb, path)
        xyz = transform_vertices(xyz, method=m, **tp)
        transformed_bounds = BoundsTracker()
        transformed_bounds.add(xyz)
        transformed_bounds.report(fb)
        edges = {}
        for a, b, cc in faces:
            for u, v in [(a, b), (b, cc), (cc, a)]:
                k = tuple(sorted((int(u), int(v))))
                edges[k] = edges.get(k, 0) + 1
        bedges = [k for k, v in edges.items() if v == 1]
        adj = {}
        for a, b in bedges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        lines = []
        unused = set(bedges)
        while unused:
            a, b = next(iter(unused))
            line = [a, b]
            unused.remove((a, b))
            unused.discard((b, a))
            cur = b
            prev = a
            while True:
                nxts = [
                    n
                    for n in adj.get(cur, [])
                    if n != prev and (min(cur, n), max(cur, n)) in unused
                ]
                if not nxts:
                    break
                nxt = nxts[0]
                unused.discard((min(cur, nxt), max(cur, nxt)))
                line.append(nxt)
                prev, cur = cur, nxt
            lines.append(line)
        fields = QgsFields()
        fields.append(QgsField("surface", FIELD_STRING, len=254))
        for name in (
            "surface_name",
            "source_vendor",
            "source_file",
            "source_name",
            "source_description",
        ):
            fields.append(QgsField(name, FIELD_STRING, len=254))
        sink, dest = _sink(self, p, c, fields, QgsWkbTypes.Type.LineString)
        if sink is None:
            raise QgsProcessingException("Could not create surface boundary layer.")
        document = load_document(path)
        surface = next(
            (
                item
                for item in document.surfaces()
                if item.attrib.get("name") == meta["surface_name"]
            ),
            None,
        )
        for line in lines:
            f = QgsFeature(fields)
            f.setGeometry(
                QgsGeometry.fromPolylineXY(
                    [QgsPointXY(xyz[i, 0], xyz[i, 1]) for i in line]
                )
            )
            f.setAttributes(
                [
                    meta["surface_name"],
                    meta["surface_name"],
                    document.vendor,
                    os.path.basename(path),
                    meta["surface_name"],
                    surface.attrib.get("desc") if surface is not None else None,
                ]
            )
            sink.addFeature(f)
        return {"OUTPUT": dest}


class StationPointsAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return StationPointsAlgorithm()

    def name(self):
        return "landxml_station_points"

    def displayName(self):
        return "Create Alignment Station Points"

    def group(self):
        return "Road design extraction"

    def groupId(self):
        return "road_design_extraction"

    def shortHelpString(self):
        return "Creates points at whole multiples of the station interval (for example, 20, 40, 60), with an optional off-grid alignment endpoint."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common(self)
        self.addParameter(
            number_param("INTERVAL", "Station interval", 20, 0.01, 100000, decimals=2)
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "INCLUDE_ENDPOINT",
                "Include alignment end station (may be off interval)",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT",
                "Station points",
                type=QgsProcessing.SourceType.TypeVectorPoint,
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        m, out, src, tp = _transform_params(p, c, self, fb, path)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        include_end = self.parameterAsBoolean(p, "INCLUDE_ENDPOINT", c)
        fields = QgsFields()
        for n in ("alignment", "station", "bearing_deg"):
            fields.append(
                QgsField(n, FIELD_DOUBLE if n != "alignment" else FIELD_STRING, len=254)
            )
        sink, dest = _sink(self, p, c, fields, QgsWkbTypes.Type.Point)
        interval = self.parameterAsDouble(p, "INTERVAL", c)
        for name, desc, sta0, sta1, parts, _ in _alignment_parts(
            path, 5.0, alignment_filter
        ):
            if not sta0:
                fb.pushWarning(
                    f"Skipped station points for '{name}': start station is missing."
                )
                continue
            pts = []
            for part in parts:
                pts.extend(part[1:] if pts and part and pts[-1] == part[0] else part)
            if len(pts) < 2:
                continue
            import numpy as np

            raw = np.asarray([[x, y, 0] for x, y in pts], dtype=float)
            cum = [0.0]
            for i in range(1, len(raw)):
                cum.append(
                    cum[-1]
                    + math.hypot(raw[i, 0] - raw[i - 1, 0], raw[i, 1] - raw[i - 1, 1])
                )
            stations = regular_station_distances(
                float(sta0), cum[-1], interval, include_end
            )
            for distance, station in stations:
                i = min(max(1, bisect_left(cum, distance)), len(cum) - 1)
                prev = i - 1
                segment = max(cum[i] - cum[prev], 1e-12)
                q = (distance - cum[prev]) / segment
                source = raw[prev] + q * (raw[i] - raw[prev])
                xy = transform_vertices(
                    np.asarray([source], dtype=float), method=m, **tp
                )[0]
                bearing = (
                    math.degrees(
                        math.atan2(raw[i, 0] - raw[prev, 0], raw[i, 1] - raw[prev, 1])
                    )
                    + 360
                ) % 360
                f = QgsFeature(fields)
                f.setGeometry(
                    QgsGeometry.fromPointXY(QgsPointXY(float(xy[0]), float(xy[1])))
                )
                f.setAttributes([name, station, bearing])
                sink.addFeature(f)
        return {"OUTPUT": dest}


class CrossSectionsAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return CrossSectionsAlgorithm()

    def name(self):
        return "landxml_cross_sections"

    def displayName(self):
        return "Extract LandXML Cross-Sections"

    def group(self):
        return "Road design extraction"

    def groupId(self):
        return "road_design_extraction"

    def shortHelpString(self):
        return "Extracts LandXML CrossSect geometry as line features. Supports PntList3D/PntList2D-style section point lists and station attributes where present. CrossSect records that only carry station-relative DesignCrossSectSurf/CrossSectPnt design-template geometry (pavement/subbase layers etc.), without an absolute-coordinate point list, are skipped and reported in Processing messages rather than being placed at a fabricated position."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common(self)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT",
                "Cross-section lines",
                type=QgsProcessing.SourceType.TypeVectorLine,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "POINTS",
                "Cross-section points",
                type=QgsProcessing.SourceType.TypeVectorPoint,
                optional=True,
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        m, out, src, tp = _transform_params(p, c, self, fb, path)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        doc = load_document(path)
        records, warnings = read_cross_sections(doc.root)
        records = [
            record
            for record in records
            if not alignment_filter or record.alignment_name == alignment_filter
        ]
        dimensions = {len(record.points[0]) for record in records}
        if len(dimensions) > 1:
            raise QgsProcessingException(
                "Selected cross sections mix 2D and 3D coordinates; export them separately."
            )
        dim = 3 if dimensions == {3} else 2
        fields = QgsFields()
        for name in (
            "alignment_name",
            "source_name",
            "source_description",
            "source_vendor",
            "source_file",
        ):
            fields.append(QgsField(name, FIELD_STRING, len=254))
        fields.append(QgsField("station", FIELD_DOUBLE))
        fields.append(QgsField("point_count", FIELD_INT))
        wkb = QgsWkbTypes.Type.LineStringZ if dim == 3 else QgsWkbTypes.Type.LineString
        sink, dest = _sink(self, p, c, fields, wkb)
        if sink is None:
            raise QgsProcessingException("Could not create cross-section line layer.")
        point_fields = QgsFields()
        for name in ("alignment_name", "source_name", "source_vendor", "source_file"):
            point_fields.append(QgsField(name, FIELD_STRING, len=254))
        for name in ("station", "offset", "elevation"):
            point_fields.append(QgsField(name, FIELD_DOUBLE))
        point_fields.append(QgsField("point_index", FIELD_INT))
        point_sink, point_dest = None, None
        if p.get("POINTS"):
            point_wkb = QgsWkbTypes.Type.PointZ if dim == 3 else QgsWkbTypes.Type.Point
            point_sink, point_dest = self.parameterAsSink(
                p, "POINTS", c, point_fields, point_wkb, out
            )
            if point_sink is None:
                raise QgsProcessingException(
                    "Could not create cross-section point layer."
                )
        import numpy as np

        transformed_bounds = BoundsTracker()
        for record in records:
            if fb.isCanceled():
                raise QgsProcessingException("Cross-section extraction cancelled.")
            arr = transform_vertices(
                np.asarray(record.points, dtype=float), method=m, **tp
            )
            transformed_bounds.add(arr)
            geom = (
                QgsGeometry.fromPolyline([QgsPoint(*map(float, row)) for row in arr])
                if dim == 3
                else QgsGeometry.fromPolylineXY(
                    [QgsPointXY(*map(float, row)) for row in arr]
                )
            )
            feature = QgsFeature(fields)
            feature.setGeometry(geom)
            feature.setAttributes(
                [
                    record.alignment_name,
                    record.name,
                    record.description,
                    doc.vendor,
                    os.path.basename(path),
                    record.station,
                    len(record.points),
                ]
            )
            sink.addFeature(feature)
            if point_sink is not None:
                for index, row in enumerate(arr):
                    point = QgsFeature(point_fields)
                    point.setGeometry(
                        QgsGeometry.fromPoint(QgsPoint(*map(float, row)))
                        if dim == 3
                        else QgsGeometry.fromPointXY(QgsPointXY(*map(float, row)))
                    )
                    point.setAttributes(
                        [
                            record.alignment_name,
                            record.name,
                            doc.vendor,
                            os.path.basename(path),
                            record.station,
                            None,
                            float(row[2]) if dim == 3 else None,
                            index,
                        ]
                    )
                    point_sink.addFeature(point)
        for warning in warnings[:20]:
            fb.pushWarning(warning)
        if len(warnings) > 20:
            fb.pushWarning(f"{len(warnings) - 20} additional sections were skipped.")
        transformed_bounds.report(fb)
        return {
            "OUTPUT": dest,
            "POINTS": point_dest or "",
            "SECTION_COUNT": len(records),
        }


class GenericLinesAlgorithm(QgsProcessingAlgorithm):
    MODE = "FEATURE"

    def createInstance(self):
        return GenericLinesAlgorithm(self.mode)

    def __init__(self, mode="FEATURE"):
        super().__init__()
        self.mode = mode

    def name(self):
        return "landxml_" + (
            "feature_lines" if self.mode == "FEATURE" else "breaklines"
        )

    def displayName(self):
        return "Extract LandXML " + (
            "Feature Lines" if self.mode == "FEATURE" else "Breaklines"
        )

    def group(self):
        return "Surface extraction"

    def groupId(self):
        return "surface_extraction"

    def shortHelpString(self):
        return "Extracts 3D polyline-style FeatureLine or Breakline records from LandXML when present. Unsupported structures are skipped with a Processing message."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common(self)
        self.addParameter(
            QgsProcessingParameterString(
                "NAME_FILTER",
                "Feature/breakline name (blank = all)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", "Output lines", type=QgsProcessing.SourceType.TypeVectorLine
            )
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        m, out, src, tp = _transform_params(p, c, self, fb, path)
        name_filter = self.parameterAsString(p, "NAME_FILTER", c).strip()
        doc = load_document(path)
        records, warnings = read_line_features(doc.root)
        kind = "FeatureLine" if self.mode == "FEATURE" else "Breakline"
        records = [
            record
            for record in records
            if record.kind == kind and (not name_filter or record.name == name_filter)
        ]
        dimensions = {len(record.points[0]) for record in records}
        if len(dimensions) > 1:
            raise QgsProcessingException(
                "Selected lines mix 2D and 3D coordinates; export them separately to preserve their dimensions."
            )
        fields = QgsFields()
        for field in (
            "name",
            "description",
            "type",
            "source_name",
            "source_description",
            "feature_name",
            "feature_type",
            "surface_name",
            "feature_code",
            "breakline_type",
            "source_vendor",
            "source_file",
            "source_id",
            "source_user_id",
        ):
            fields.append(QgsField(field, FIELD_STRING, len=254))
        wkb = (
            QgsWkbTypes.Type.LineStringZ
            if dimensions == {3}
            else QgsWkbTypes.Type.LineString
        )
        sink, dest = _sink(self, p, c, fields, wkb)
        if sink is None:
            raise QgsProcessingException("Could not create output line layer.")
        import numpy as np

        transformed_bounds = BoundsTracker()
        for record in records:
            if fb.isCanceled():
                raise QgsProcessingException("Line extraction cancelled.")
            xyz = transform_vertices(
                np.asarray(record.points, dtype=float), method=m, **tp
            )
            transformed_bounds.add(xyz)
            if len(record.points[0]) == 3:
                geom = QgsGeometry.fromPolyline(
                    [QgsPoint(float(x), float(y), float(z)) for x, y, z in xyz]
                )
            else:
                geom = QgsGeometry.fromPolylineXY(
                    [QgsPointXY(float(x), float(y)) for x, y in xyz]
                )
            f = QgsFeature(fields)
            f.setGeometry(geom)
            f.setAttributes(
                [
                    record.name,
                    record.description,
                    record.kind,
                    record.name,
                    record.description,
                    record.name,
                    record.kind,
                    record.surface_name,
                    record.feature_code,
                    record.breakline_type,
                    doc.vendor,
                    os.path.basename(path),
                    record.properties.get("id"),
                    record.properties.get("UsrID"),
                ]
            )
            sink.addFeature(f)
        for warning in warnings[:20]:
            fb.pushWarning(warning)
        if len(warnings) > 20:
            fb.pushWarning(
                f"{len(warnings) - 20} additional line records were skipped."
            )
        transformed_bounds.report(fb)
        return {"OUTPUT": dest}


class FeatureLinesAlgorithm(GenericLinesAlgorithm):
    def __init__(self):
        super().__init__("FEATURE")

    def createInstance(self):
        return FeatureLinesAlgorithm()


class BreaklinesAlgorithm(GenericLinesAlgorithm):
    def __init__(self):
        super().__init__("BREAK")

    def createInstance(self):
        return BreaklinesAlgorithm()
