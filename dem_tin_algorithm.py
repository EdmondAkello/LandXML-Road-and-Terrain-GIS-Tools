"""DEM to LandXML TIN: an adaptive (within a vertical tolerance) or regular TIN of a raster, written as a LandXML surface."""

from __future__ import annotations

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProject,
)

from .earthworks_algorithm import VERTICAL_UNITS
from .grading_algorithm import HORIZONTAL_UNITS
from .landxml.dem_tin import adaptive_tin, grid_tin
from .landxml.grading import _inside_many
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.tin_overlay import sample_tin
from .landxml.writer import Surface, write_landxml
from .params import number_param
from .project_settings import ProjectSettingsAlgorithm

MAX_CELLS = 40_000_000           # largest DEM window triangulated at once
CHECK_POINTS = 250_000           # cell centres sampled for the reported fit
WRITER_UNITS = {"meter": "meter", "foot": "foot", "ussurveyfoot": "USSurveyFoot"}


class DemToTinAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return DemToTinAlgorithm()

    def name(self):
        return "landxml_dem_to_tin"

    def displayName(self):
        return "DEM to LandXML TIN"

    def group(self):
        return "3. Terrain conversion"

    def groupId(self):
        return "terrain_conversion"

    def shortHelpString(self):
        return (
            "Turns a DEM (any raster GDAL reads, in a projected CRS in metres or feet) into a TIN surface in a LandXML "
            "file, for Civil 3D and for the tools here that read LandXML surfaces.\n\n"
            "<b>Adaptive</b> places triangles where the ground changes: large ones on even ground, small ones where it "
            "bends, so that the TIN is within the <b>vertical tolerance</b> of every DEM cell centre. <b>Regular grid</b> "
            "uses every n-th cell centre (two triangles a cell). Every TIN point is a DEM cell centre with its own level: "
            "nothing is interpolated or smoothed. Cells without data are left out (holes stay holes). A <b>triangle "
            "limit</b> caps the size; the worst-fitting areas are refined first, and the report says when the tolerance "
            "could not be met within it.\n\n"
            "A DEM stored in whole metres (ALOS AW3D30, some SRTM products) is stepped: a tolerance under half a metre "
            "then keeps almost every cell. A DSM (ALOS) includes buildings and trees; use a terrain model (FABDEM, "
            "a DTM) for ground.\n\n"
            "Choose an extent or a mask polygon layer to convert part of a large DEM. The elevation unit is taken from the "
            "DEM; when it does not declare it, choose it. Levels are written in the CRS's length unit."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", "DEM"))
        self.addParameter(QgsProcessingParameterExtent("EXTENT", "Extent (default: the whole DEM)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource("MASK", "Mask polygons (keep only triangles inside)",
                                                              [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "TRIANGULATION", "Triangulation", options=["Adaptive (within a vertical tolerance)", "Regular grid"], defaultValue=0))
        self.addParameter(number_param("TOLERANCE", "Vertical tolerance (adaptive)", 0.10, 0.0, 1000.0, decimals=3))
        self.addParameter(number_param("STEP", "Use every n-th cell (regular grid)", 1, 1, 1000, decimals=0))
        self.addParameter(number_param("MAX_TRIANGLES", "Triangle limit (0 = none)", 2_000_000, 0, 1e9, decimals=0))
        self.addParameter(QgsProcessingParameterEnum(
            "VERTICAL_UNIT", "Elevation unit — choose when the DEM does not declare it",
            options=[name for name, _unit in VERTICAL_UNITS], defaultValue=0))
        self.addParameter(QgsProcessingParameterString("SURFACE_NAME", "Surface name (default: the DEM's name)", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "LandXML file", fileFilter="LandXML (*.xml)"))
        for key, label in (("POINTS", "Points"), ("TRIANGLES", "Triangles"),
                           ("MAX_MISS", "Largest vertical miss at a cell centre")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def processAlgorithm(self, p, c, fb):
        from osgeo import gdal

        layer = self.parameterAsRasterLayer(p, "DEM", c)
        crs = layer.crs()
        unit = HORIZONTAL_UNITS.get(crs.mapUnits())
        if unit is None:
            raise QgsProcessingException("The DEM must be in a projected CRS in metres or feet.")
        dataset = gdal.Open(layer.source())
        if dataset is None:
            raise QgsProcessingException(f"Could not open the DEM '{layer.name()}'.")
        x0, cx, r1, y0, r2, cy = dataset.GetGeoTransform()
        if r1 or r2:
            raise QgsProcessingException("The DEM is rotated; warp it to a north-up grid first.")
        band = dataset.GetRasterBand(1)
        declared = (band.GetUnitType() or "").strip().lower()
        aliases = {"m": "meter", "metre": "meter", "meters": "meter", "metres": "meter", "ft": "foot", "feet": "foot",
                   "us survey foot": "USSurveyFoot", "ussurveyfoot": "USSurveyFoot"}
        level_unit = aliases.get(declared, declared) or VERTICAL_UNITS[self.parameterAsEnum(p, "VERTICAL_UNIT", c)][1]
        if not level_unit or level_unit.lower() not in LINEAR_UNITS:
            raise QgsProcessingException("The DEM does not declare its elevation unit; choose the Elevation unit.")
        factor = LINEAR_UNITS[level_unit.lower()][0] / LINEAR_UNITS[unit.lower()][0]

        # ---- the window to convert: the extent, the mask's bounds, or the whole DEM
        col0, row0, col1, row1 = 0, 0, dataset.RasterXSize, dataset.RasterYSize
        rings = self._mask(p, c, crs)
        box = None
        if p.get("EXTENT"):
            extent = self.parameterAsExtent(p, "EXTENT", c, crs)
            if not extent.isNull():
                box = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
        if rings:
            mb = (min(r[:, 0].min() for r in rings), min(r[:, 1].min() for r in rings),
                  max(r[:, 0].max() for r in rings), max(r[:, 1].max() for r in rings))
            box = mb if box is None else (max(box[0], mb[0]), max(box[1], mb[1]), min(box[2], mb[2]), min(box[3], mb[3]))
        if box is not None:
            col0, col1 = max(int((box[0] - x0) / cx), 0), min(int(np.ceil((box[2] - x0) / cx)), dataset.RasterXSize)
            row0, row1 = max(int((y0 - box[3]) / -cy), 0), min(int(np.ceil((y0 - box[1]) / -cy)), dataset.RasterYSize)
        if col1 - col0 < 2 or row1 - row0 < 2:
            raise QgsProcessingException("The extent or mask does not overlap the DEM.")
        if (col1 - col0) * (row1 - row0) > MAX_CELLS:
            raise QgsProcessingException(
                f"The window is {col1 - col0:,} × {row1 - row0:,} cells, more than {MAX_CELLS:,}. Choose an extent or a "
                "mask, or resample the DEM first.")
        z = band.ReadAsArray(col0, row0, col1 - col0, row1 - row0).astype(float)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            z[z == nodata] = np.nan
        z *= factor
        fb.pushInfo(f"DEM window: {z.shape[1]:,} × {z.shape[0]:,} cells of {abs(cx):g} {unit}.")

        adaptive = self.parameterAsEnum(p, "TRIANGULATION", c) == 0
        limit = int(self.parameterAsDouble(p, "MAX_TRIANGLES", c)) or None
        tolerance = self.parameterAsDouble(p, "TOLERANCE", c)
        if adaptive:
            nodes, faces = adaptive_tin(z, tolerance, limit)
        else:
            nodes, faces = grid_tin(z, int(self.parameterAsDouble(p, "STEP", c)))
            if limit and len(faces) > limit:
                raise QgsProcessingException(f"The regular grid has {len(faces):,} triangles, above the limit; use a larger "
                                             "step, the adaptive method or a smaller extent.")
        xs = x0 + (col0 + nodes[:, 1] + 0.5) * cx
        ys = y0 + (row0 + nodes[:, 0] + 0.5) * cy
        xyz = np.column_stack([xs, ys, z[nodes[:, 0], nodes[:, 1]]])
        if rings:
            centroid = xyz[faces, :2].mean(axis=1)
            keep = np.zeros(len(faces), bool)
            for ring in rings:
                keep |= _inside_many(centroid, ring)
            faces = faces[keep]
            used = np.unique(faces)
            remap = np.full(len(xyz), -1)
            remap[used] = np.arange(len(used))
            xyz, faces = xyz[used], remap[faces]
        if not len(faces):
            raise QgsProcessingException("No triangles: the DEM has no data inside the extent or mask.")

        # ---- the fit, checked at the cell centres the TIN covers (a fixed random sample of them on large windows)
        rr, cc = np.nonzero(np.isfinite(z))
        if len(rr) > CHECK_POINTS:
            pick = np.random.default_rng(0).choice(len(rr), CHECK_POINTS, replace=False)
            rr, cc = rr[pick], cc[pick]
        probe = np.column_stack([x0 + (col0 + cc + 0.5) * cx, y0 + (row0 + rr + 0.5) * cy])
        got = sample_tin((xyz, faces), probe)
        ok = np.isfinite(got)
        miss = float(np.abs(got[ok] - z[rr[ok], cc[ok]]).max()) if ok.any() else 0.0
        name = self.parameterAsString(p, "SURFACE_NAME", c).strip() or layer.name()
        out = self.parameterAsFileOutput(p, "OUTPUT", c)
        authority = crs.authid()
        code = authority.split(":")[1] if authority.upper().startswith("EPSG:") else ""
        epsg = int(code) if code.isdigit() else None
        write_landxml(out, [], linear_unit=WRITER_UNITS[unit.lower()], vertical_unit=WRITER_UNITS[unit.lower()],
                      epsg=epsg,
                      surfaces=[Surface(name, xyz.tolist(), faces.tolist(), f"From DEM '{layer.name()}'")])
        full = 2 * (z.shape[0] - 1) * (z.shape[1] - 1)
        fb.pushInfo(f"TIN '{name}': {len(xyz):,} points, {len(faces):,} triangles ({100 * len(faces) / max(full, 1):.1f} % of a "
                    f"full grid); largest miss at a cell centre {miss:.3f} {unit}.")
        if adaptive and limit and miss > tolerance * (1 + 1e-3) + 1e-3:
            fb.pushWarning(f"The triangle limit stopped the refinement before the {tolerance:g} tolerance was met; "
                           "raise the limit or convert a smaller area.")
        return {"OUTPUT": out, "POINTS": len(xyz), "TRIANGLES": len(faces), "MAX_MISS": miss}

    def _mask(self, p, c, crs):
        source = self.parameterAsSource(p, "MASK", c) if p.get("MASK") else None
        if source is None:
            return []
        transform = QgsCoordinateTransform(source.sourceCrs(), crs, QgsProject.instance())
        rings = []
        for feature in source.getFeatures():
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            geometry.transform(transform)
            for part in geometry.constParts():
                rings.append(np.array([[v.x(), v.y()] for v in part.exteriorRing()], float))
        return rings
