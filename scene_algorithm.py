"""Export 3D Scene and Viewer: any LandXML surfaces, DEM rasters, contours and linework in an area."""

from __future__ import annotations

import math
import os

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)

from .compat import mark_advanced
from .core import read_tin, transform_vertices
from .grading_algorithm import HORIZONTAL_UNITS
from .landxml import surface_style as ss
from .landxml.catalog import match_name, read_catalog
from .landxml.grading import _inside_many
from .landxml.plan_features import level_lines
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.reports import station_label
from .landxml.scene import SceneWriter, extrude, parse_views, station_offset, water_mesh
from .landxml.scene_viewer import write_viewer
from .landxml.tin_overlay import TinIndex, sample_tin
from .params import number_param
from .processing_widgets import name_param, names_param, style_param, surfaces_param
from .processing_common import coordinate_choices
from .project_settings import ProjectSettingsAlgorithm
from .road_features import _alignment_parts, _param_coordinates, centerlines_3d, merge_parts

GROUP = "8. Reports and sheets"
DEFAULT_VIEWS = "sw: -0.6, -0.8, 0.55; se: 0.8, -0.5, 0.6"
GROUP_ID = "reports"
SURFACE_COLOURS = ["#cfd8c4", "#b8c7d9", "#d9c7b8", "#c4d8d3", "#d8d0c4"]
VERTICAL_UNITS = [("As declared in the file", None), ("Metre", "meter"), ("International foot", "foot"),
                  ("US survey foot", "USSurveyFoot")]


def parse_surface_list(text):
    """'EG = plain #cfd8c4; FG = depth over EG; Datum' → [(name, style text, existing name or '')]."""
    out = []
    for part in (text or "").split(";"):
        if not part.strip():
            continue
        name, _, style = part.partition("=")
        style = style.strip()
        over = ""
        if " over " in f" {style} ":
            style, _, over = style.partition(" over ")
        out.append((name.strip(), style.strip(), over.strip()))
    return out


def parse_intervals(text):
    """'EG = 1; FG = 0.5' → {'EG': 1.0, 'FG': 0.5}; a bare number applies to every surface ('*')."""
    out = {}
    for part in (text or "").split(";"):
        if not part.strip():
            continue
        name, sep, value = part.rpartition("=")
        try:
            out[name.strip() if sep else "*"] = float(value)
        except ValueError as exc:
            raise QgsProcessingException(f"A contour interval is 'surface = interval': {part.strip()}") from exc
    return out


def _inverse_box(box, method, params):
    """Stored-coordinate box of an output-coordinate box, for the simple interpretations (else None)."""
    x0, y0, x1, y1 = box
    dx, dy = float(params.get("dx", 0.0)), float(params.get("dy", 0.0))
    if method == "stored":
        return box
    if method == "swap":
        return (y0, x0, y1, x1)
    if method == "offset":
        return (x0 - dx, y0 - dy, x1 - dx, y1 - dy)
    if method == "swap_offset":
        return (y0 - dy, x0 - dx, y1 - dy, x1 - dx)
    return None


class Area:
    """The area to render, in output coordinates: a box, optionally with polygon rings."""

    def __init__(self, box, rings=()):
        self.box = box
        self.rings = [np.asarray(r, float)[:, :2] for r in rings]

    def keep_faces(self, xyz, faces):
        centres = xyz[faces, :2].mean(axis=1)
        x0, y0, x1, y1 = self.box
        keep = (centres[:, 0] >= x0) & (centres[:, 0] <= x1) & (centres[:, 1] >= y0) & (centres[:, 1] <= y1)
        if self.rings and keep.any():
            inside = np.zeros(len(faces), bool)
            for ring in self.rings:
                inside[keep] ^= _inside_many(centres[keep], ring)
            keep &= inside
        return keep

    def keep_points(self, points):
        x0, y0, x1, y1 = self.box
        keep = (points[:, 0] >= x0) & (points[:, 0] <= x1) & (points[:, 1] >= y0) & (points[:, 1] <= y1)
        if self.rings and keep.any():
            inside = np.zeros(len(points), bool)
            for ring in self.rings:
                inside[keep] ^= _inside_many(points[keep, :2], ring)
            keep &= inside
        return keep


def _compact(xyz, faces):
    used = np.unique(faces)
    remap = np.full(len(xyz), -1, np.int64)
    remap[used] = np.arange(len(used))
    return xyz[used], remap[faces], used


def refine_near(xyz, faces, footprint, target, rounds=5):
    """Split ground triangles that touch ``footprint`` (a TIN) until their edges are at most ``target`` long,
    so a coarse DEM can be cut cleanly along a narrow road; the ground keeps its shape."""
    from .landxml.structures import refine

    return refine(np.asarray(xyz, float), np.asarray(faces, np.int64), footprint, target, rounds)


def refine_edges(xyz, faces, footprint, target=None):
    """Split ground triangles that cross the edge of ``footprint`` (a design TIN) down to ``target``
    (default: half a metre or a 64th of the ground's typical triangle, whichever is larger)."""
    from .landxml.structures import refine

    xyz, faces = np.asarray(xyz, float), np.asarray(faces, np.int64)
    if not len(faces):
        return xyz, faces
    if target is None:
        tri = xyz[faces[: min(len(faces), 20000)]]
        typical = float(np.median(np.linalg.norm(tri[:, 1, :2] - tri[:, 0, :2], axis=1)))
        target = max(0.5, typical / 64.0)
    return refine(xyz, faces, footprint, target, rounds=7, boundary=True)


def _runs_inside(points, area):
    """Parts of a polyline inside the area (split where it leaves)."""
    inside = area.keep_points(points)
    out = []
    for run in np.split(np.arange(len(points)), np.flatnonzero(np.diff(inside.astype(int))) + 1):
        if inside[run[0]] and len(run) > 1:
            out.append(points[run])
    return out


def _densify(points, step):
    out = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        n = int(math.ceil(math.hypot(*(b[:2] - a[:2])) / step)) if step > 0 else 1
        for k in range(1, max(n, 1) + 1):
            out.append(a + (b - a) * k / max(n, 1))
    return np.asarray(out)


def _coverage_message(layer, crs, area):
    """Why a raster misses the scene area, with the extents, and a hint when swapped LandXML axes would fit."""
    extent = layer.extent()
    if layer.crs() != crs:
        extent = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance()).transformBoundingBox(extent)
    x0, y0, x1, y1 = area.box[:4]
    text = (f"Raster '{layer.name()}' does not cover the scene area. In the output CRS the area is "
            f"X {x0:,.0f}–{x1:,.0f}, Y {y0:,.0f}–{y1:,.0f}; the raster covers X {extent.xMinimum():,.0f}–"
            f"{extent.xMaximum():,.0f}, Y {extent.yMinimum():,.0f}–{extent.yMaximum():,.0f}.")
    swapped = QgsRectangle(y0, x0, y1, x1)
    if swapped.intersects(extent):
        text += (" With the LandXML's X and Y swapped the area would fall on the raster: choose Coordinate "
                 "interpretation 'Swap X / Y' (Civil 3D and OpenRoads usually store northing first).")
    else:
        text += " Check the raster's CRS and the scene's output CRS, or choose a raster that covers the design."
    return text


def raster_mesh(layer, crs, area, budget, feedback):
    """(xyz, faces) of a single-band DEM raster in the output CRS, inside the area, thinned to ``budget`` triangles."""
    from osgeo import gdal, osr

    source = layer.source().split("|")[0]
    dataset = gdal.Open(source)
    if dataset is None:
        raise QgsProcessingException(f"Could not open raster '{layer.name()}'.")
    band = dataset.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    gt = dataset.GetGeoTransform()
    if gt[2] or gt[4]:
        raise QgsProcessingException(f"Raster '{layer.name()}' is rotated; warp it to a north-up grid first.")
    to_raster = QgsCoordinateTransform(crs, layer.crs(), QgsProject.instance())
    box = to_raster.transformBoundingBox(QgsRectangle(*area.box[:2], *area.box[2:])) if layer.crs() != crs else \
        QgsRectangle(*area.box[:2], *area.box[2:])
    c0 = max(int((box.xMinimum() - gt[0]) / gt[1]) - 1, 0)
    c1 = min(int((box.xMaximum() - gt[0]) / gt[1]) + 2, dataset.RasterXSize)
    r0 = max(int((box.yMaximum() - gt[3]) / gt[5]) - 1, 0)
    r1 = min(int((box.yMinimum() - gt[3]) / gt[5]) + 2, dataset.RasterYSize)
    if c1 - c0 < 2 or r1 - r0 < 2:
        raise QgsProcessingException(_coverage_message(layer, crs, area))
    cells = (c1 - c0) * (r1 - r0)
    stride = max(1, int(math.ceil(math.sqrt(2 * cells / max(budget, 1000)))))
    if stride > 1:
        feedback.pushInfo(f"{layer.name()}: every {stride}th cell is used to stay within the triangle budget.")
    columns = np.arange(c0, c1, stride)
    rows = np.arange(r0, r1, stride)
    data = band.ReadAsArray(int(c0), int(r0), int(c1 - c0), int(r1 - r0)).astype(float)[::stride, ::stride]
    valid = np.isfinite(data)
    if nodata is not None:
        valid &= data != nodata
    X = gt[0] + (columns + 0.5) * gt[1]
    Y = gt[3] + (rows + 0.5) * gt[5]
    gx, gy = np.meshgrid(X, Y)
    xyz = np.column_stack([gx.ravel(), gy.ravel(), data.ravel()])
    if layer.crs() != crs:
        source_srs, target_srs = osr.SpatialReference(), osr.SpatialReference()
        source_srs.ImportFromWkt(layer.crs().toWkt())
        target_srs.ImportFromWkt(crs.toWkt())
        for srs in (source_srs, target_srs):
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        moved = np.asarray(osr.CoordinateTransformation(source_srs, target_srs).TransformPoints(xyz[:, :2].tolist()))
        xyz[:, :2] = moved[:, :2]
    n_rows, n_cols = data.shape
    index = np.arange(n_rows * n_cols).reshape(n_rows, n_cols)
    a, b = index[:-1, :-1].ravel(), index[:-1, 1:].ravel()
    c, d = index[1:, :-1].ravel(), index[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, c, b]), np.column_stack([b, c, d])])
    ok = valid.ravel()
    faces = faces[ok[faces].all(axis=1)]
    faces = faces[area.keep_faces(xyz, faces)]
    if not len(faces):
        raise QgsProcessingException(f"Raster '{layer.name()}' has no data in the area.")
    xyz, faces, _used = _compact(xyz, faces)
    return xyz, faces


class Scene3DAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return Scene3DAlgorithm()

    def name(self):
        return "landxml_3d_scene"

    def displayName(self):
        return "Export 3D Scene and Viewer"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Builds a 3D scene of an area from LandXML surfaces, DEM rasters and linework, and writes it as an "
            "interactive viewer (viewer.html: opens offline in any current browser, nothing to install) with "
            "scene.json and PLY meshes for other renderers.\n\n"
            "Area: a map extent, a polygon layer, or a corridor (an alignment and a width). Without one, the "
            "chosen surfaces' extent is used.\n"
            "Surfaces: 'name = style; name = style', for example 'EG = plain #cfd8c4; FG = slope' or "
            "'FG = depth over EG' (cut and fill against another surface). Styles: elevation, slope, depth, aspect, "
            "'attribute: breaks | colours', 'elevation: equal 9 | terrain' or 'plain #rrggbb'.\n"
            "Contours: 'EG = 1; FG = 0.5' or one interval for every surface; every fifth is an index contour.\n"
            "Rasters: single-band DEMs, thinned to stay within the triangle budget.\n"
            "Linework: alignments (3D where a profile covers them, otherwise draped), station labels, LandXML "
            "feature lines and breaklines, and any QGIS line or polygon layers (their own Z, or draped on the first "
            "surface). A road style ('TOP = road: 7, 1.5, 2') is painted as a finished road and cuts the ground under it.\n"
            "Synthetic road (feasibility): choose alignments with a profile but no corridor; a concept road is built "
            "on the profile (widths, crossfalls and slopes under Advanced) with side slopes run out to the chosen ground "
            "(surfaces or DEMs; the first wins). Structures come from a CSV schedule or typed entries such as "
            "'1+250 = box 3 x 2.0 x 2.0; 1+840 = pipe 2 x 0.9, skew 15; 1+364-2+293 = bridge, spans 7'. DEMs can be "
            "shifted or fitted to the alignment's surveyed ground profile.\n"
            "Views: 'name: dx, dy, dz; ...' (direction from the area towards the camera). Coordinates are in the "
            "output CRS relative to the scene origin.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("INPUT", "LandXML file (optional)", extension="xml", optional=True))
        self.addParameter(surfaces_param("SURFACES", "LandXML surfaces and styles"))
        self.addParameter(QgsProcessingParameterString("CONTOURS", "Contour intervals (source vertical units)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterMultipleLayers("RASTERS", "DEM rasters", QgsProcessing.SourceType.TypeRaster, optional=True))
        self.addParameter(style_param("RASTER_STYLE", "Raster style", default="elevation"))
        self.addParameter(QgsProcessingParameterNumber("RASTER_CONTOURS", "Raster contour interval (0 = none)",
                                                       QgsProcessingParameterNumber.Type.Double, 0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterExtent("AREA", "Area (map extent)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource("AREA_LAYER", "Area (polygons)",
                                                              [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(name_param("CORRIDOR", "Corridor: alignment name", "alignments"))
        self.addParameter(number_param("CORRIDOR_WIDTH", "Corridor width either side (output units)", 60.0, 1.0, 1e5, decimals=1))
        self.addParameter(QgsProcessingParameterNumber("STATION_FROM", "Corridor from station (blank = start)",
                                                       QgsProcessingParameterNumber.Type.Double, optional=True))
        self.addParameter(QgsProcessingParameterNumber("STATION_TO", "Corridor to station (blank = end)",
                                                       QgsProcessingParameterNumber.Type.Double, optional=True))
        for key, label, default in (("ALIGNMENTS", "Alignments", True), ("FEATURE_LINES", "LandXML feature lines and breaklines", True)):
            self.addParameter(QgsProcessingParameterBoolean(key, label, defaultValue=default))
        self.addParameter(number_param("STATION_LABELS", "Station labels every (0 = none)", 100.0, 0.0, 1e6, decimals=1))
        self.addParameter(QgsProcessingParameterMultipleLayers("VECTORS", "Other line or polygon layers",
                                                               QgsProcessing.SourceType.TypeVectorAnyGeometry, optional=True))
        self.addParameter(number_param("STRETCH", "Long corridors: a view every (stations; 0 = none)", 1000.0, 0.0, 1e6, decimals=0))
        self.addParameter(names_param(
            "SYNTHETIC", "Synthetic road: alignments (concept road on the profile)", "alignments", ";"))
        for key, label, default, lo, hi in (
                ("SYN_CARRIAGEWAY", "Synthetic road: carriageway width", 7.0, 2.0, 60.0),
                ("SYN_SHOULDER", "Synthetic road: shoulder width each side", 1.5, 0.0, 10.0),
                ("SYN_LANES", "Synthetic road: lanes", 2.0, 1.0, 8.0),
                ("SYN_CAMBER", "Synthetic road: carriageway crossfall (%)", 2.5, 0.0, 10.0),
                ("SYN_SHOULDER_FALL", "Synthetic road: shoulder crossfall (%)", 4.0, 0.0, 15.0),
                ("SYN_CUT_SLOPE", "Synthetic road: cut slope (horizontal per 1 vertical)", 1.5, 0.1, 10.0),
                ("SYN_FILL_SLOPE", "Synthetic road: fill slope (horizontal per 1 vertical)", 2.0, 0.1, 10.0),
                ("SYN_MAX_WIDTH", "Synthetic road: longest side slope (output units)", 40.0, 1.0, 500.0)):
            self.addParameter(number_param(key, label, default, lo, hi, decimals=2))
        self.addParameter(names_param(
            "SYN_GROUND", "Synthetic road: ground (first wins; blank = first surface)",
            "surfaces", ",", layers_param="RASTERS"))
        self.addParameter(QgsProcessingParameterBoolean(
            "SYN_BRIDGES", "Synthetic road: bridges where slopes miss the ground", defaultValue=True))
        self.addParameter(number_param("SYN_BRIDGE_SPAN", "Synthetic road: bridge span", 25.0, 5.0, 200.0, decimals=1))
        self.addParameter(QgsProcessingParameterFile(
            "SYN_STRUCTURES_CSV", "Synthetic road: culvert and bridge schedule (CSV)", extension="csv", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "SYN_STRUCTURES", "Synthetic road: structures typed in (1+250 = box 3 x 2 x 2)",
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "SYN_FIT_PROFILE", "Synthetic road: fit DEMs to the surveyed ground profile",
            defaultValue=False))
        self.addParameter(number_param("SYN_FIT_WIDTH", "Synthetic road: profile fit fades out over", 60.0, 5.0, 1000.0,
                                       decimals=1))
        self.addParameter(QgsProcessingParameterBoolean(
            "SYN_CHANNELS", "Synthetic road: cut a channel at each culvert", defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "SYN_MATCH_PROFILE", "Synthetic road: shift DEMs to the surveyed ground profile", defaultValue=False))
        self.addParameter(QgsProcessingParameterString("WATER", "Water levels ('surface = level; ...')", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString("EXTRUDE_FIELD", "Raise polygons by this height field", defaultValue="", optional=True))
        self.addParameter(number_param("EXTRUDE_HEIGHT", "Or raise all polygons by (0 = outlines only)", 0.0, 0.0, 1000.0, decimals=2))
        self.addParameter(number_param("MAX_TRIANGLES", "Triangle budget (all surfaces)", 2_000_000, 10_000, 5e7, decimals=0))
        self.addParameter(number_param("EXAGGERATION", "Suggested vertical exaggeration", 1.5, 0.1, 20.0, decimals=2))
        self.addParameter(QgsProcessingParameterString("VIEWS", "Camera views (a corridor gets side and along views by default)",
                                                       defaultValue=DEFAULT_VIEWS))
        self.addParameter(QgsProcessingParameterString("TITLE", "Title", defaultValue="", optional=True))
        _param_coordinates(self)
        self.addParameter(QgsProcessingParameterEnum("VERTICAL_UNIT", "Vertical unit (when the file does not declare it)",
                                                     options=[n for n, _u in VERTICAL_UNITS], defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "Scene folder"))
        self.addOutput(QgsProcessingOutputFile("SCENE", "scene.json"))
        self.addOutput(QgsProcessingOutputFile("VIEWER", "Interactive 3D viewer (HTML)"))

    # ------------------------------------------------------------------ helpers
        mark_advanced(self, ("RASTER_CONTOURS", "STRETCH", "SYN_LANES", "SYN_CAMBER", "SYN_SHOULDER_FALL", "SYN_CUT_SLOPE",
                             "SYN_FILL_SLOPE", "SYN_MAX_WIDTH", "SYN_BRIDGES", "SYN_BRIDGE_SPAN", "SYN_FIT_WIDTH",
                             "SYN_CHANNELS", "SYN_MATCH_PROFILE", "WATER", "EXTRUDE_FIELD", "EXTRUDE_HEIGHT",
                             "MAX_TRIANGLES", "EXAGGERATION", "VIEWS", "TITLE"))

    def _crs_and_method(self, p, c, fb, path):
        if path:
            method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
            return method, crs, params, doc
        if p.get("OUTPUT_CRS") in (None, ""):
            raise QgsProcessingException("Choose an output CRS.")
        return "stored", self.parameterAsCrs(p, "OUTPUT_CRS", c), {}, None

    def _area(self, p, c, path, method, params, crs, fb):
        rings = []
        box = None
        if p.get("AREA"):
            rect = self.parameterAsExtent(p, "AREA", c, crs)
            if not rect.isEmpty():
                box = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
        source = self.parameterAsSource(p, "AREA_LAYER", c) if p.get("AREA_LAYER") else None
        if source is not None:
            transform = QgsCoordinateTransform(source.sourceCrs(), crs, QgsProject.instance())
            for feature in source.getFeatures():
                geometry = QgsGeometry(feature.geometry())
                geometry.transform(transform)
                for polygon in (geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]):
                    rings += [[(pt.x(), pt.y()) for pt in ring] for ring in polygon]
        corridor = self.parameterAsString(p, "CORRIDOR", c).strip()
        if corridor:
            if not path:
                raise QgsProcessingException("A corridor needs the LandXML file with its alignment.")
            name = match_name(read_catalog(path).alignments, corridor, "alignment")
            self._corridor_name = name
            parts = _alignment_parts(path, 2.0, name)
            points = merge_parts(parts[0][4]) if parts else []
            if not points:
                raise QgsProcessingException(f"Alignment '{name}' has no geometry.")
            raw = np.array(points, float)
            start = float(parts[0][2]) if parts[0][2] not in (None, "") else 0.0
            stations = start + np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(raw[:, :2], axis=0).T))])
            low = p.get("STATION_FROM")
            high = p.get("STATION_TO")
            low = stations[0] if low in (None, "") else float(low)
            high = stations[-1] if high in (None, "") else float(high)
            if high <= low:
                raise QgsProcessingException("The corridor end station must be after its start station.")
            chosen = np.unique(np.concatenate([[low, high], stations[(stations > low) & (stations < high)]]))
            chosen = chosen[(chosen >= stations[0]) & (chosen <= stations[-1])]
            if len(chosen) < 2:
                raise QgsProcessingException(f"The stations are outside alignment '{name}'.")
            points = np.column_stack([np.interp(chosen, stations, raw[:, 0]), np.interp(chosen, stations, raw[:, 1])])
            plan = transform_vertices(np.column_stack([points, np.zeros(len(points))]), method=method, **params)
            chord = plan[-1, :2] - plan[0, :2]
            self._axis = chord / max(np.hypot(*chord), 1e-9)
            buffer = QgsGeometry.fromPolylineXY([QgsPointXY(float(x), float(y)) for x, y, _z in plan]).buffer(
                self.parameterAsDouble(p, "CORRIDOR_WIDTH", c), 8)
            for polygon in (buffer.asMultiPolygon() if buffer.isMultipart() else [buffer.asPolygon()]):
                rings += [[(pt.x(), pt.y()) for pt in ring] for ring in polygon]
        if rings:
            stack = np.vstack([np.asarray(r) for r in rings])
            ring_box = (stack[:, 0].min(), stack[:, 1].min(), stack[:, 0].max(), stack[:, 1].max())
            box = ring_box if box is None else (max(box[0], ring_box[0]), max(box[1], ring_box[1]),
                                                min(box[2], ring_box[2]), min(box[3], ring_box[3]))
        return Area(box, rings) if box else None

    def _road_path(self, path, method, params, fb):
        """(name, 3D points in output coordinates, stations) of the corridor alignment, or the first
        alignment with a profile."""
        wanted = self._corridor_name or ""
        for name, start, _end, stored in centerlines_3d(path, 2.0, None, fb.pushInfo, fb.isCanceled, max_step=5.0):
            if wanted and name != wanted:
                continue
            xyz = transform_vertices(stored, method=method, **params)
            raw = np.asarray(stored, float)
            stations = start + np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(raw[:, :2], axis=0).T))])
            return name, xyz, stations
        return None

    @staticmethod
    def _ground_profile(path, name):
        """f(stations) -> surveyed ground levels (NaN outside the profile), or None."""
        from .landxml.longsection import default_pair, read_alignment_profiles
        from .landxml.parser import load_document

        alignment = next((a for a in load_document(path).alignments() if (a.attrib.get("name") or "") == name), None)
        if alignment is None:
            return None
        _design, profile = default_pair(read_alignment_profiles(alignment, 5.0))
        if profile is None:
            return None
        parts = [np.asarray(part, float) for part in profile.parts]

        def levels(stations):
            out = np.full(len(stations), np.nan)
            for part in parts:
                inside = (stations >= part[:, 0].min()) & (stations <= part[:, 0].max())
                out[inside] = np.interp(stations[inside], part[:, 0], part[:, 1])
            return out
        return levels

    @staticmethod
    def _dem_offset(path, name, centre, stations, ground, fb):
        """Median (surveyed ground profile − DEM) along the alignment, or None without a ground profile."""
        from .landxml.longsection import default_pair, read_alignment_profiles
        from .landxml.parser import load_document
        from .landxml.tin_overlay import sample_tin

        alignment = next((a for a in load_document(path).alignments() if (a.attrib.get("name") or "") == name), None)
        if alignment is None:
            return None
        _design, profile = default_pair(read_alignment_profiles(alignment, 5.0))
        if profile is None:
            return None
        dem = sample_tin(ground, centre[:, :2])
        surveyed = np.full(len(stations), np.nan)
        for part in profile.parts:
            part = np.asarray(part, float)
            inside = (stations >= part[:, 0].min()) & (stations <= part[:, 0].max())
            surveyed[inside] = np.interp(stations[inside], part[:, 0], part[:, 1])
        difference = surveyed - dem
        difference = difference[np.isfinite(difference)]
        if len(difference) < 3:
            return None
        median = float(np.median(difference))
        spread = float(np.percentile(difference, 90) - np.percentile(difference, 10))
        message = (f"The DEM is {-median:+.2f} from the surveyed ground profile '{profile.name}' along the alignment "
                   f"(median; 10–90 % spread {spread:.2f}).")
        if abs(median) > 1.0:
            fb.pushWarning(message + " A vertical datum difference (for example ellipsoidal against local heights) "
                           "makes every cut and fill wrong by about that much; tick 'match the surveyed ground profile' "
                           "to shift the DEMs.")
        else:
            fb.pushInfo(message)
        return median

    def _synthetic(self, p, c, fb, path, method, params, area, meshes, unders, vertical_factor, wanted, first=True):
        """Add a synthetic finished road along an alignment's profile, cut into the ground meshes."""
        from .landxml.grading import volume_summary
        from .landxml.structures import (MeshBuilder, bridge, carve_channels, culvert, low_points, read_schedule_csv,
                                         read_schedule_text)
        from .landxml.synthetic_corridor import _normals as corridor_normals, synthetic_corridor
        from .landxml.tin_overlay import depth_tin

        if not path:
            raise QgsProcessingException("A synthetic corridor needs the LandXML file with the alignment and profile.")
        name = match_name(read_catalog(path).alignments, wanted, "alignment")
        lines = centerlines_3d(path, 2.0, name, fb.pushInfo, fb.isCanceled, max_step=2.0)
        if not lines:
            raise QgsProcessingException(f"Alignment '{name}' has no vertical profile; a synthetic corridor follows the profile.")
        _n, first, _e, stored = lines[0]
        centre = transform_vertices(stored, method=method, **params)
        stations = first + np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(stored[:, :2], axis=0).T))])
        if area is not None:
            inside = area.keep_points(centre)
            if inside.sum() < 2:
                raise QgsProcessingException(f"Alignment '{name}' is outside the area.")
            idx = np.flatnonzero(inside)
            centre, stations = centre[idx[0]:idx[-1] + 1], stations[idx[0]:idx[-1] + 1]
        grounds = [m for m in meshes if m[2] in ("surface", "raster")]
        if not grounds:
            raise QgsProcessingException("A synthetic corridor needs a ground surface or DEM to run its side slopes out to.")
        picked = []
        for chosen in (t.strip() for t in self.parameterAsString(p, "SYN_GROUND", c).split(",")):
            if not chosen:
                continue
            candidates = [m for m in meshes if not str(m[0]).startswith("syn")]       # designed surfaces may be named too
            named = [m for m in candidates if m[1].lower() == chosen.lower()] or \
                [m for m in candidates if m[1].lower().startswith(chosen.lower())]
            if not named:
                raise QgsProcessingException(f"Synthetic ground '{chosen}' is not among the scene's surfaces and DEMs: "
                                             + ", ".join(m[1] for m in meshes if not str(m[0]).startswith("syn")))
            picked.append(named[0])
        if picked:
            grounds = picked + [m for m in grounds if all(m is not k for k in picked)]
        def build_ground():
            """The first chosen ground, with later ones filling in where it does not reach (current meshes)."""
            current = {m[0]: m for m in meshes}
            order = [current[m[0]] for m in (picked or grounds[:1])]
            merged = (order[0][3], order[0][4])
            for extra_ground in order[1:]:
                covered = np.isfinite(TinIndex(*merged).sample(extra_ground[3][extra_ground[4]].mean(axis=1)))
                merged = (np.vstack([merged[0], extra_ground[3]]),
                          np.vstack([merged[1], extra_ground[4][~covered] + len(merged[0])]))
            return merged

        ground = build_ground()
        if first:
            fb.pushInfo("Synthetic corridors run their side slopes out to "
                        + " then ".join(f"'{m[1]}'" for m in (picked or grounds[:1])) + ".")
        if grounds[0][2] == "raster" and first:
            shift = self._dem_offset(path, name, centre, stations, ground, fb)
            if shift is not None and self.parameterAsBool(p, "SYN_MATCH_PROFILE", c):
                for index, mesh in enumerate(meshes):
                    if mesh[2] == "raster":
                        moved = mesh[3].copy()
                        moved[:, 2] += shift
                        meshes[index] = (*mesh[:3], moved, *mesh[4:])
                ground = build_ground()
                fb.pushInfo(f"DEMs raised by {shift:+.2f} to match the alignment's surveyed ground profile.")
        covered = np.isfinite(TinIndex(*ground).sample(centre[:, :2]))
        if covered.sum() < 2:
            raise QgsProcessingException(f"The ground does not cover alignment '{name}' in the area.")
        if not covered.all():
            idx = np.flatnonzero(covered)
            centre, stations = centre[idx[0]:idx[-1] + 1], stations[idx[0]:idx[-1] + 1]
            fb.pushInfo(f"Synthetic corridor limited to {stations[0]:.0f}–{stations[-1]:.0f}, where the ground covers the alignment.")
        if self.parameterAsBool(p, "SYN_FIT_PROFILE", c):
            profile = self._ground_profile(path, name)
            if profile is None:
                fb.pushWarning(f"Alignment '{name}' has no surveyed ground profile; the ground is not fitted to it.")
            else:
                taper = self.parameterAsDouble(p, "SYN_FIT_WIDTH", c)
                normal0 = corridor_normals(centre[:, :2])
                ribbon = np.concatenate([np.column_stack([centre[:, :2] + normal0 * side * taper, centre[:, 2]])
                                         for side in (-1.0, 1.0)])
                n = len(centre)
                band_faces = np.concatenate([np.column_stack([np.arange(n - 1), np.arange(n - 1) + n, np.arange(n - 1) + 1]),
                                             np.column_stack([np.arange(n - 1) + 1, np.arange(n - 1) + n, np.arange(n - 1) + n + 1])])
                surveyed = profile(stations)
                for index, mesh in enumerate(meshes):
                    if mesh[2] != "raster":
                        continue
                    rxyz, rfaces = refine_near(mesh[3], mesh[4], (ribbon, band_faces), max(taper / 8.0, 2.0), rounds=4)
                    difference = surveyed - sample_tin((rxyz, rfaces), centre[:, :2])
                    known = np.isfinite(difference)
                    if known.sum() < 2:
                        continue
                    # station and offset of each ground point, read off the ribbon (station and offset as its "levels")
                    vs = sample_tin((np.column_stack([ribbon[:, :2], np.tile(stations, 2)]), band_faces), rxyz[:, :2])
                    vo = sample_tin((np.column_stack([ribbon[:, :2], np.repeat([-taper, taper], n)]), band_faces), rxyz[:, :2])
                    weight = np.clip(1.0 - np.abs(np.nan_to_num(vo, nan=taper)) / taper, 0.0, 1.0)
                    vs = np.nan_to_num(vs, nan=-np.inf)
                    covered = (vs >= stations[known][0]) & (vs <= stations[known][-1])
                    rxyz = rxyz.copy()
                    rxyz[:, 2] += np.where(covered, weight * np.interp(vs, stations[known], difference[known]), 0.0)
                    meshes[index] = (*mesh[:3], rxyz, rfaces, *mesh[5:])
                    fb.pushInfo(f"{mesh[1]}: fitted to the surveyed ground profile along the road (correction "
                                f"{np.nanmin(difference):+.1f} to {np.nanmax(difference):+.1f} on the centreline, fading out "
                                f"{taper:g} either side).")
                ground = build_ground()
        width = self.parameterAsDouble(p, "SYN_CARRIAGEWAY", c)
        shoulder = self.parameterAsDouble(p, "SYN_SHOULDER", c)
        lanes = int(self.parameterAsDouble(p, "SYN_LANES", c))
        ground_index = TinIndex(*ground)
        xyz, faces, station, offset, daylight = synthetic_corridor(
            centre, stations, ground_index, width, shoulder,
            self.parameterAsDouble(p, "SYN_CAMBER", c) / 100.0, self.parameterAsDouble(p, "SYN_SHOULDER_FALL", c) / 100.0,
            self.parameterAsDouble(p, "SYN_CUT_SLOPE", c), self.parameterAsDouble(p, "SYN_FILL_SLOPE", c),
            self.parameterAsDouble(p, "SYN_MAX_WIDTH", c))
        style = ss.road_style(width, shoulder, lanes)
        max_width = self.parameterAsDouble(p, "SYN_MAX_WIDTH", c)
        outer = xyz.reshape(len(centre), 7, 3)[:, [0, 6], :2].reshape(-1, 2)
        known = np.isfinite(ground_index.sample(outer)).reshape(len(centre), 2).all(axis=1)
        short = (daylight.max(axis=1) >= max_width - 1e-6) & known      # no daylight against ground that is there
        def ground_at(points):
            return ground_index.sample(np.asarray(points, float)[:, :2])
        normal = corridor_normals(centre[:, :2])
        edge = width / 2 + shoulder

        # ---- structures: the schedule (CSV and typed), and bridges over the stretches without daylight
        schedule = []
        csv_path = self.parameterAsFile(p, "SYN_STRUCTURES_CSV", c)
        if csv_path:
            try:
                items, notes = read_schedule_csv(csv_path)
            except (OSError, ValueError) as exc:
                raise QgsProcessingException(f"Structure schedule: {exc}") from exc
            for note in notes:
                fb.pushInfo(note)
            schedule += items
        try:
            schedule += read_schedule_text(self.parameterAsString(p, "SYN_STRUCTURES", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        bridge_rows = np.zeros(len(centre), bool)
        auto = np.zeros(len(centre), bool)
        if self.parameterAsBool(p, "SYN_BRIDGES", c) and short.any():
            edges = np.flatnonzero(np.diff(np.concatenate([[0], short.astype(int), [0]])))
            for first_row, after in zip(edges[::2], edges[1::2]):
                auto[max(first_row - 1, 0):min(after + 1, len(centre))] = True
        given = {}                          # first row of a scheduled bridge -> its item
        for item in schedule:
            if item["kind"] == "bridge":
                end = item["end"] if item["end"] is not None else item["station"]
                rows = (stations >= min(item["station"], end)) & (stations <= max(item["station"], end))
                if not rows.any():
                    continue
                # a scheduled bridge replaces the automatic one it overlaps
                labels = np.cumsum(np.diff(np.concatenate([[0], auto.astype(int)])) == 1) * auto
                for label in set(labels[rows & auto]) - {0}:
                    auto[labels == label] = False
                bridge_rows |= rows
                given[int(np.flatnonzero(rows)[0])] = item
        bridge_rows |= auto
        quads = len(faces) // 2
        quad_row = np.arange(quads) // 6
        quad_col = np.arange(quads) % 6
        quad_bridge = bridge_rows[quad_row] & bridge_rows[quad_row + 1]
        face_bridge = np.concatenate([quad_bridge, quad_bridge])
        face_slope = np.concatenate([quad_col, quad_col])
        face_slope = (face_slope == 0) | (face_slope == 5)
        road_faces = faces[~(face_bridge & face_slope)]          # the deck keeps the road, the slopes go
        earth_faces = faces[~face_bridge]                       # earthworks and ground cutting leave bridges out

        sid = f"syn{len(meshes) + 1}"
        meshes.append((sid, f"{name} (synthetic corridor)", "design", xyz, road_faces, style, None))
        self._synthetic_extra[sid] = {"station": station, "offset": offset}
        fb.pushInfo(f"Side slopes run out {np.median(daylight):.1f} (median), up to {daylight.max():.1f} beyond the shoulders.")

        builder = MeshBuilder()
        channels = []
        notes = []
        views = self._synthetic_views
        if bridge_rows.any():
            edges = np.flatnonzero(np.diff(np.concatenate([[0], bridge_rows.astype(int), [0]])))
            default_span = self.parameterAsDouble(p, "SYN_BRIDGE_SPAN", c)
            for first_row, after in zip(edges[::2], edges[1::2]):
                rows = np.arange(first_row, after)
                if len(rows) < 2:
                    continue
                item = next((given[k] for k in given if first_row <= k < after), None) or {}
                middle_row = rows[len(rows) // 2]
                self._synthetic_labels.append((f"Bridge {stations[rows[0]]:.0f}–{stations[rows[-1]]:.0f}",
                                               tuple(centre[middle_row])))
                text, _piers = bridge(builder, centre, stations, normal, rows, 2 * edge + 1.0,
                                      item.get("span") or default_span, ground_at,
                                      spans=item.get("spans"), piers=item.get("piers"))
                notes.append(text)
                middle = rows[len(rows) // 2]
                n = normal[middle]
                views.append((f"Bridge {stations[rows[0]]:.0f}–{stations[rows[-1]]:.0f}", (n[0], n[1], 0.25),
                              centre[middle] - [0, 0, 5.0], max(1.2 * (stations[rows[-1]] - stations[rows[0]]), 60.0)))
        culverts = []
        for item in schedule:
            if item["kind"] == "bridge":
                continue
            s = item["station"]
            if not stations[0] <= s <= stations[-1]:
                notes.append(f"{item['id']} at {s:.0f} is outside the corridor; not drawn.")
                continue
            i = int(np.clip(np.searchsorted(stations, s), 1, len(stations) - 1))
            if bridge_rows[i - 1] and bridge_rows[i]:
                notes.append(f"{item['id']} at {s:.0f} is under a bridge; not drawn.")
                continue
            t = (s - stations[i - 1]) / max(stations[i] - stations[i - 1], 1e-9)
            point = centre[i - 1] + (centre[i] - centre[i - 1]) * t
            reach = daylight[i - 1] + (daylight[i] - daylight[i - 1]) * t
            try:
                notes.append(culvert(builder, item, point, normal[i - 1], edge + reach[0] + 0.5, edge + reach[1] + 0.5,
                                     ground_at, record=channels))
                size = (f"{item['cells']} x {item['span']:g} m pipe" if item["kind"] == "pipe"
                        else f"{item['cells']} x {item['span']:g} x {item['rise']:g} m box")
                self._synthetic_labels.append((f"{item['id']} {size}", (point[0], point[1], point[2])))
                culverts.append(s)
                n = normal[i - 1]
                along = np.array([n[1], -n[0]])
                end = channels[-1]                              # look at the outlet end along the barrel
                outward = (end["right"] - end["left"]) / max(np.linalg.norm(end["right"] - end["left"]), 1e-9)
                views.append((f"{item['id']} ({s:.0f})",
                              (outward[0] + along[0] * 0.35, outward[1] + along[1] * 0.35, 0.4),
                              (end["right"][0], end["right"][1], end["z_right"] + 1.0),
                              max(2.5 * end["width"], 30.0)))
            except ValueError as exc:
                notes.append(str(exc))
        for note in notes:
            (fb.pushWarning if "does not fit" in note else fb.pushInfo)(note)
        sxyz, sfaces = builder.result()
        if len(sfaces):
            if self._synthetic_structures is not None:
                oxyz, ofaces = self._synthetic_structures
                sxyz, sfaces = np.vstack([oxyz, sxyz]), np.vstack([ofaces, sfaces + len(oxyz)])
            self._synthetic_structures = (sxyz, sfaces)
        if channels and self.parameterAsBool(p, "SYN_CHANNELS", c):
            for index, mesh in enumerate(meshes):
                if mesh[2] in ("surface", "raster"):
                    cxyz, cfaces, lowered = carve_channels(mesh[3], mesh[4], channels)
                    meshes[index] = (*mesh[:3], cxyz, cfaces, *mesh[5:])
            fb.pushInfo(f"Channels cut into the ground at {len(channels)} culvert{'s' if len(channels) > 1 else ''} "
                        "(barrel width, banks 1V:1.5H, beds rising 1:20 for 30 m beyond the headwalls).")
        self._synthetic_cuts.append((xyz, earth_faces, max(width / 4.0, 1.0)))
        # dips in the ground under fill with no structure near them
        road = centre[:, 2]
        surveyed = ground_at(centre)
        candidates = [s for s in low_points(stations, road, surveyed)
                      if not bridge_rows[int(np.clip(np.searchsorted(stations, s), 0, len(stations) - 1))]
                      and all(abs(s - k) > 50.0 for k in culverts)]
        if candidates:
            fb.pushWarning("Possible crossings, not sized (ground low point under 1.5 m or more of fill with no culvert or "
                           "bridge within 50 m): " + ", ".join(f"{s:.0f}" for s in candidates[:20])
                           + (" …" if len(candidates) > 20 else "") + ". Check them in the drainage design.")

        def volumes(face_set):
            try:
                depth_xyz, depth_faces, _stats = depth_tin((xyz, face_set), ground)
                return volume_summary(depth_xyz, depth_faces)
            except ValueError:
                return None

        if short.any():
            edges = np.flatnonzero(np.diff(np.concatenate([[0], short.astype(int), [0]])))
            runs = [f"{stations[a]:.0f}–{stations[b - 1]:.0f}" for a, b in zip(edges[::2], edges[1::2])]
            fb.pushWarning("Side slopes do not reach the ground within the longest slope at " + ", ".join(runs[:12])
                           + (" …" if len(runs) > 12 else "") + ": a deep crossing or high embankment, likely a structure "
                           "at design stage." + (" Drawn as bridges." if self.parameterAsBool(p, "SYN_BRIDGES", c) else ""))
        whole = volumes(earth_faces if bridge_rows.any() else faces)
        if whole:
            fb.pushInfo(f"Synthetic corridor {stations[0]:.0f}–{stations[-1]:.0f}: about {whole['cut_volume']:,.0f} cut and "
                        f"{whole['fill_volume']:,.0f} fill (cubic output units, concept section, no bulking"
                        + (", bridges left out" if bridge_rows.any() else "") + ").")
        if short.any() and not bridge_rows.any():
            rows = faces // 7
            earthworks = volumes(faces[~short[rows].any(axis=1)])
            if earthworks:
                fb.pushInfo(f"Leaving the stretches without daylight out: about {earthworks['cut_volume']:,.0f} cut and "
                            f"{earthworks['fill_volume']:,.0f} fill.")

    def _cut_synthetic(self, meshes, unders):
        """Cut the ground under every synthetic corridor, as for a designed surface drawn over it."""
        cuts = self._synthetic_cuts
        if not cuts:
            return
        offsets = np.cumsum([0] + [len(x) for x, _f, _t in cuts[:-1]])
        footprint = (np.vstack([x for x, _f, _t in cuts]), np.vstack([f + o for (_x, f, _t), o in zip(cuts, offsets)]))
        footprint_index = TinIndex(*footprint)
        for index, mesh in enumerate(meshes):
            if mesh[2] not in ("surface", "raster"):
                continue
            gxyz, gfaces = mesh[3], mesh[4]
            for xyz, faces, target in cuts:
                gxyz, gfaces = refine_near(gxyz, gfaces, (xyz, faces), target)
                gxyz, gfaces = refine_edges(gxyz, gfaces, (xyz, faces))
            under = footprint_index.sample(gxyz)
            inside = np.isfinite(under)
            removed = inside[gfaces].all(axis=1)
            snapped = gxyz.copy()
            snapped[inside, 2] = np.minimum(snapped[inside, 2], under[inside])
            meshes[index] = (*mesh[:3], snapped, gfaces, *mesh[5:])
            if removed.any():
                uxyz, ufaces, _u = _compact(gxyz, gfaces[removed])
                unders.append((mesh[0], mesh[1], uxyz, ufaces, mesh[5]))
                keep = gfaces[~removed]
                if len(keep):
                    kxyz, keep, _used = _compact(snapped, keep)
                    meshes[index] = (*mesh[:3], kxyz, keep, *mesh[5:])

    # ------------------------------------------------------------------ run
    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if path and not os.path.isfile(path):
            raise QgsProcessingException("The LandXML file does not exist.")
        method, crs, params, doc = self._crs_and_method(p, c, fb, path)
        surfaces = parse_surface_list(self.parameterAsString(p, "SURFACES", c))
        rasters = self.parameterAsLayerList(p, "RASTERS", c)
        if not surfaces and not rasters:
            if path and read_catalog(path).surfaces:
                surfaces = [(name, "", "") for name in read_catalog(path).surfaces]
            elif not path:
                raise QgsProcessingException("Choose LandXML surfaces or DEM rasters to render.")
        if surfaces and not path:
            raise QgsProcessingException("LandXML surfaces need the LandXML file.")
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("3D scenes need an output CRS in metres, feet or US survey feet.")
        chosen = VERTICAL_UNITS[self.parameterAsEnum(p, "VERTICAL_UNIT", c)][1]
        vertical = (doc.vertical_unit if doc is not None else None) or chosen or horizontal or "meter"
        vertical_factor = (LINEAR_UNITS[vertical.lower()][0] / LINEAR_UNITS[horizontal.lower()][0]
                           if horizontal and vertical.lower() in LINEAR_UNITS else 1.0)
        unit_label = LINEAR_UNITS.get(vertical.lower(), (1, "m"))[1]
        intervals = parse_intervals(self.parameterAsString(p, "CONTOURS", c))
        self._axis = None
        self._corridor_name = None
        self._synthetic_extra = {}
        self._synthetic_structures = None
        self._synthetic_views = []
        self._synthetic_cuts = []
        self._synthetic_labels = []
        area = self._area(p, c, path, method, params, crs, fb)
        corridor_axis = self._axis
        budget = int(self.parameterAsDouble(p, "MAX_TRIANGLES", c))

        # ---- surfaces in output coordinates
        meshes = []        # (id, name, role, xyz, faces, style, depth)
        names = read_catalog(path).surfaces if path else []
        raw_box = _inverse_box(area.box, method, params) if area else None
        if raw_box:
            raw_box = (min(raw_box[0], raw_box[2]), min(raw_box[1], raw_box[3]), max(raw_box[0], raw_box[2]), max(raw_box[1], raw_box[3]))
        loaded = {}

        def load(name):
            if name not in loaded:
                try:
                    xyz, faces, meta = read_tin(path, name, cancel=fb.isCanceled, bounds=raw_box)
                except ValueError as exc:
                    raise QgsProcessingException(f"{name}: {exc}") from exc
                xyz = transform_vertices(xyz, method=method, **params)
                if area:
                    faces = faces[area.keep_faces(xyz, faces)]
                    if not len(faces):
                        raise QgsProcessingException(f"Surface '{name}' has no triangles in the area.")
                    xyz, faces, _used = _compact(xyz, faces)
                loaded[name] = (xyz, faces)
            return loaded[name]

        cutouts = {}      # existing surface → designs drawn over it
        road_without_ground = []   # finished roads with no 'over': they cut through every other LandXML surface
        # A finished road drawn without 'over' is set over the first other surface, so the ground is cut
        # away to its daylight lines instead of covering it in cuts.
        plain_names = [requested for requested, style_text, over in surfaces if not style_text.strip().lower().startswith("road")]
        surfaces = [(requested, style_text, over or (plain_names[0] if style_text.strip().lower().startswith("road") and plain_names else ""))
                    for requested, style_text, over in surfaces]
        for k, (requested, style_text, over) in enumerate(surfaces):
            name = match_name(names, requested, "surface")
            xyz, faces = load(name)
            try:
                style = ss.parse_style(style_text, "elevation") if style_text else \
                    {"attribute": "elevation", "breaks": [], "colors": [SURFACE_COLOURS[k % len(SURFACE_COLOURS)]], "labels": ["Surface"]}
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
            depth = None
            if style["attribute"] == "depth" or over:
                if not over:
                    raise QgsProcessingException(f"'{requested} = depth' needs 'over <existing surface>'.")
                exyz, efaces = load(match_name(names, over, "surface"))
                depth = xyz[:, 2] - sample_tin((exyz, efaces), xyz)
            meshes.append((f"s{k + 1}", name, "design" if depth is not None or style["attribute"] == "road" else "surface",
                           xyz, faces, style, depth))
            if over:
                cutouts.setdefault(match_name(names, over, "surface"), []).append((xyz, faces))
            elif style["attribute"] == "road":
                road_without_ground.append((xyz, faces))

        # ground under a design drawn over it is left out, and ground points just inside the design's edge
        # take the design level, so the ground cannot show through the design
        for mesh in meshes:
            if mesh[5]["attribute"] != "road" and mesh[2] != "design":
                cutouts.setdefault(mesh[1], []).extend(road_without_ground)
        unders = []        # ground removed under designs, kept for the viewer's before/after switch
        for index, mesh in enumerate(meshes):
            if mesh[1] not in cutouts:
                continue
            base, faces = mesh[3], mesh[4]
            for dxyz, dfaces in cutouts[mesh[1]]:          # ground triangles across a design's edge are split finely,
                base, faces = refine_edges(base, faces, (dxyz, dfaces))   # so the cut edge is not jagged
            mesh = (*mesh[:3], base, faces, *mesh[5:])
            xyz = base.copy()
            removed = np.zeros(len(faces), bool)
            for dxyz, dfaces in cutouts[mesh[1]]:
                under = sample_tin((dxyz, dfaces), mesh[3])
                inside = np.isfinite(under)
                removed |= inside[faces].all(axis=1)
                # ground above the design comes down to it (so it cannot show through); ground below stays,
                # so a wall's foot does not pull the ground up into spikes
                xyz[inside, 2] = np.minimum(xyz[inside, 2], under[inside])
            if removed.any():
                uxyz, ufaces, _u = _compact(mesh[3], faces[removed])
                unders.append((mesh[0], mesh[1], uxyz, ufaces, mesh[5]))
            keep = faces[~removed]
            if len(keep):
                xyz, keep, _used = _compact(xyz, keep)
                meshes[index] = (*mesh[:3], xyz, keep, *mesh[5:])
        try:
            raster_style = ss.parse_style(self.parameterAsString(p, "RASTER_STYLE", c), "elevation")
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        for k, layer in enumerate(rasters):
            if area is None:
                e = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance()).transformBoundingBox(layer.extent())
                area = Area((e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()))
            xyz, faces = raster_mesh(layer, crs, area, max(budget // max(len(rasters) + len(meshes), 1), 10_000), fb)
            meshes.append((f"r{k + 1}", layer.name(), "raster", xyz, faces, raster_style, None))
        for order, synthetic_name in enumerate(n.strip() for n in self.parameterAsString(p, "SYNTHETIC", c).split(";")):
            if synthetic_name:
                self._synthetic(p, c, fb, path, method, params, area, meshes, unders, vertical_factor, synthetic_name,
                                first=not order)
        self._cut_synthetic(meshes, unders)
        if not meshes:
            raise QgsProcessingException("Nothing to render in the area.")
        total = sum(len(m[4]) for m in meshes)
        if total > budget:
            fb.pushWarning(f"The scene has {total:,} triangles, above the budget of {budget:,}; the viewer may be slow. "
                           "Choose a smaller area.")

        all_xyz = np.vstack([m[3] for m in meshes])
        if area is None:
            area = Area((all_xyz[:, 0].min(), all_xyz[:, 1].min(), all_xyz[:, 0].max(), all_xyz[:, 1].max()))
        origin = [round(float(all_xyz[:, 0].mean())), round(float(all_xyz[:, 1].mean())), round(float(all_xyz[:, 2].min()))]
        folder = self.parameterAsString(p, "OUTPUT_FOLDER", c)
        title = self.parameterAsString(p, "TITLE", c).strip() or (os.path.splitext(os.path.basename(path))[0] if path else "3D scene")
        writer = SceneWriter(folder, origin, {"horizontal": horizontal or "", "vertical": vertical},
                             {"authid": crs.authid(), "wkt": crs.toWkt()},
                             {"file": os.path.basename(path) if path else "", "surfaces": [m[1] for m in meshes]},
                             self.parameterAsDouble(p, "EXAGGERATION", c))
        writer.scene["title"] = title
        road_path = self._road_path(path, method, params, fb) if path and any(
            m[5]["attribute"] == "road" for m in meshes) else None
        if road_path is None and any(m[5]["attribute"] == "road" and m[0] not in getattr(self, "_synthetic_extra", {})
                                     for m in meshes):
            raise QgsProcessingException("A road style needs an alignment with a vertical profile in the LandXML file.")
        designs = [m[0] for m in meshes if m[2] == "design"]
        for surface_id, name, role, xyz, faces, style, depth in meshes:
            if style["attribute"] == "road" and surface_id in getattr(self, "_synthetic_extra", {}):
                extra = self._synthetic_extra[surface_id]
                station, offset = extra["station"], extra["offset"]
            elif style["attribute"] == "road":
                station, offset = station_offset(xyz, road_path[1][:, :2], road_path[2], max_distance=250.0)
                far = np.isnan(offset)                      # far from the road: verge colours
                station, offset = np.where(far, 0.0, station), np.where(far, 1e6, offset)
            if style["attribute"] == "road":
                slope = np.zeros(len(xyz))
                np.maximum.at(slope, faces.ravel(), np.repeat(ss.triangle_values(xyz, faces, "slope", None, vertical_factor), 3))
                resolved = ss.resolve(style, None, unit_label)
                writer.surface(surface_id, name, role, xyz, faces, ss.road_colors(offset, slope, style), resolved, depth,
                               extra={"station": station, "offset": offset})
                continue
            values = ss.triangle_values(xyz, faces, style["attribute"], depth, vertical_factor)
            resolved = ss.resolve(style, values, unit_label)
            mesh = ss.mesh_colors(xyz, faces, resolved, depth, vertical_factor)
            writer.surface(surface_id, name, role, *mesh[:3], resolved, mesh[3])
        merged = {}                       # one hidden "under" surface per ground, however many designs cut it
        for base_id, base_name, uxyz, ufaces, style in unders:
            if base_id in merged:
                _n, oxyz, ofaces, _s = merged[base_id]
                uxyz, ufaces = np.vstack([oxyz, uxyz]), np.vstack([ofaces, ufaces + len(oxyz)])
            merged[base_id] = (base_name, uxyz, ufaces, style)
        for base_id, (base_name, uxyz, ufaces, style) in merged.items():
            values = ss.triangle_values(uxyz, ufaces, style["attribute"], None, vertical_factor) \
                if style["attribute"] not in ("depth", "road") else None
            resolved = ss.resolve(style, values, unit_label) if values is not None else \
                ss.resolve(ss.parse_style("plain #cfd8c4"), None, unit_label)
            mesh = ss.mesh_colors(uxyz, ufaces, resolved, None, vertical_factor)
            writer.surface(f"{base_id}_under", f"{base_name} (under the design)", "existing_under", *mesh[:3], resolved,
                           visible=False, pair={"before": [f"{base_id}_under"], "after": designs})
        written_paths = set()

        # ---- contours
        for surface_id, name, role, xyz, faces, _style, _depth in meshes:
            interval = intervals.get(name, intervals.get("*", 0.0)) if role != "raster" else \
                self.parameterAsDouble(p, "RASTER_CONTOURS", c)
            if interval <= 0:
                continue
            low, high = xyz[:, 2].min(), xyz[:, 2].max()
            levels = np.arange(math.ceil(low / interval) * interval, high, interval)
            if len(levels) > 400:
                fb.pushWarning(f"{name}: {len(levels)} contour levels; the interval was doubled until there are at most 400.")
                while len(levels) > 400:
                    interval *= 2
                    levels = np.arange(math.ceil(low / interval) * interval, high, interval)
            for level in levels:
                index = abs(round(level / interval) % 5) == 0
                for line in level_lines(xyz, faces, float(level)):
                    writer.line("index_contour" if index else "contour", f"{name} {level:g}", line,
                                {"surface": name, "level": float(level)},
                                color="#6b5a45" if index else "#8f7f6a", width=2 if index else 1, lift=0.05)
        # drape onto the first surface
        # drape on the designs first, then the other surfaces in the order given
        drapes = [TinIndex(m[3], m[4]) for m in meshes if m[2] == "design"] + [TinIndex(m[3], m[4]) for m in meshes if m[2] != "design"]

        def drape_points(points):
            z = np.full(len(points), np.nan)
            for tin in drapes:
                missing = np.isnan(z)
                if not missing.any():
                    break
                z[missing] = sample_tin(tin, points[missing])
            if np.isnan(z).all():
                return None
            fill = np.nanmean(z) if np.isnan(z).any() else 0.0
            return np.column_stack([points[:, 0], points[:, 1], np.where(np.isnan(z), fill, z)])

        size = math.hypot(area.box[2] - area.box[0], area.box[3] - area.box[1])
        step = max(size / 800.0, 0.5)

        # ---- LandXML linework
        if path and self.parameterAsBool(p, "ALIGNMENTS", c) and read_catalog(path).alignments:
            profiled = {name: (start_, stored) for name, start_, _e, stored in centerlines_3d(path, 2.0, None, fb.pushInfo, fb.isCanceled, max_step=5.0)}
            every = self.parameterAsDouble(p, "STATION_LABELS", c)
            for name, _desc, sta0, _sta1, parts, _unsupported, distances in _alignment_parts(path, 2.0):
                pts = merge_parts(parts)
                if len(pts) < 2:
                    continue
                if name in profiled:
                    first_station, stored = profiled[name]
                    line3 = transform_vertices(stored, method=method, **params)
                    along = first_station + np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(stored[:, :2], axis=0).T))])
                    inside = area.keep_points(line3)
                    for run in np.split(np.arange(len(line3)), np.flatnonzero(np.diff(inside.astype(int))) + 1):
                        if inside[run[0]] and len(run) > 1:      # paths for the viewer's drive-through
                            writer.path(name, line3[run], along[run])
                            written_paths.add(name)
                else:
                    plan = transform_vertices(np.array([[x, y, 0.0] for x, y in pts]), method=method, **params)
                    line3 = drape_points(_densify(plan, step))
                    if line3 is None:
                        continue
                for run in _runs_inside(line3, area):
                    writer.line("alignment", name, run, {"alignment": name, "profile": name in profiled},
                                color="#c2185b" if name in profiled else "#7a3b5e", width=3, lift=0.15)
                if every > 0 and sta0 not in (None, ""):
                    plan = transform_vertices(np.array([[x, y, 0.0] for x, y in pts]), method=method, **params)
                    start = float(sta0)
                    raw = np.array(pts, float)
                    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(raw[:, :2], axis=0).T))])
                    first = math.ceil(start / every) * every
                    for station in np.arange(first, start + cum[-1] + 1e-9, every):
                        d = station - start
                        x = np.interp(d, cum, plan[:, 0])
                        y = np.interp(d, cum, plan[:, 1])
                        point = drape_points(np.array([[x, y]]))
                        if point is not None and area.keep_points(point[:, :2])[0]:
                            text = station_label(station, doc.horizontal_unit if doc else None)
                            writer.label(text[:-3] if text.endswith(".00") else text, point[0], 1.0, "station")
        if path and self.parameterAsBool(p, "FEATURE_LINES", c):
            from .landxml.features import read_line_features
            from .landxml.parser import load_document

            records, _warnings = read_line_features(load_document(path).root)
            for record in records:
                points = np.asarray(record.points, float)
                if points.ndim != 2 or len(points) < 2:
                    continue
                if points.shape[1] < 3:
                    points = np.column_stack([points, np.zeros(len(points))])
                    points = transform_vertices(points, method=method, **params)
                    points = drape_points(_densify(points, step))
                    if points is None:
                        continue
                else:
                    points = transform_vertices(points[:, :3], method=method, **params)
                kind = "breakline" if record.kind == "Breakline" else "feature_line"
                for run in _runs_inside(points, area):
                    writer.line(kind, record.name or kind, run, {"code": record.feature_code or ""},
                                color="#a6611a" if kind == "breakline" else "#00897b", width=2, lift=0.1)
        # ---- QGIS vector layers (polygons raised to solids when a height is given)
        extrude_field = self.parameterAsString(p, "EXTRUDE_FIELD", c).strip()
        default_height = self.parameterAsDouble(p, "EXTRUDE_HEIGHT", c)
        solids = []
        for layer in self.parameterAsLayerList(p, "VECTORS", c):
            transform = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())
            rect = QgsCoordinateTransform(crs, layer.crs(), QgsProject.instance()).transformBoundingBox(QgsRectangle(*area.box[:2], *area.box[2:]))
            has_z = QgsWkbTypes.hasZ(layer.wkbType())
            count = 0
            for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                geometry = QgsGeometry(feature.geometry())
                if geometry.isEmpty():
                    continue
                geometry.transform(transform)
                kind = QgsWkbTypes.geometryType(geometry.wkbType())
                parts = []
                if kind == QgsWkbTypes.GeometryType.LineGeometry:
                    parts = [list(part) for part in geometry.constParts()]
                elif kind == QgsWkbTypes.GeometryType.PolygonGeometry:
                    for part in geometry.constParts():
                        parts.append(list(part.exteriorRing()))
                        parts += [list(part.interiorRing(i)) for i in range(part.numInteriorRings())]
                height = default_height
                if extrude_field and extrude_field in feature.fields().names():
                    try:
                        height = float(feature[extrude_field])
                    except (TypeError, ValueError):
                        height = default_height
                if kind == QgsWkbTypes.GeometryType.PolygonGeometry and height > 0:
                    for part in geometry.constParts():
                        ring = np.array([[v.x(), v.y()] for v in part.exteriorRing()], float)
                        if not area.keep_points(ring).any():
                            continue
                        ground = drape_points(ring)
                        base = float(np.nanmin(ground[:, 2])) if ground is not None else origin[2]
                        bxyz, bfaces = extrude(ring, base, height)
                        if len(bfaces):
                            solids.append((bxyz, bfaces + sum(len(x) for x, _f in solids)))
                            count += 1
                    continue
                for vertices in parts:
                    points = np.array([[v.x(), v.y(), v.z() if has_z else 0.0] for v in vertices], float)
                    if len(points) < 2:
                        continue
                    if not has_z or np.isnan(points[:, 2]).all():
                        points = drape_points(_densify(points[:, :2], step))
                        if points is None:
                            continue
                    for run in _runs_inside(points, area):
                        writer.line("vector", layer.name(), run, {"layer": layer.name()}, color="#37474f", width=2, lift=0.2)
                        count += 1
            fb.pushInfo(f"{layer.name()}: {count:,} lines or solids.")
        if solids:
            sxyz = np.vstack([x for x, _f in solids])
            sfaces = np.vstack([f for _x, f in solids])
            style = ss.resolve(ss.parse_style("plain #d9d4c7"), None, unit_label)
            style["classes"][0]["label"] = "Raised polygons"
            writer.surface("solids", "Buildings and structures", "solid", sxyz, sfaces,
                           np.tile(np.array([0xd9, 0xd4, 0xc7], np.uint8), (len(sxyz), 1)), style)
        if getattr(self, "_synthetic_structures", None):
            sxyz, sfaces = self._synthetic_structures
            style = ss.resolve(ss.parse_style("plain #bdb9b0"), None, unit_label)
            style["classes"][0]["label"] = "Concept structures"
            writer.surface("structures", "Structures (concept)", "solid", sxyz, sfaces,
                           np.tile(np.array([0xbd, 0xb9, 0xb0], np.uint8), (len(sxyz), 1)), style)
        # ---- water at given levels
        for part in (self.parameterAsString(p, "WATER", c) or "").split(";"):
            if not part.strip():
                continue
            name, _sep, level_text = part.rpartition("=")
            try:
                level = float(level_text)
            except ValueError as exc:
                raise QgsProcessingException(f"A water level is 'surface = level': {part.strip()}") from exc
            target = next((m for m in meshes if m[1] == match_name([m[1] for m in meshes], name.strip(), "surface")), None)
            wxyz, wfaces = water_mesh(target[3], target[4], level)
            if not len(wfaces):
                fb.pushWarning(f"{target[1]} has no ground below {level:g}; no water drawn.")
                continue
            style = ss.resolve(ss.parse_style("plain #4f9fd6"), None, unit_label)
            style["classes"][0]["label"] = f"Water at {level:g}"
            writer.surface(f"water{len(writer.scene['surfaces'])}", f"Water {level:g} ({target[1]})", "water", wxyz, wfaces,
                           np.tile(np.array([0x4f, 0x9f, 0xd6], np.uint8), (len(wxyz), 1)), style, opacity=0.72, lift=0.02)

        view_text = self.parameterAsString(p, "VIEWS", c)
        focus = np.vstack([m[3] for m in meshes if m[2] == "design"] or [all_xyz])
        low, high = focus.min(axis=0), focus.max(axis=0)
        size = float(np.linalg.norm((high - low)[:2]))
        if corridor_axis is not None and view_text.strip() == DEFAULT_VIEWS:
            # a corridor is seen from its sides and along it, not from a fixed compass direction
            ax, ay = corridor_axis
            view_text = (f"left: {-ay:.3f}, {ax:.3f}, 0.45; right: {ay:.3f}, {-ax:.3f}, 0.45; "
                         f"along: {-ax:.3f}, {-ay:.3f}, 0.25")
            size = 1.3 * max(np.ptp(focus[:, 0] * ax + focus[:, 1] * ay), 1.0)
        if road_path is not None and road_path[0] not in written_paths:
            inside = area.keep_points(road_path[1])
            if inside.sum() > 1:
                writer.path(road_path[0], road_path[1][inside], road_path[2][inside])
        views = parse_views(view_text)
        writer.views(views, target=(low + high) / 2, size=size * (0.8 if corridor_axis is not None else 1.0))
        # long corridors: a side view of every stretch, so each part of the road can be reached in one click
        stretch = self.parameterAsDouble(p, "STRETCH", c)
        for drive_path in writer.scene.get("paths", []):
            if stretch <= 0 or corridor_axis is None:
                break
            stations = np.asarray(drive_path["stations"])
            points = np.asarray(drive_path["points"], float) + writer.origin
            if stations[-1] - stations[0] <= 1.5 * stretch:
                continue
            for a in np.arange(stations[0], stations[-1], stretch):
                b = min(a + stretch, stations[-1])
                chosen = (stations >= a) & (stations <= b)
                if chosen.sum() < 2:
                    continue
                part = points[chosen]
                chord = part[-1, :2] - part[0, :2]
                ax, ay = chord / max(np.hypot(*chord), 1e-9)
                centre = part.mean(axis=0)
                unit = doc.horizontal_unit if doc is not None else horizontal
                name = f"{station_label(a, unit, 2)[:-3]}–{station_label(b, unit, 2)[:-3]}"
                writer.view(name, (-ay, ax, 0.45), centre, 1.3 * max(np.hypot(*chord), 50.0))
        for view_name, direction, target, distance in self._synthetic_views:
            writer.view(view_name, direction, target, distance)
        for text, point in self._synthetic_labels:            # structures get callouts like platforms do
            writer.label(text, point, 2.0, "structure")
        scene = writer.write()
        viewer = write_viewer(os.path.dirname(scene), writer.scene, title=title)
        fb.pushInfo(f"Scene: {len(writer.scene['surfaces'])} surfaces ({total:,} triangles), {len(writer.scene['lines']):,} lines, "
                    f"{len(writer.scene['labels'])} labels. Open {viewer} in a web browser.")
        return {"OUTPUT_FOLDER": os.path.dirname(scene), "SCENE": scene, "VIEWER": viewer}
