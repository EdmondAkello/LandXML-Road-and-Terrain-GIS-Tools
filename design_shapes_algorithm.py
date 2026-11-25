"""Design Surface from Shapes: platforms, basins, pits, stockpiles, terraces and golf features from polygons on a DEM."""

from __future__ import annotations

import html
import os

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProject,
    QgsWkbTypes,
)

from .core import read_tin, transform_vertices
from .earthworks_algorithm import VERTICAL_UNITS
from .grading_algorithm import GROUP, GROUP_ID, HORIZONTAL_UNITS
from .landxml.catalog import read_catalog
from .landxml.tin_overlay import TinIndex
from .processing_common import coordinate_choices
from .processing_widgets import name_param
from .road_features import _param_coordinates
from .landxml import design_shapes as ds
from .landxml.plan_features import footprint_lines
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.writer import FeatureLine, Surface, write_landxml
from .params import number_param
from .project_settings import ProjectSettingsAlgorithm

SHAPES = ["platform", "basin", "pit", "stockpile", "terraces", "fairway", "green", "bunker", "pad", "line"]
WRITER_UNITS = {"meter": "meter", "foot": "foot", "ussurveyfoot": "USSurveyFoot"}
CELL_BUDGET = 250_000            # design grid cells; the cell grows on big sites so the output stays usable
FIELDS = ("shape", "level", "floor", "top", "volume", "cut_slope", "fill_slope", "slope", "bench", "berm", "face",
          "step", "riser", "height", "depth", "window", "order", "name", "width", "crossfall", "bench_height",
          "bench_width", "benches_in", "max_width", "mode")


class DesignShapesAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return DesignShapesAlgorithm()

    def name(self):
        return "landxml_design_shapes"

    def displayName(self):
        return "Design Surface from Shapes (Platforms, Basins, Pits, Stockpiles, Terraces)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Builds a design (finished-level) surface on a DEM from polygons, each a shape with its own parameters, and "
            "writes it to LandXML with the existing ground, so the grading tools, the 3D scene and Civil 3D can use it.\n\n"
            "Each polygon's <b>shape</b> field is one of: platform (a pad at <i>level</i> with <i>cut_slope</i> and "
            "<i>fill_slope</i> batters), basin (an excavation to <i>floor</i> with side <i>slope</i>), pit (benches "
            "<i>bench</i> high with <i>berm</i> wide berms and faces at <i>face</i> H:V, down to <i>floor</i>), stockpile "
            "(a pile or mound to <i>top</i>, or holding <i>volume</i>, sides at <i>slope</i>, built in lifts of <i>bench</i> "
            "with <i>berm</i> wide berms when both are given, as a waste dump), terraces (benches every "
            "<i>step</i> with risers taking <i>riser</i> of each step), fairway (ground eased over <i>window</i>), green "
            "(raised <i>height</i> and level, sides at <i>slope</i>) and bunker (<i>depth</i> below its rim, sides at "
            "<i>slope</i>). <b>pad</b> is a polygon with a level at every vertex (its Z values, as a Civil 3D feature "
            "line; a sloped or twisted platform) with <i>cut_slope</i> and <i>fill_slope</i> batters, benched every "
            "<i>bench_height</i> with <i>bench_width</i> benches when both are given. <b>line</b> is a line with a level "
            "at every vertex (a ramp, haul road, bund crest, swale or channel invert) with a top <i>width</i> (falling "
            "<i>crossfall</i> % each side) and the same batters; <i>mode</i> cut only excavates, fill only builds. "
            "The Grading Editor (toolbar) draws and edits pads and lines on the map with live cut and fill. "
            "Missing fields or values take the defaults below; slopes are horizontal per 1 vertical. "
            "Shapes are applied in the <i>order</i> field's order (else the layer's), so a later shape works on the "
            "result of the earlier ones.\n\n"
            "Side slopes run out until they meet the ground. Outputs: a LandXML with 'Existing ground' and 'Design' TIN "
            "surfaces, the shape outlines and the daylight lines as feature lines; the design as a GeoTIFF; and an HTML "
            "report of cut and fill for each shape.\n\n"
            "The existing ground is a DEM (any raster GDAL reads, in a projected CRS in metres or feet) or a TIN surface "
            "in a LandXML file: choose the file, the surface and the coordinate interpretation and output CRS, as in the "
            "other tools. The elevation unit is taken from the DEM or the file; when neither declares it, choose it."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("GROUND", "Existing ground: DEM (or leave empty and use a LandXML TIN)",
                                                            optional=True))
        self.addParameter(QgsProcessingParameterFile("INPUT", "Existing ground: LandXML file", optional=True,
                                                     behavior=QgsProcessingParameterFile.Behavior.File,
                                                     fileFilter="LandXML (*.xml *.landxml)"))
        self.addParameter(name_param("GROUND_SURFACE", "Existing ground: LandXML surface", "surfaces"))
        self.addParameter(QgsProcessingParameterEnum(
            "VERTICAL_UNIT", "Elevation unit — choose when the DEM or the file does not declare it",
            options=[name for name, _unit in VERTICAL_UNITS], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource("SHAPES", "Shapes (polygons, or lines for graded lines, "
                                                              "with a 'shape' field)",
                                                              [QgsProcessing.SourceType.TypeVectorPolygon,
                                                               QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource("LINES", "Graded lines (lines with a level at every vertex)",
                                                              [QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterEnum("DEFAULT_SHAPE", "Shape where the 'shape' field is missing or blank",
                                                     options=SHAPES, defaultValue=0))
        self.addParameter(number_param("CUT_SLOPE", "Default cut slope (H per 1 V)", 1.5, 0.05, 50.0, decimals=2))
        self.addParameter(number_param("FILL_SLOPE", "Default fill slope (H per 1 V)", 2.0, 0.05, 50.0, decimals=2))
        self.addParameter(number_param("SLOPE", "Default slope for basins, stockpiles, greens, bunkers (H per 1 V)", 2.0, 0.05, 50.0, decimals=2))
        self.addParameter(number_param("BENCH", "Default pit bench height", 10.0, 0.5, 100.0, decimals=2))
        self.addParameter(number_param("BERM", "Default pit berm width", 8.0, 0.0, 100.0, decimals=2))
        self.addParameter(number_param("FACE", "Default pit face slope (H per 1 V)", 0.5, 0.0, 10.0, decimals=2))
        self.addParameter(number_param("STEP", "Default terrace step height", 2.0, 0.1, 50.0, decimals=2))
        self.addParameter(number_param("CELL", "Design grid cell (0 = auto)", 0.0, 0.0, 1000.0, decimals=2))
        self.addParameter(number_param("MARGIN", "Margin around the shapes for side slopes", 50.0, 0.0, 10000.0, decimals=1))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT_XML", "LandXML (existing ground, design, feature lines)",
                                                                fileFilter="LandXML (*.xml)"))
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT_DEM", "Design surface (GeoTIFF)", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("REPORT", "Volumes report", fileFilter="HTML (*.html)", optional=True))
        _param_coordinates(self, output_optional=True)
        from .compat import mark_advanced
        mark_advanced(self, ("METHOD", "DX", "DY", "ROTATION", "SCALE", "SOURCE_CRS", "BENCH", "BERM", "FACE", "STEP",
                             "CELL", "MARGIN"))
        for key, label in (("CUT", "Cut volume"), ("FILL", "Fill volume"), ("SHAPE_COUNT", "Shapes built")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    # ------------------------------------------------------------------ run
    def processAlgorithm(self, p, c, fb):
        crs, unit, sample, native, ground_name = self._ground(p, c, fb)
        shapes = self._shapes(p, c, crs, fb)
        if not shapes:
            raise QgsProcessingException("The shapes layers have no polygons or lines.")
        margin = self.parameterAsDouble(p, "MARGIN", c)
        xs = np.concatenate([s["ring"][:, 0] for s in shapes])
        ys = np.concatenate([s["ring"][:, 1] for s in shapes])
        margin = max(margin, max((float(s["values"].get("width", 0) or 0) / 2 for s in shapes), default=0.0))
        box = (xs.min() - margin, ys.min() - margin, xs.max() + margin, ys.max() + margin)
        cell = self.parameterAsDouble(p, "CELL", c) or max(min(native, 2.0 / LINEAR_UNITS[unit.lower()][0]),
                                                            float(np.sqrt((box[2] - box[0]) * (box[3] - box[1]) / CELL_BUDGET)))
        n_cols, n_rows = max(int(round((box[2] - box[0]) / cell)), 2), max(int(round((box[3] - box[1]) / cell)), 2)
        grid = ds.Grid(np.zeros((n_rows, n_cols)), box[0], box[3], cell)
        grid.z = sample(grid.xy).reshape(n_rows, n_cols)
        if np.isnan(grid.z).any():
            raise QgsProcessingException(f"{ground_name} does not cover the shapes and their margin; move the shapes or reduce "
                                         "the margin.")
        fb.pushInfo(f"Design grid: {grid.z.shape[1]} × {grid.z.shape[0]} cells of {cell:g} {unit} over the shapes and a "
                    f"{margin:g} margin.")
        design, rows = grid, []
        for k, shape in enumerate(shapes):
            if fb.isCanceled():
                raise QgsProcessingException("Cancelled.")
            try:
                after = self._apply(design, shape, p, c)
            except ValueError as error:
                raise QgsProcessingException(f"{shape['label']}: {error}") from error
            cut, fill = ds.volumes(design, after)
            rows.append(dict(shape, cut=cut, fill=fill))
            fb.pushInfo(f"{shape['label']}: cut {cut:,.0f}, fill {fill:,.0f}.")
            design = after
            fb.setProgress(80.0 * (k + 1) / len(shapes))
        total_cut, total_fill = ds.volumes(grid, design)

        # ---- outputs
        from .landxml.grading_objects import _tins
        (eg_points, eg_faces), (fg_points, fg_faces) = _tins(grid, design, 0.02)    # one adaptive mesh for both
        full_points, full_faces = design.tin()
        depth = design.z.ravel() - grid.z.ravel()
        lines = [FeatureLine(f"{s['label']} {'line' if s['kind'] == 'line' else 'outline'}", self._outline(s, design))
                 for s in shapes]
        changed = np.abs(depth) > 1e-6
        if changed.any():
            for k, line in enumerate(footprint_lines(full_points, full_faces, depth[_used(design)], tolerance=0.01,
                                                     min_length=cell * 2)):
                lines.append(FeatureLine(f"Daylight {k + 1}", [tuple(v) for v in np.asarray(line)[:, :3]]))
        epsg = _epsg(crs)
        xml = self.parameterAsFileOutput(p, "OUTPUT_XML", c)
        write_landxml(xml, [], linear_unit=WRITER_UNITS[unit.lower()], vertical_unit=WRITER_UNITS[unit.lower()], epsg=epsg,
                      surfaces=[Surface("Existing ground", eg_points.tolist(), eg_faces.tolist(), f"Resampled from {ground_name}"),
                                Surface("Design", fg_points.tolist(), fg_faces.tolist(), "Design Surface from Shapes")],
                      feature_lines=lines)
        result = {"OUTPUT_XML": xml, "CUT": total_cut, "FILL": total_fill, "SHAPE_COUNT": len(shapes)}
        dem = self.parameterAsOutputLayer(p, "OUTPUT_DEM", c)
        if dem:
            from .core import write_geotiff
            write_geotiff(dem, np.where(np.isfinite(design.z), design.z, -9999.0), design.x0, design.y0, cell, wkt=crs.toWkt())
            result["OUTPUT_DEM"] = dem
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            with open(report, "w", encoding="utf-8") as stream:
                stream.write(_report(rows, total_cut, total_fill, unit, cell, ground_name))
            result["REPORT"] = report
        fb.pushInfo(f"Total cut {total_cut:,.0f}, fill {total_fill:,.0f} (cubic {unit}s, bank volumes); written to {xml}.")
        return result

    # ------------------------------------------------------------------ pieces
    def _shapes(self, p, c, crs, fb):
        sources = [self.parameterAsSource(p, key, c) for key in ("SHAPES", "LINES")]
        sources = [s for s in sources if s is not None]
        if not sources:
            raise QgsProcessingException("Choose the shapes layer (or a graded lines layer).")
        shapes = []
        for number, source in enumerate(sources):
            shapes += self._source_shapes(source, p, c, crs, fb, number * 1_000_000)
        shapes.sort(key=lambda s: (s["order"], s["index"]))
        return shapes

    def _source_shapes(self, source, p, c, crs, fb, offset):
        transform = QgsCoordinateTransform(source.sourceCrs(), crs, QgsProject.instance())
        names = {name.lower(): name for name in source.fields().names()}
        default = SHAPES[self.parameterAsEnum(p, "DEFAULT_SHAPE", c)]
        shapes = []
        for index, feature in enumerate(source.getFeatures(QgsFeatureRequest())):
            geometry = feature.geometry()
            gtype = QgsWkbTypes.geometryType(geometry.wkbType())
            if geometry.isEmpty() or gtype not in (QgsWkbTypes.GeometryType.PolygonGeometry,
                                                   QgsWkbTypes.GeometryType.LineGeometry):
                continue
            geometry.transform(transform)
            has_z = QgsWkbTypes.hasZ(geometry.wkbType())
            values = {}
            for key in FIELDS:
                if key in names:
                    value = feature[names[key]]
                    if value not in (None, "") and str(value) != "NULL":
                        values[key] = value
            is_line = gtype == QgsWkbTypes.GeometryType.LineGeometry
            kind = str(values.get("shape", "line" if is_line else default)).strip().lower()
            if is_line and kind != "line":
                raise QgsProcessingException(f"Feature {feature.id()}: a line can only be a 'line' shape.")
            if kind not in SHAPES:
                raise QgsProcessingException(f"Feature {feature.id()}: unknown shape '{kind}'. Use one of {', '.join(SHAPES)}.")
            for part_number, part in enumerate(geometry.constParts()):
                curve = part if is_line else part.exteriorRing()
                ring = np.array([[v.x(), v.y(), v.z() if has_z else np.nan] for v in curve.vertices()], float)
                if kind in ("pad", "line") and not np.isfinite(ring[:, 2]).all():
                    if "level" in values:
                        ring[:, 2] = np.where(np.isfinite(ring[:, 2]), ring[:, 2], float(values["level"]))
                    else:
                        raise QgsProcessingException(f"Feature {feature.id()}: a {kind} needs a level (Z) at every vertex, "
                                                     "or a 'level' value.")
                if not is_line and part.numInteriorRings():
                    fb.pushWarning(f"Feature {feature.id()}: holes are not used; the outer ring is the shape.")
                label = str(values.get("name") or f"{kind} {feature.id()}") + (f" ({part_number + 1})" if part_number else "")
                shapes.append({"kind": kind, "ring": ring, "values": values, "label": label,
                               "order": float(values.get("order", index + offset)), "index": index + offset})
        return shapes

    def _ground(self, p, c, fb):
        """(crs, horizontal unit, sampler of levels in horizontal units, native spacing, name) of the existing ground."""
        layer = self.parameterAsRasterLayer(p, "GROUND", c)
        path = self.parameterAsFile(p, "INPUT", c)
        chosen = VERTICAL_UNITS[self.parameterAsEnum(p, "VERTICAL_UNIT", c)][1]
        if layer is not None and path:
            raise QgsProcessingException("Choose the existing ground as a DEM or as a LandXML surface, not both.")
        if layer is not None:
            from osgeo import gdal

            crs = layer.crs()
            unit = HORIZONTAL_UNITS.get(crs.mapUnits())
            if unit is None:
                raise QgsProcessingException("The DEM must be in a projected CRS in metres or feet.")
            dataset = gdal.Open(layer.source())
            if dataset is None:
                raise QgsProcessingException(f"Could not open the DEM '{layer.name()}'.")
            transform = dataset.GetGeoTransform()
            if transform[2] or transform[4]:
                raise QgsProcessingException("The DEM is rotated; warp it to a north-up grid first.")
            band = dataset.GetRasterBand(1)
            declared = (band.GetUnitType() or "").strip().lower()
            aliases = {"m": "meter", "metre": "meter", "meters": "meter", "metres": "meter", "ft": "foot", "feet": "foot",
                       "us survey foot": "USSurveyFoot", "ussurveyfoot": "USSurveyFoot"}
            level_unit = aliases.get(declared, declared) or chosen
            if not level_unit or level_unit.lower() not in LINEAR_UNITS:
                raise QgsProcessingException("The DEM does not declare its elevation unit; choose the Elevation unit.")
            factor = LINEAR_UNITS[level_unit.lower()][0] / LINEAR_UNITS[unit.lower()][0]
            cache = {}

            def sample(points):
                box = (points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max())
                if box not in cache:
                    cache.clear()
                    cache[box] = self._read_ground(dataset, band, transform, box, factor)
                return cache[box].sample(points)

            return crs, unit, sample, abs(transform[1]), f"DEM '{layer.name()}'"
        if not path:
            raise QgsProcessingException("Choose the existing ground: a DEM, or a LandXML file and its surface.")
        if not os.path.isfile(path):
            raise QgsProcessingException("The LandXML file does not exist.")
        method, crs, _source, params, doc = coordinate_choices(self, p, c, fb, path)
        unit = HORIZONTAL_UNITS.get(crs.mapUnits())
        if unit is None:
            raise QgsProcessingException("Choose an output CRS in metres or feet.")
        surfaces = read_catalog(path).surfaces
        wanted = self.parameterAsString(p, "GROUND_SURFACE", c).strip()
        if not wanted:
            if len(surfaces) != 1:
                raise QgsProcessingException(f"Choose the existing-ground surface: {', '.join(surfaces) or 'none in the file'}.")
            wanted = surfaces[0]
        if wanted not in surfaces:
            raise QgsProcessingException(f"Surface '{wanted}' is not in the file: {', '.join(surfaces)}.")
        vertical = doc.vertical_unit or chosen
        if not vertical or vertical.lower() not in LINEAR_UNITS:
            raise QgsProcessingException("The file does not declare its elevation unit; choose the Elevation unit.")
        if doc.vertical_unit and chosen and LINEAR_UNITS[doc.vertical_unit.lower()] != LINEAR_UNITS[chosen.lower()]:
            raise QgsProcessingException(f"The file declares '{doc.vertical_unit}', but '{chosen}' was chosen.")
        try:
            xyz, faces, _meta = read_tin(path, wanted, cancel=fb.isCanceled)
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error
        xyz = np.array(transform_vertices(xyz, method=method, **params), float)
        xyz[:, 2] *= LINEAR_UNITS[vertical.lower()][0] / LINEAR_UNITS[unit.lower()][0]
        index = TinIndex(xyz, faces)
        edges = np.linalg.norm(xyz[faces[:, 0], :2] - xyz[faces[:, 1], :2], axis=1)
        return crs, unit, index.sample, float(np.median(edges)) if len(edges) else 1.0, f"surface '{wanted}'"

    def _read_ground(self, dataset, band, transform, box, to_horizontal):
        x0, cx, _r1, y0, _r2, cy = transform
        col0 = max(int((box[0] - x0) / cx) - 2, 0)
        col1 = min(int((box[2] - x0) / cx) + 3, dataset.RasterXSize)
        row0 = max(int((y0 - box[3]) / -cy) - 2, 0)
        row1 = min(int((y0 - box[1]) / -cy) + 3, dataset.RasterYSize)
        if col1 <= col0 + 1 or row1 <= row0 + 1:
            raise QgsProcessingException("The shapes lie outside the DEM.")
        z = band.ReadAsArray(col0, row0, col1 - col0, row1 - row0).astype(float)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            z[z == nodata] = np.nan
        return ds.Grid(z * to_horizontal, x0 + col0 * cx, y0 + row0 * cy, cx)

    def _apply(self, grid, shape, p, c):
        v = shape["values"]

        def number(key, default_param=None, default=None):
            if key in v:
                return float(v[key])
            if default_param:
                return self.parameterAsDouble(p, default_param, c)
            if default is None:
                raise ValueError(f"a {shape['kind']} needs a '{key}' value.")
            return default

        kind, ring3 = shape["kind"], shape["ring"]
        ring = ring3[:, :2]
        if kind in ("pad", "line"):
            common = dict(cut_slope=number("cut_slope", "CUT_SLOPE"), fill_slope=number("fill_slope", "FILL_SLOPE"),
                          bench_height=number("bench_height", default=0.0), bench_width=number("bench_width", default=0.0),
                          benches_in=str(v.get("benches_in", "both")), max_width=number("max_width", default=0.0) or None)
            if kind == "pad":
                return ds.graded_pad(grid, ring3, **common)
            return ds.graded_line(grid, ring3, width=number("width", default=0.0), mode=str(v.get("mode", "both")),
                                  crossfall=number("crossfall", default=0.0), **common)
        if kind == "platform":
            return ds.platform(grid, ring, number("level"), number("cut_slope", "CUT_SLOPE"), number("fill_slope", "FILL_SLOPE"))
        if kind == "basin":
            return ds.basin(grid, ring, number("floor"), number("slope", "SLOPE"))
        if kind == "pit":
            return ds.pit(grid, ring, number("floor"), number("bench", "BENCH"), number("berm", "BERM"), number("face", "FACE"))
        if kind == "stockpile":
            top = float(v["top"]) if "top" in v else None
            volume = float(v["volume"]) if "volume" in v else None
            if top is None and volume is None:
                raise ValueError("a stockpile needs a 'top' level or a 'volume'.")
            return ds.stockpile(grid, ring, top, number("slope", "SLOPE"), volume,
                                bench=float(v.get("bench", 0) or 0), berm=float(v.get("berm", 0) or 0))
        if kind == "terraces":
            return ds.terraces(grid, ring, number("step", "STEP"), number("riser", default=0.3))
        if kind == "fairway":
            return ds.smooth_area(grid, ring, number("window", default=40.0), number("depth", default=10.0))
        if kind == "green":
            return ds.raised(grid, ring, number("height", default=0.6), number("slope", default=4.0))
        return ds.bunker(grid, ring, number("depth", default=1.0), number("slope", default=2.0))

    @staticmethod
    def _outline(shape, design):
        """The outline (or line) as a feature line: the vertices' own levels for pads and lines, else the design."""
        ring = np.asarray(shape["ring"], float)
        if shape["kind"] in ("pad", "line"):
            pts = ring
            if shape["kind"] == "pad" and not np.allclose(pts[0, :2], pts[-1, :2]):
                pts = np.vstack([pts, pts[:1]])
            return [(float(x), float(y), float(z)) for x, y, z in pts[:, :3]]
        ring = ring[:, :2]
        if not np.allclose(ring[0], ring[-1]):
            ring = np.vstack([ring, ring[:1]])
        z = design.sample(ring)
        return [(x, y, float(level)) for (x, y), level in zip(ring, z) if np.isfinite(level)]


def _used(grid):
    """The grid indices that ``Grid.tin`` keeps, in its order (all of them for a full grid)."""
    return np.flatnonzero(np.isfinite(grid.z.ravel()))


def _epsg(crs):
    authority = crs.authid()
    return int(authority.split(":")[1]) if authority.upper().startswith("EPSG:") and authority.split(":")[1].isdigit() else None


def _report(rows, cut, fill, unit, cell, dem_name):
    from .landxml.reports import _cards, _document, _table

    body = [_cards([("Cut", f"{cut:,.0f} {unit}³"), ("Fill", f"{fill:,.0f} {unit}³"),
                    ("Net (fill − cut)", f"{fill - cut:+,.0f} {unit}³"), ("Shapes", str(len(rows))),
                    ("Grid", f"{cell:g} {unit}")])]
    table = [(row["label"], row["kind"],
              ", ".join(f"{k} {v}" for k, v in row["values"].items() if k not in ("shape", "name", "order")) or "defaults",
              row["cut"], row["fill"], row["fill"] - row["cut"]) for row in rows]
    table.append(("Total", "", "", cut, fill, fill - cut))
    body.append("<h2>Volumes by shape</h2><p class='note'>Bank (in-place) volumes in cubic " + html.escape(unit)
                + "s; each shape's cut and fill is measured on the result of the shapes before it.</p>"
                + _table(["Shape", "Type", "Parameters", "Cut", "Fill", "Net"], table))
    return _document("Design Surface from Shapes", "".join(body), meta=[("Ground", dem_name)],
                     kicker="Site grading · Design Surface from Shapes")


__all__ = ["DesignShapesAlgorithm", "SHAPES", "os"]
