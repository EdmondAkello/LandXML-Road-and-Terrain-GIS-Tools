from bisect import bisect_left
import math
import os
import numpy as np
from osgeo import ogr, osr, gdal
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
)
from .core import (
    read_tin,
    transform_vertices,
    rasterize_tin,
    write_geotiff,
)
from .landxml.geometry import read_alignments, regular_station_distances
from .landxml.features import read_line_features
from .landxml.profile import read_profile_controls, read_vertical_profile
from .landxml.sections import read_cross_sections
from .processing_common import coordinate_choices
from .params import number_param


def _ogr_srs(crs):
    s = osr.SpatialReference()
    s.ImportFromWkt(crs.toWkt())
    return s


def _delete_layer(ds, name):
    # Idempotent "ensure clean slate" delete: check first rather than
    # attempting the delete and silently swallowing any failure, so a
    # genuine GDAL/OGR error here isn't masked -- only "layer doesn't
    # exist yet" is treated as a normal, expected outcome.
    if ds.GetLayerByName(name) is not None:
        ds.DeleteLayer(name)


def _fld(layer, name, typ=ogr.OFTString):
    layer.CreateField(ogr.FieldDefn(name, typ))


def _set_fields(feature, values):
    for name, value in values.items():
        if value is not None and value != "":
            feature.SetField(name, value)


def _ogr_line(points, method, params, dimension=2):
    array = transform_vertices(np.asarray(points, dtype=float), method=method, **params)
    geometry = ogr.Geometry(
        ogr.wkbLineString25D if dimension == 3 else ogr.wkbLineString
    )
    for row in array:
        if dimension == 3:
            geometry.AddPoint(float(row[0]), float(row[1]), float(row[2]))
        else:
            geometry.AddPoint_2D(float(row[0]), float(row[1]))
    return geometry


def _write_points(ds, name, records, srs):
    _delete_layer(ds, name)
    lyr = ds.CreateLayer(name, srs, ogr.wkbPoint)
    _fld(lyr, "alignment")
    _fld(lyr, "station", ogr.OFTReal)
    _fld(lyr, "bearing", ogr.OFTReal)
    for x, y, a, st, b in records:
        f = ogr.Feature(lyr.GetLayerDefn())
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(x, y)
        f.SetGeometry(g)
        f.SetField("alignment", a)
        f.SetField("station", st)
        f.SetField("bearing", b)
        lyr.CreateFeature(f)


def _param_common_local(a):
    a.addParameter(
        QgsProcessingParameterEnum(
            "METHOD",
            "Coordinate interpretation",
            options=[
                "Use stored coordinates",
                "Swap X / Y",
                "Apply X / Y offset",
                "Swap X / Y + offset",
                "2-D Helmert (similarity)",
                "Reproject from source CRS",
            ],
            defaultValue=0,
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
            "OUTPUT_CRS", "Output CRS (result layer / raster)", defaultValue=None
        )
    )


class CompleteRoadDesignAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return CompleteRoadDesignAlgorithm()

    def name(self):
        return "landxml_complete_road_design"

    def displayName(self):
        return "Export Complete Road Design to GeoPackage"

    def group(self):
        return "Road design extraction"

    def groupId(self):
        return "road_design_extraction"

    def shortHelpString(self):
        return "Export a configurable LandXML road design package with alignment, profile, station, cross-section, feature-line, breakline, surface-boundary, DEM and contour outputs."

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        _param_common_local(self)
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
                "SURFACE",
                "TIN surface name (blank = all)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            number_param(
                "RESOLUTION",
                "TIN DEM resolution (output CRS units)",
                5,
                0.01,
                10000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "NODATA", "DEM NoData value", -9999, -3.4e38, 3.4e38, decimals=3
            )
        )
        self.addParameter(
            number_param(
                "CONTOUR_INTERVAL",
                "Contour interval (source vertical units; 0 = skip)",
                5,
                0,
                10000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "ALIGNMENT_SEGMENT",
                "Alignment curve/spiral segment length (source horizontal units)",
                5,
                0.01,
                10000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "STATION_INTERVAL",
                "Station point interval (source station units)",
                20,
                0.01,
                100000,
                decimals=2,
            )
        )
        self.addParameter(
            number_param(
                "PROFILE_INTERVAL",
                "Profile sampling interval (source station units)",
                5,
                0.01,
                10000,
                decimals=2,
            )
        )
        for key, label in [
            ("INCLUDE_ALIGNMENTS", "Include horizontal alignments"),
            ("INCLUDE_CENTERLINES", "Include 3D centerlines"),
            ("INCLUDE_PROFILES", "Include vertical profiles"),
            ("INCLUDE_STATIONS", "Include station points"),
            ("INCLUDE_CROSSSECTIONS", "Include cross-sections"),
            ("INCLUDE_FEATURELINES", "Include feature lines"),
            ("INCLUDE_BREAKLINES", "Include breaklines"),
            ("INCLUDE_SURFACE_BOUNDARY", "Include TIN surface boundary"),
            ("INCLUDE_DEM", "Create TIN-derived DEM"),
            ("INCLUDE_CONTOURS", "Create contours"),
        ]:
            self.addParameter(
                QgsProcessingParameterBoolean(key, label, defaultValue=True)
            )
        self.addParameter(
            QgsProcessingParameterFolderDestination("OUTPUT_DIR", "Output folder")
        )

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        out = self.parameterAsString(p, "OUTPUT_DIR", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        m, crs, src, tp, document = coordinate_choices(self, p, c, fb, path)
        os.makedirs(out, exist_ok=True)
        alignment_filter = self.parameterAsString(p, "ALIGNMENT", c).strip()
        surface_filter = self.parameterAsString(p, "SURFACE", c).strip() or None
        if surface_filter:
            matches = [
                surface
                for index, surface in enumerate(document.surfaces())
                if surface.attrib.get("name", f"Surface {index + 1}") == surface_filter
            ]
            if not matches:
                raise QgsProcessingException(
                    f"Surface '{surface_filter}' was not found in the LandXML file."
                )
            if len(matches) > 1:
                raise QgsProcessingException(
                    f"Surface name '{surface_filter}' is ambiguous; {len(matches)} surfaces have that name."
                )
        align_seg = self.parameterAsDouble(p, "ALIGNMENT_SEGMENT", c)
        station_interval = self.parameterAsDouble(p, "STATION_INTERVAL", c)
        profile_interval = self.parameterAsDouble(p, "PROFILE_INTERVAL", c)
        nodata = self.parameterAsDouble(p, "NODATA", c)
        flags = {
            k: self.parameterAsBoolean(p, k, c)
            for k in [
                "INCLUDE_ALIGNMENTS",
                "INCLUDE_CENTERLINES",
                "INCLUDE_PROFILES",
                "INCLUDE_STATIONS",
                "INCLUDE_CROSSSECTIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_BREAKLINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ]
        }
        base = os.path.splitext(os.path.basename(path))[0]
        gpkg = os.path.join(out, base + "_GIS.gpkg")
        srs = _ogr_srs(crs)
        if os.path.exists(gpkg):
            raise QgsProcessingException(
                f"Output GeoPackage already exists: {gpkg}. Choose a new folder or move the existing file."
            )
        ds = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg)
        if ds is None:
            raise QgsProcessingException(f"Could not create GeoPackage: {gpkg}")
        root = document.root
        try:
            al, uns = read_alignments(
                path, align_seg, feedback=fb, cancel=fb.isCanceled
            )
        except ValueError as exc:
            if "No <Alignment>" in str(exc):
                al, uns = [], []
            else:
                raise QgsProcessingException(
                    f"Alignment parsing failed: {exc}"
                ) from exc
        for warning in uns[:20]:
            fb.pushWarning(f"Alignment geometry skipped: {warning}")
        if len(uns) > 20:
            fb.pushWarning(
                f"{len(uns) - 20} additional alignment elements were skipped."
            )
        if alignment_filter:
            al = [a for a in al if a["name"] == alignment_filter]

        if (
            flags["INCLUDE_ALIGNMENTS"]
            or flags["INCLUDE_STATIONS"]
            or flags["INCLUDE_CENTERLINES"]
        ):
            if flags["INCLUDE_ALIGNMENTS"]:
                _delete_layer(ds, "alignments")
                lyr = ds.CreateLayer("alignments", srs, ogr.wkbLineString)
                for n, t in [
                    ("name", ogr.OFTString),
                    ("description", ogr.OFTString),
                    ("length", ogr.OFTReal),
                    ("sta_start", ogr.OFTString),
                    ("sta_end", ogr.OFTString),
                ]:
                    _fld(lyr, n, t)
                for field in (
                    "source_vendor",
                    "source_file",
                    "source_name",
                    "source_description",
                    "alignment_name",
                    "geometry_type",
                    "direction",
                ):
                    _fld(lyr, field)
                for field in (
                    "station_start",
                    "station_end",
                    "radius",
                    "curve_length",
                    "spiral_length",
                ):
                    _fld(lyr, field, ogr.OFTReal)
                segments_layer = ds.CreateLayer(
                    "alignment_elements", srs, ogr.wkbLineString
                )
                for field in (
                    "source_vendor",
                    "source_file",
                    "source_name",
                    "source_description",
                    "alignment_name",
                    "geometry_type",
                    "direction",
                ):
                    _fld(segments_layer, field)
                for field in (
                    "station_start",
                    "radius",
                    "curve_length",
                    "spiral_length",
                ):
                    _fld(segments_layer, field, ogr.OFTReal)
                for a in al:
                    g = _ogr_line(a["points"], m, tp)
                    f = ogr.Feature(lyr.GetLayerDefn())
                    f.SetGeometry(g)
                    single = a["segments"][0] if len(a["segments"]) == 1 else None
                    _set_fields(
                        f,
                        dict(
                            name=a["name"],
                            description=a["desc"],
                            length=float(a["length"]) if a["length"] else None,
                            sta_start=a["sta_start"],
                            sta_end=a["sta_end"],
                            source_vendor=document.vendor,
                            source_file=os.path.basename(path),
                            source_name=a["name"],
                            source_description=a["desc"],
                            alignment_name=a["name"],
                            station_start=float(a["sta_start"])
                            if a["sta_start"]
                            else None,
                            station_end=float(a["sta_end"]) if a["sta_end"] else None,
                            geometry_type=single["geometry_type"]
                            if single
                            else "compound",
                            direction=single["direction"] if single else None,
                            radius=single["radius"] if single else None,
                            curve_length=single["curve_length"] if single else None,
                            spiral_length=single["spiral_length"] if single else None,
                        ),
                    )
                    lyr.CreateFeature(f)
                    for segment in a["segments"]:
                        part = ogr.Feature(segments_layer.GetLayerDefn())
                        part.SetGeometry(_ogr_line(segment["points"], m, tp))
                        _set_fields(
                            part,
                            dict(
                                source_vendor=document.vendor,
                                source_file=os.path.basename(path),
                                source_name=a["name"],
                                source_description=a["desc"],
                                alignment_name=a["name"],
                                geometry_type=segment["geometry_type"],
                                direction=segment["direction"],
                                station_start=segment["station_start"],
                                radius=segment["radius"],
                                curve_length=segment["curve_length"],
                                spiral_length=segment["spiral_length"],
                            ),
                        )
                        segments_layer.CreateFeature(part)
            if flags["INCLUDE_STATIONS"]:
                ptsrec = []
                for a in al:
                    if not a["sta_start"]:
                        fb.pushWarning(
                            f"Skipped station points for '{a['name']}': start station is missing."
                        )
                        continue
                    raw = np.asarray([[x, y, 0] for x, y in a["points"]], float)
                    cum = [0.0]
                    for i in range(1, len(raw)):
                        cum.append(
                            cum[-1]
                            + math.hypot(
                                raw[i, 0] - raw[i - 1, 0], raw[i, 1] - raw[i - 1, 1]
                            )
                        )
                    for distance, station in regular_station_distances(
                        float(a["sta_start"]), cum[-1], station_interval
                    ):
                        i = min(max(1, bisect_left(cum, distance)), len(cum) - 1)
                        j = max(0, i - 1)
                        L = max(cum[i] - cum[j], 1e-12)
                        q = (distance - cum[j]) / L
                        source = raw[j] + q * (raw[i] - raw[j])
                        target = transform_vertices(
                            np.asarray([source]), method=m, **tp
                        )[0]
                        bearing = (
                            math.degrees(
                                math.atan2(raw[i, 0] - raw[j, 0], raw[i, 1] - raw[j, 1])
                            )
                            + 360
                        ) % 360
                        ptsrec.append(
                            (
                                float(target[0]),
                                float(target[1]),
                                a["name"],
                                station,
                                bearing,
                            )
                        )
                _write_points(ds, "station_points", ptsrec, srs)

        if flags["INCLUDE_CENTERLINES"] or flags["INCLUDE_PROFILES"]:
            profile_records = []
            for alignment_name, element in document.profiles():
                if alignment_filter and alignment_name != alignment_filter:
                    continue
                try:
                    samples = read_vertical_profile(element, profile_interval)
                    controls = read_profile_controls(element)
                except ValueError as exc:
                    raise QgsProcessingException(
                        f"Profile '{element.attrib.get('name', '')}' is invalid: {exc}"
                    ) from exc
                if len(samples) >= 2:
                    profile_records.append((alignment_name, element, samples, controls))
            if flags["INCLUDE_PROFILES"]:
                profile_layer = ds.CreateLayer("profiles", None, ogr.wkbLineString)
                for field in (
                    "alignment_name",
                    "profile_name",
                    "source_name",
                    "source_description",
                    "source_vendor",
                    "source_file",
                ):
                    _fld(profile_layer, field)
                for field in ("station_start", "station_end"):
                    _fld(profile_layer, field, ogr.OFTReal)
                control_layer = ds.CreateLayer("profile_controls", None, ogr.wkbPoint)
                for field in (
                    "alignment_name",
                    "profile_name",
                    "source_name",
                    "source_description",
                    "control_type",
                    "curve_type",
                    "source_vendor",
                    "source_file",
                ):
                    _fld(control_layer, field)
                for field in (
                    "station",
                    "elevation",
                    "grade_in",
                    "grade_out",
                    "curve_length",
                ):
                    _fld(control_layer, field, ogr.OFTReal)
                for key, element, samples, controls in profile_records:
                    feature = ogr.Feature(profile_layer.GetLayerDefn())
                    graph = ogr.Geometry(ogr.wkbLineString)
                    for station, elevation in samples:
                        graph.AddPoint_2D(station, elevation)
                    feature.SetGeometry(graph)
                    _set_fields(
                        feature,
                        dict(
                            alignment_name=key,
                            profile_name=element.attrib.get("name"),
                            source_name=element.attrib.get("name"),
                            source_description=element.attrib.get("desc"),
                            source_vendor=document.vendor,
                            source_file=os.path.basename(path),
                            station_start=samples[0][0],
                            station_end=samples[-1][0],
                        ),
                    )
                    profile_layer.CreateFeature(feature)
                    for control in controls:
                        point = ogr.Feature(control_layer.GetLayerDefn())
                        geom = ogr.Geometry(ogr.wkbPoint)
                        geom.AddPoint_2D(control["station"], control["elevation"])
                        point.SetGeometry(geom)
                        _set_fields(
                            point,
                            dict(
                                alignment_name=key,
                                profile_name=element.attrib.get("name"),
                                source_name=element.attrib.get("name"),
                                source_description=element.attrib.get("desc"),
                                control_type=control["control_type"],
                                curve_type=control["curve_type"],
                                source_vendor=document.vendor,
                                source_file=os.path.basename(path),
                                station=control["station"],
                                elevation=control["elevation"],
                                grade_in=control["grade_in"],
                                grade_out=control["grade_out"],
                                curve_length=control["curve_length"],
                            ),
                        )
                        control_layer.CreateFeature(point)
            if flags["INCLUDE_CENTERLINES"]:
                centerlines = ds.CreateLayer(
                    "centerlines_3d", srs, ogr.wkbLineString25D
                )
                for field in (
                    "alignment_name",
                    "profile_name",
                    "source_vendor",
                    "source_file",
                ):
                    _fld(centerlines, field)
                for alignment in al:
                    matches = [
                        row for row in profile_records if row[0] == alignment["name"]
                    ]
                    if len(matches) != 1:
                        fb.pushWarning(
                            f"Skipped 3D centerline '{alignment['name']}': expected one matching vertical profile, found {len(matches)}."
                        )
                        continue
                    if not alignment["sta_start"]:
                        fb.pushWarning(
                            f"Skipped 3D centerline '{alignment['name']}': start station is missing."
                        )
                        continue
                    _, element, samples, _controls = matches[0]
                    raw = np.asarray([[x, y, 0] for x, y in alignment["points"]], float)
                    cum = [0.0]
                    for i in range(1, len(raw)):
                        cum.append(
                            cum[-1]
                            + math.hypot(
                                raw[i, 0] - raw[i - 1, 0], raw[i, 1] - raw[i - 1, 1]
                            )
                        )
                    s0 = float(alignment["sta_start"])
                    start = max(s0, samples[0][0])
                    end = min(s0 + cum[-1], samples[-1][0])
                    if end <= start:
                        fb.pushWarning(
                            f"Skipped 3D centerline '{alignment['name']}': profile does not overlap the alignment station range."
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
                        return raw[i - 1, :2] + fraction * (
                            raw[i, :2] - raw[i - 1, :2]
                        )

                    stations = [start]
                    stations.extend(s0 + d for d in cum if start < s0 + d < end)
                    stations.append(end)
                    clipped = np.asarray(
                        [[*xy_at(station), 0] for station in stations], dtype=float
                    )
                    target = transform_vertices(clipped, method=m, **tp)
                    graph = ogr.Geometry(ogr.wkbLineString25D)
                    sample_stations = [sample[0] for sample in samples]
                    for (x, y, _), station in zip(target, stations):
                        j = max(1, bisect_left(sample_stations, station))
                        st0, z0 = samples[j - 1]
                        st1, z1 = samples[j]
                        z = z0 + (z1 - z0) * (station - st0) / (st1 - st0)
                        graph.AddPoint(float(x), float(y), float(z))
                    feature = ogr.Feature(centerlines.GetLayerDefn())
                    feature.SetGeometry(graph)
                    _set_fields(
                        feature,
                        dict(
                            alignment_name=alignment["name"],
                            profile_name=element.attrib.get("name"),
                            source_vendor=document.vendor,
                            source_file=os.path.basename(path),
                        ),
                    )
                    centerlines.CreateFeature(feature)
                    if start > s0 or end < s0 + cum[-1]:
                        fb.pushWarning(
                            f"Created partial 3D centerline '{alignment['name']}' from station {start:.3f} to {end:.3f}; "
                            "the profile does not cover the remaining alignment."
                        )

        if flags["INCLUDE_CROSSSECTIONS"]:
            sections, warnings = read_cross_sections(root)
            for warning in warnings[:20]:
                fb.pushWarning(warning)
            if len(warnings) > 20:
                fb.pushWarning(
                    f"{len(warnings) - 20} additional cross sections were skipped."
                )
            if alignment_filter:
                sections = [
                    section
                    for section in sections
                    if section.alignment_name == alignment_filter
                ]
            for dimension in (2, 3):
                selected = [
                    section
                    for section in sections
                    if len(section.points[0]) == dimension
                ]
                if not selected:
                    continue
                layer_name = "cross_sections" if dimension == 3 else "cross_sections_2d"
                layer = ds.CreateLayer(
                    layer_name,
                    srs,
                    ogr.wkbLineString25D if dimension == 3 else ogr.wkbLineString,
                )
                for field in (
                    "alignment_name",
                    "source_name",
                    "source_description",
                    "source_vendor",
                    "source_file",
                ):
                    _fld(layer, field)
                _fld(layer, "station", ogr.OFTReal)
                _fld(layer, "point_count", ogr.OFTInteger)
                for section in selected:
                    feature = ogr.Feature(layer.GetLayerDefn())
                    feature.SetGeometry(_ogr_line(section.points, m, tp, dimension))
                    _set_fields(
                        feature,
                        dict(
                            alignment_name=section.alignment_name,
                            source_name=section.name,
                            source_description=section.description,
                            source_vendor=document.vendor,
                            source_file=os.path.basename(path),
                            station=section.station,
                            point_count=len(section.points),
                        ),
                    )
                    layer.CreateFeature(feature)

        if flags["INCLUDE_FEATURELINES"] or flags["INCLUDE_BREAKLINES"]:
            line_records, warnings = read_line_features(root)
            for warning in warnings[:20]:
                fb.pushWarning(warning)
            if len(warnings) > 20:
                fb.pushWarning(
                    f"{len(warnings) - 20} additional source lines were skipped."
                )
            for kind, layer_name, flag in (
                ("FeatureLine", "feature_lines", "INCLUDE_FEATURELINES"),
                ("Breakline", "breaklines", "INCLUDE_BREAKLINES"),
            ):
                if not flags[flag]:
                    continue
                matching = [
                    record
                    for record in line_records
                    if record.kind == kind
                    and (
                        surface_filter is None or record.surface_name == surface_filter
                    )
                ]
                for dimension in (2, 3):
                    selected = [
                        record
                        for record in matching
                        if len(record.points[0]) == dimension
                    ]
                    if not selected:
                        continue
                    name = layer_name if dimension == 3 else layer_name + "_2d"
                    layer = ds.CreateLayer(
                        name,
                        srs,
                        ogr.wkbLineString25D if dimension == 3 else ogr.wkbLineString,
                    )
                    for field in (
                        "source_vendor",
                        "source_file",
                        "surface_name",
                        "feature_name",
                        "source_description",
                        "feature_type",
                        "feature_code",
                        "breakline_type",
                        "source_id",
                        "source_user_id",
                    ):
                        _fld(layer, field)
                    for record in selected:
                        feature = ogr.Feature(layer.GetLayerDefn())
                        feature.SetGeometry(_ogr_line(record.points, m, tp, dimension))
                        _set_fields(
                            feature,
                            dict(
                                source_vendor=document.vendor,
                                source_file=os.path.basename(path),
                                surface_name=record.surface_name,
                                feature_name=record.name,
                                source_description=record.description,
                                feature_type=record.kind,
                                feature_code=record.feature_code,
                                breakline_type=record.breakline_type,
                                source_id=record.properties.get("id"),
                                source_user_id=record.properties.get("UsrID"),
                            ),
                        )
                        layer.CreateFeature(feature)

        if (
            flags["INCLUDE_SURFACE_BOUNDARY"]
            or flags["INCLUDE_DEM"]
            or flags["INCLUDE_CONTOURS"]
        ):
            surfaces = [
                (index, surface.attrib.get("name", f"Surface {index + 1}"))
                for index, surface in enumerate(document.surfaces())
            ]
            if surface_filter:
                surfaces = [item for item in surfaces if item[1] == surface_filter]
                if not surfaces:
                    raise QgsProcessingException(
                        f"Surface '{surface_filter}' was not found in the LandXML file."
                    )
                if len(surfaces) > 1:
                    raise QgsProcessingException(
                        f"Surface name '{surface_filter}' is ambiguous; {len(surfaces)} surfaces have that name."
                    )
            if not surfaces:
                fb.pushWarning(
                    "No TIN surfaces were found; terrain outputs were skipped."
                )
            boundary_layer = None
            if flags["INCLUDE_SURFACE_BOUNDARY"] and surfaces:
                boundary_layer = ds.CreateLayer(
                    "surface_boundary", srs, ogr.wkbLineString
                )
                for field in ("surface_name", "source_vendor", "source_file"):
                    _fld(boundary_layer, field)
            for output_index, (surface_index, surface_name) in enumerate(surfaces, 1):
                if fb.isCanceled():
                    raise QgsProcessingException("Complete export cancelled.")
                xyz, faces, meta = read_tin(path, surface_index=surface_index)
                xyz = transform_vertices(xyz, method=m, **tp)
                fb.pushInfo(
                    f"Transformed bounds for surface '{surface_name}': "
                    f"X {xyz[:, 0].min():.3f}–{xyz[:, 0].max():.3f}; "
                    f"Y {xyz[:, 1].min():.3f}–{xyz[:, 1].max():.3f}"
                )
                if boundary_layer is not None:
                    edges = {}
                    for a, b, c3 in faces:
                        for u, v in ((a, b), (b, c3), (c3, a)):
                            key = tuple(sorted((int(u), int(v))))
                            edges[key] = edges.get(key, 0) + 1
                    for (u, v), count in edges.items():
                        if count != 1:
                            continue
                        feature = ogr.Feature(boundary_layer.GetLayerDefn())
                        line = ogr.Geometry(ogr.wkbLineString)
                        line.AddPoint_2D(float(xyz[u, 0]), float(xyz[u, 1]))
                        line.AddPoint_2D(float(xyz[v, 0]), float(xyz[v, 1]))
                        feature.SetGeometry(line)
                        _set_fields(
                            feature,
                            dict(
                                surface_name=meta["surface_name"],
                                source_vendor=document.vendor,
                                source_file=os.path.basename(path),
                            ),
                        )
                        boundary_layer.CreateFeature(feature)
                if flags["INCLUDE_DEM"] or flags["INCLUDE_CONTOURS"]:
                    array, xmin, ymax, resolution = rasterize_tin(
                        xyz,
                        faces,
                        self.parameterAsDouble(p, "RESOLUTION", c),
                        nodata,
                        progress=lambda value, message: fb.setProgress(value),
                        cancel=fb.isCanceled,
                    )
                    suffix = (
                        "" if len(surfaces) == 1 else f"_surface_{output_index:02d}"
                    )
                    dem = os.path.join(out, base + suffix + "_DEM.tif")
                    if os.path.exists(dem):
                        raise QgsProcessingException(
                            f"Output GeoTIFF already exists: {dem}"
                        )
                    write_geotiff(
                        dem, array, xmin, ymax, resolution, nodata, wkt=crs.toWkt()
                    )
                    if (
                        flags["INCLUDE_CONTOURS"]
                        and self.parameterAsDouble(p, "CONTOUR_INTERVAL", c) > 0
                    ):
                        contour = os.path.join(out, base + suffix + "_Contours.gpkg")
                        if os.path.exists(contour):
                            raise QgsProcessingException(
                                f"Output contour GeoPackage already exists: {contour}"
                            )
                        cds = ogr.GetDriverByName("GPKG").CreateDataSource(contour)
                        if cds is None:
                            raise QgsProcessingException(
                                f"Could not create contour GeoPackage: {contour}"
                            )
                        clyr = cds.CreateLayer("contours", srs, ogr.wkbLineString)
                        _fld(clyr, "elev", ogr.OFTReal)
                        dem_ds = gdal.Open(dem)
                        band = dem_ds.GetRasterBand(1)
                        gdal.ContourGenerate(
                            band,
                            float(self.parameterAsDouble(p, "CONTOUR_INTERVAL", c)),
                            0,
                            [],
                            1,
                            nodata,
                            clyr,
                            -1,
                            0,
                        )
                        dem_ds = None
                        cds = None
                    if not flags["INCLUDE_DEM"]:
                        os.remove(dem)
        ds = None
        return {"OUTPUT_DIR": out}
