"""Site-grading tools that work from a design and an existing-ground surface: edge analysis and 3D scene export."""

from __future__ import annotations

import csv
import os

import numpy as np

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
    _design_depth,
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
    _depth_surface(path, design, existing, method, params, fb, existing_path, keep, overlay=False)
    xyz, faces, depth = _design_depth(keep)
    return design, existing, existing_path, existing_doc, transform_vertices(xyz, method=method, **params), faces, depth


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


def _labels(text):
    """'text @ x, y [, lift]; ...' (output coordinates) → [(text, x, y, lift)]."""
    out = []
    for part in (text or "").split(";"):
        if not part.strip():
            continue
        label, at, where = part.rpartition("@")
        try:
            values = [float(value) for value in where.replace(",", " ").split()]
        except ValueError as exc:
            raise QgsProcessingException(f"A label is 'text @ x, y [, lift]': {part.strip()}") from exc
        if not at or len(values) not in (2, 3) or not label.strip():
            raise QgsProcessingException(f"A label is 'text @ x, y [, lift]': {part.strip()}")
        out.append((label.strip(), values[0], values[1], values[2] if len(values) == 3 else 4.0))
    return out


class ExportSceneAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return ExportSceneAlgorithm()

    def name(self):
        return "landxml_export_scene"

    def displayName(self):
        return "Export 3D Scene (Site Grading)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Writes a 3D scene of a graded site for a renderer: scene.json and binary PLY meshes in the output "
            "folder. The design surface and the surrounding existing ground (to the context margin) are meshes "
            "coloured by their surface styles; daylight lines, platform outlines, retained edges and batters, road "
            "centrelines at profile level and labels are added as lines and points, with camera views. Roads are "
            "conceptual and are not burnt into the terrain.\n\n"
            "Surface styles: a preset (elevation, slope, depth or aspect), 'attribute: break, break, ...' with "
            "optional '| colours or palette', 'elevation: equal 9' or 'quantile 6', or 'plain #rrggbb'. Views: "
            "'name: dx, dy, dz; ...', the direction from the site towards the camera. Labels: 'text @ x, y [, lift]; "
            "...' in the output CRS. Coordinates are in the output CRS, relative to the scene origin; the vertical "
            "exaggeration is a suggestion for the renderer. The format is described in the user guide and in "
            "landxml/scene.py; tools/render_scene.py renders it with PyVista.")

    def initAlgorithm(self, config=None):
        from qgis.core import QgsProcessingOutputFile, QgsProcessingParameterBoolean, QgsProcessingParameterFolderDestination

        _design_and_existing_params(self)
        self.addParameter(QgsProcessingParameterString("DESIGN_STYLE", "Design surface style", defaultValue="depth"))
        self.addParameter(QgsProcessingParameterString("CONTEXT_STYLE", "Existing-ground style", defaultValue="plain #cfd8c4"))
        self.addParameter(number_param("CONTEXT_MARGIN", "Existing ground around the design (output units)", 50.0, 0.0, 1e6, decimals=1))
        self.addParameter(number_param("EXAGGERATION", "Suggested vertical exaggeration", 1.5, 0.1, 20.0, decimals=2))
        self.addParameter(QgsProcessingParameterString("VIEWS", "Camera views", defaultValue="sw: -0.6, -0.8, 0.55; se: 0.8, -0.5, 0.6"))
        self.addParameter(number_param("CAMERA_DISTANCE", "Camera distance (output units; 0 = automatic)", 0.0, 0.0, 1e7, decimals=1))
        for key, label in (("PLATFORM_LABELS", "Label platforms with their levels"), ("DAYLIGHT", "Daylight lines"),
                           ("EDGES", "Retained edges and batters"), ("ROADS", "Road centrelines at profile level")):
            self.addParameter(QgsProcessingParameterBoolean(key, label, defaultValue=True))
        self.addParameter(number_param("ROAD_WIDTH", "Road width for ribbons (output units; 0 = centreline only)", 0.0, 0.0, 1000.0, decimals=2))
        self.addParameter(QgsProcessingParameterString("LABELS", "Extra labels", defaultValue="", optional=True))
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "Scene folder"))
        self.addOutput(QgsProcessingOutputFile("SCENE", "scene.json"))

    def processAlgorithm(self, p, c, fb):
        from .core import read_tin
        from .grading_algorithm import outline_polygons, platform_summary
        from .landxml import surface_style as ss
        from .landxml.plan_features import footprint_lines
        from .landxml.scene import SceneWriter, parse_views
        from .landxml.tin_overlay import sample_tin
        from .road_features import centerlines_3d

        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("The scene export requires an output CRS in metres, feet or US survey feet.")
        existing_path, existing_doc = _existing_file(self, p, c, doc, fb)
        units, vertical = _grading_units(self, p, c, [doc] + ([existing_doc] if existing_doc else []), fb, horizontal)
        vertical_factor = LINEAR_UNITS[vertical.lower()][0] / LINEAR_UNITS[horizontal.lower()][0]
        catalog = read_catalog(path)
        design = _resolve(catalog.surfaces, self.parameterAsString(p, "DESIGN_SURFACE", c), "design surface")
        existing = _resolve(read_catalog(existing_path or path).surfaces,
                            self.parameterAsString(p, "EXISTING_SURFACE", c), "existing-ground surface")
        if not design or not existing:
            raise QgsProcessingException("Choose the design surface and the existing-ground surface.")
        try:
            design_style = ss.parse_style(self.parameterAsString(p, "DESIGN_STYLE", c), "depth")
            context_style = ss.parse_style(self.parameterAsString(p, "CONTEXT_STYLE", c), "elevation")
            views = parse_views(self.parameterAsString(p, "VIEWS", c), self.parameterAsDouble(p, "CAMERA_DISTANCE", c) or None)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        extra_labels = _labels(self.parameterAsString(p, "LABELS", c))

        keep = {}
        _depth_surface(path, design, existing, method, params, fb, existing_path, keep, overlay=False)
        raw, faces, depth = _design_depth(keep)
        x0, y0, x1, y1 = raw[:, 0].min(), raw[:, 1].min(), raw[:, 0].max(), raw[:, 1].max()
        margin = self.parameterAsDouble(p, "CONTEXT_MARGIN", c)
        scale = float(params.get("scale", 1.0)) if method == "helmert" else 1.0
        margin_source = margin / scale
        try:
            exyz, efaces, _emeta = read_tin(existing_path or path, existing, cancel=fb.isCanceled,
                                            bounds=(x0 - margin_source, y0 - margin_source, x1 + margin_source, y1 + margin_source))
        except ValueError as exc:
            raise QgsProcessingException(f"{existing}: {exc}") from exc
        # Ground under the design is dropped; ground points just inside its edge take the design level,
        # so the triangles that straddle the edge meet the design instead of showing through it.
        under = sample_tin((raw, faces), exyz)
        inside = np.isfinite(under)
        efaces = efaces[~inside[efaces].all(axis=1)]
        exyz = exyz.copy()
        exyz[inside, 2] = under[inside]
        design_t = transform_vertices(raw, method=method, **params)
        context_t = transform_vertices(exyz, method=method, **params)

        origin = [round(float(design_t[:, 0].mean())), round(float(design_t[:, 1].mean())), round(float(design_t[:, 2].min()))]
        writer = SceneWriter(self.parameterAsString(p, "OUTPUT_FOLDER", c), origin,
                             {"horizontal": horizontal, "vertical": vertical},
                             {"authid": crs.authid(), "wkt": crs.toWkt()},
                             {"file": os.path.basename(path), "design": design, "existing": existing,
                              "existing_file": os.path.basename(existing_path) if existing_path else ""},
                             self.parameterAsDouble(p, "EXAGGERATION", c))
        try:
            for surface_id, name, role, xyz, tri, style, dep in (
                    ("context", existing, "existing", context_t, efaces, context_style, None),
                    ("design", design, "design", design_t, faces, design_style, depth)):
                if not len(tri):
                    continue
                values = ss.triangle_values(xyz, tri, style["attribute"], dep, vertical_factor)
                resolved = ss.resolve(style, values, units.depth)
                mesh_xyz, mesh_faces, rgb, mesh_depth = ss.mesh_colors(xyz, tri, resolved, dep, vertical_factor)
                writer.surface(surface_id, name, role, mesh_xyz, mesh_faces, rgb, resolved, mesh_depth)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

        if self.parameterAsBool(p, "DAYLIGHT", c):
            for k, line in enumerate(footprint_lines(raw, faces, depth), 1):
                writer.line("daylight", f"Daylight {k}", transform_vertices(line, method=method, **params))
        summary = platform_summary(design_t, faces, vertical_factor, outlines=False)
        for item in summary["platforms"]:
            for outer, holes in outline_polygons(design_t, faces[item["members"]], dims=3):
                for ring in [outer, *holes]:
                    writer.line("platform_outline", f"{item['id']} outline", np.vstack([ring, ring[:1]]),
                                {"platform": item["id"], "level": round(item["level_mean"], 3)})
            if self.parameterAsBool(p, "PLATFORM_LABELS", c):
                outer = outline_polygons(design_t, faces[item["members"]], dims=3)[0][0]
                cx, cy = outer[:, 0].mean(), outer[:, 1].mean()
                writer.label(f"{item['id']} RL {item['level_mean']:.2f}", (cx, cy, item["level_mean"]), 3.0, "platform")
        if self.parameterAsBool(p, "EDGES", c):
            try:
                result = edge_analysis(design_t, faces, depth, vertical_factor)
            except ValueError:
                result = {"runs": []}
            for run in result["runs"]:
                if run["type"] in ("retained", "batter"):
                    writer.line(f"{run['type']}_edge", f"{run['type']} {run['kind']} {run['band']}", run["points"],
                                {"side": run["kind"], "height_max": round(run["height_max"], 3),
                                 "length": round(run["length"], 3)})
        if self.parameterAsBool(p, "ROADS", c) and read_catalog(path).alignments:
            width = self.parameterAsDouble(p, "ROAD_WIDTH", c)
            area = (x0 - margin_source, y0 - margin_source, x1 + margin_source, y1 + margin_source)
            for name, start, end, stored in centerlines_3d(path, 2.0, None, fb.pushInfo, fb.isCanceled):
                within = ((stored[:, 0] >= area[0]) & (stored[:, 0] <= area[2]) &
                          (stored[:, 1] >= area[1]) & (stored[:, 1] <= area[3]))
                runs = np.split(np.arange(len(stored)), np.flatnonzero(np.diff(within.astype(int))) + 1)
                for run in runs:           # only the parts of a road inside the scene area
                    if within[run[0]] and len(run) > 1:
                        writer.line("road", name, transform_vertices(stored[run], method=method, **params),
                                    {"station_start": start, "station_end": end, **({"width": width} if width else {})})
        for text, x, y, lift in extra_labels:
            z = sample_tin((design_t, faces), np.array([[x, y]]))[0]
            if not np.isfinite(z):
                z = sample_tin((context_t, efaces), np.array([[x, y]]))[0] if len(efaces) else np.nan
            writer.label(text, (x, y, float(z) if np.isfinite(z) else origin[2]), lift)
        low, high = design_t.min(axis=0), design_t.max(axis=0)
        writer.views(views, target=(low + high) / 2, size=float(np.linalg.norm((high - low)[:2])))
        scene = writer.write()
        fb.pushInfo(f"Scene written: {scene} ({len(writer.scene['surfaces'])} surfaces, {len(writer.scene['lines'])} "
                    f"lines, {len(writer.scene['labels'])} labels, {len(writer.scene['views'])} views).")
        return {"OUTPUT_FOLDER": os.path.dirname(scene), "SCENE": scene}
