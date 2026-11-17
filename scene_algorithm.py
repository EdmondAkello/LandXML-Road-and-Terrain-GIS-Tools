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

from .core import read_tin, transform_vertices
from .grading_algorithm import HORIZONTAL_UNITS
from .landxml import surface_style as ss
from .landxml.catalog import match_name, read_catalog
from .landxml.grading import _inside_many
from .landxml.plan_features import level_lines
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.reports import station_label
from .landxml.scene import SceneWriter, parse_views
from .landxml.scene_viewer import write_viewer
from .landxml.tin_overlay import sample_tin
from .params import number_param
from .processing_common import coordinate_choices
from .project_settings import ProjectSettingsAlgorithm
from .road_features import _alignment_parts, _param_coordinates, centerlines_3d

GROUP = "10. 3D scenes"
GROUP_ID = "scenes_3d"
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
        raise QgsProcessingException(f"Raster '{layer.name()}' does not cover the area.")
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
            "surface). Roads stay lines over the terrain; nothing is cut into it.\n"
            "Views: 'name: dx, dy, dz; ...' (direction from the area towards the camera). Coordinates are in the "
            "output CRS relative to the scene origin.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("INPUT", "LandXML file (optional)", extension="xml", optional=True))
        self.addParameter(QgsProcessingParameterString("SURFACES", "LandXML surfaces and styles", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString("CONTOURS", "Contour intervals (source vertical units)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterMultipleLayers("RASTERS", "DEM rasters", QgsProcessing.SourceType.TypeRaster, optional=True))
        self.addParameter(QgsProcessingParameterString("RASTER_STYLE", "Raster style", defaultValue="elevation"))
        self.addParameter(QgsProcessingParameterNumber("RASTER_CONTOURS", "Raster contour interval (0 = none)",
                                                       QgsProcessingParameterNumber.Type.Double, 0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterExtent("AREA", "Area (map extent)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource("AREA_LAYER", "Area (polygons)",
                                                              [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterString("CORRIDOR", "Corridor: alignment name", defaultValue="", optional=True))
        self.addParameter(number_param("CORRIDOR_WIDTH", "Corridor width either side (output units)", 60.0, 1.0, 1e5, decimals=1))
        for key, label, default in (("ALIGNMENTS", "Alignments", True), ("FEATURE_LINES", "LandXML feature lines and breaklines", True)):
            self.addParameter(QgsProcessingParameterBoolean(key, label, defaultValue=default))
        self.addParameter(number_param("STATION_LABELS", "Station labels every (0 = none)", 100.0, 0.0, 1e6, decimals=1))
        self.addParameter(QgsProcessingParameterMultipleLayers("VECTORS", "Other line or polygon layers",
                                                               QgsProcessing.SourceType.TypeVectorAnyGeometry, optional=True))
        self.addParameter(number_param("MAX_TRIANGLES", "Triangle budget (all surfaces)", 2_000_000, 10_000, 5e7, decimals=0))
        self.addParameter(number_param("EXAGGERATION", "Suggested vertical exaggeration", 1.5, 0.1, 20.0, decimals=2))
        self.addParameter(QgsProcessingParameterString("VIEWS", "Camera views", defaultValue="sw: -0.6, -0.8, 0.55; se: 0.8, -0.5, 0.6"))
        self.addParameter(QgsProcessingParameterString("TITLE", "Title", defaultValue="", optional=True))
        _param_coordinates(self)
        self.addParameter(QgsProcessingParameterEnum("VERTICAL_UNIT", "Vertical unit (when the file does not declare it)",
                                                     options=[n for n, _u in VERTICAL_UNITS], defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "Scene folder"))
        self.addOutput(QgsProcessingOutputFile("SCENE", "scene.json"))
        self.addOutput(QgsProcessingOutputFile("VIEWER", "Interactive 3D viewer (HTML)"))

    # ------------------------------------------------------------------ helpers
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
            parts = _alignment_parts(path, 2.0, name)
            points = [pt for part in (parts[0][4] if parts else []) for pt in part]
            if not points:
                raise QgsProcessingException(f"Alignment '{name}' has no geometry.")
            plan = transform_vertices(np.array([[x, y, 0.0] for x, y in points]), method=method, **params)
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
        area = self._area(p, c, path, method, params, crs, fb)
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
            meshes.append((f"s{k + 1}", name, "design" if depth is not None else "surface", xyz, faces, style, depth))
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
        for surface_id, name, role, xyz, faces, style, depth in meshes:
            values = ss.triangle_values(xyz, faces, style["attribute"], depth, vertical_factor)
            resolved = ss.resolve(style, values, unit_label)
            mesh = ss.mesh_colors(xyz, faces, resolved, depth, vertical_factor)
            writer.surface(surface_id, name, role, *mesh[:3], resolved, mesh[3])

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
        drape = (meshes[0][3], meshes[0][4])

        def drape_points(points):
            z = sample_tin(drape, points)
            if np.isnan(z).all():
                return None
            fill = np.nanmean(z) if np.isnan(z).any() else 0.0
            return np.column_stack([points[:, 0], points[:, 1], np.where(np.isnan(z), fill, z)])

        size = math.hypot(area.box[2] - area.box[0], area.box[3] - area.box[1])
        step = max(size / 800.0, 0.5)

        # ---- LandXML linework
        if path and self.parameterAsBool(p, "ALIGNMENTS", c) and read_catalog(path).alignments:
            profiled = {name: stored for name, _s, _e, stored in centerlines_3d(path, 2.0, None, fb.pushInfo, fb.isCanceled)}
            every = self.parameterAsDouble(p, "STATION_LABELS", c)
            for name, _desc, sta0, _sta1, parts, _unsupported, distances in _alignment_parts(path, 2.0):
                pts = [pt for part in parts for pt in part]
                if len(pts) < 2:
                    continue
                if name in profiled:
                    line3 = transform_vertices(profiled[name], method=method, **params)
                else:
                    plan = transform_vertices(np.array([[x, y, 0.0] for x, y in pts]), method=method, **params)
                    line3 = drape_points(_densify(plan, step))
                    if line3 is None:
                        continue
                for run in _runs_inside(line3, area):
                    writer.line("alignment", name, run, {"alignment": name, "profile": name in profiled},
                                color="#c2185b" if name in profiled else "#7a3b5e", width=3, lift=0.15)
                if every > 0 and sta0 not in (None, "") and len(distances) == len(pts):
                    plan = transform_vertices(np.array([[x, y, 0.0] for x, y in pts]), method=method, **params)
                    start = float(sta0)
                    cum = np.asarray(distances, float)
                    first = math.ceil(start / every) * every
                    for station in np.arange(first, start + cum[-1] + 1e-9, every):
                        d = station - start
                        x = np.interp(d, cum, plan[:, 0])
                        y = np.interp(d, cum, plan[:, 1])
                        point = drape_points(np.array([[x, y]]))
                        if point is not None and area.keep_points(point[:, :2])[0]:
                            writer.label(station_label(station, doc.horizontal_unit if doc else None), point[0], 1.0, "station")
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
        # ---- QGIS vector layers
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
            fb.pushInfo(f"{layer.name()}: {count:,} lines.")

        views = parse_views(self.parameterAsString(p, "VIEWS", c))
        focus = np.vstack([m[3] for m in meshes if m[2] == "design"] or [all_xyz])
        low, high = focus.min(axis=0), focus.max(axis=0)
        writer.views(views, target=(low + high) / 2, size=float(np.linalg.norm((high - low)[:2])))
        scene = writer.write()
        viewer = write_viewer(os.path.dirname(scene), writer.scene, title=title)
        fb.pushInfo(f"Scene: {len(writer.scene['surfaces'])} surfaces ({total:,} triangles), {len(writer.scene['lines']):,} lines, "
                    f"{len(writer.scene['labels'])} labels. Open {viewer} in a web browser.")
        return {"OUTPUT_FOLDER": os.path.dirname(scene), "SCENE": scene, "VIEWER": viewer}
