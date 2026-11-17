"""Grading Processing tools.

* Grading Quantities (Volume Surface): exact cut/fill volumes, plan areas and
  depth bands from a volume (depth) TIN such as a Civil 3D TIN volume surface,
  with an optional check against the Civil 3D cut/fill summary.
* Platform Levels and Falls: slope classes, platforms, levels and falls of a
  design (finished-level) surface.
* Earthwork Balance: cut/fill balance of one or more volume surfaces under
  shrinkage scenarios, with site-to-site transfer, import and spoil.
* Grading Revision Comparison: cut/fill of two revisions' volume surfaces,
  overall and by zone, with an optional depth-change raster.
* Basin Stage–Storage: exact storage below each level of a basin surface, with a
  capacity check at the rim and with freeboard.
"""

from __future__ import annotations

import csv
import os

import numpy as np

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from .compat import FIELD_DOUBLE, FIELD_INT, FIELD_STRING
from .core import _epsg_of, bounds, grid_for_bounds, rasterize_tin, read_tin, transform_vertices, write_geotiff
from .earthworks_algorithm import (
    QUANTITY_HELP,
    VERTICAL_UNITS,
    _vertical_unit_param,
    _input_param,
    _resolve,
    _volume_parameter,
    _volume_unit,
    _write_text,
)
from .landxml.catalog import read_catalog
from .landxml.parser import load_document
from .landxml.plan_features import footprint_lines, level_lines, write_plan_features
from .landxml.tin_overlay import depth_tin, sample_tin
from .landxml.quantity_units import LINEAR_UNITS, quantity_units
from .landxml.grading import (
    depth_warning,
    earthwork_balance,
    grading_report_html,
    parse_bands,
    parse_factors,
    VOLUME_SURFACE_HINTS,
    balance_report_html,
    basin_report_html,
    change_grid,
    comparison_report_html,
    outline_polygons,
    platform_report_html,
    platform_summary,
    reconcile,
    revision_comparison,
    scale_comparison,
    stage_storage,
    suggest_volume_surface,
    volume_summary,
)
from .params import number_param
from .processing_common import coordinate_choices
from .processing_widgets import name_param
from .project_settings import ProjectSettingsAlgorithm
from .road_features import _param_coordinates

HORIZONTAL_UNITS = {
    Qgis.DistanceUnit.Meters: "meter",
    Qgis.DistanceUnit.Feet: "foot",
    Qgis.DistanceUnit.FeetUSSurvey: "usSurveyFoot",
}


def _optional_number(parameters, name):
    value = parameters.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise QgsProcessingException(f"{name.replace('_', ' ').title()} must be a number.") from exc


# Site grading has its own group, apart from road corridors.
GROUP = "9. Site grading (land development)"
GROUP_ID = "site_grading"


def _grading_units(algorithm, p, c, documents, feedback, horizontal, volume_unit="auto"):
    """(QuantityUnits, vertical unit) from the declared or explicitly chosen vertical unit.

    Civil 3D metric exports often omit ``verticalLinearUnit``. The unit is never
    inferred: it comes from the file or from the user's explicit choice, and a
    choice that contradicts a declared unit is an error.
    """
    chosen = VERTICAL_UNITS[algorithm.parameterAsEnum(p, "VERTICAL_UNIT", c)][1] if "VERTICAL_UNIT" in p else None
    declared = {doc.vertical_unit for doc in documents if doc.vertical_unit}
    if len(declared) > 1:
        raise QgsProcessingException(f"The files declare different vertical units ({', '.join(sorted(declared))}); "
                                     "prepare them with the same units first.")
    vertical = next(iter(declared), None)
    if vertical and chosen and LINEAR_UNITS[vertical.lower()] != LINEAR_UNITS[chosen.lower()]:
        raise QgsProcessingException(f"The file declares the vertical unit '{vertical}', but '{chosen}' was chosen. "
                                     "Leave Vertical unit as declared, or fix the file with Prepare LandXML.")
    if not vertical:
        if not chosen:
            raise QgsProcessingException(
                "The file does not declare its vertical (elevation) unit, and it is not inferred. Choose the Vertical unit "
                "(Civil 3D metric exports usually omit it; metre for a metric drawing), or declare it with Prepare LandXML.")
        vertical = chosen
        feedback.pushInfo(f"Vertical unit: {chosen} (chosen; the file does not declare it).")
    try:
        units = quantity_units(horizontal, vertical, True, volume_unit)
    except ValueError as exc:
        raise QgsProcessingException(str(exc)) from exc
    feedback.pushInfo(f"Earthwork units: volumes {units.volume}; lengths {units.length}; areas {units.area}; depths {units.depth}.")
    return units, vertical


EXISTING_HELP = (
    "\n\nWithout a volume surface (OpenRoads, or a Civil 3D file exported without one), enter the design surface "
    "and the existing-ground surface: the tool builds the exact depth surface (design minus existing) on the area "
    "both cover, by intersecting their triangles, so the volumes are those of a Civil 3D TIN volume surface built "
    "from the same two surfaces."
)


def _existing_file(algorithm, p, c, doc, feedback):
    """(path, document) of a separate existing-ground LandXML, or ("", None) to use the design file."""
    path = algorithm.parameterAsFile(p, "EXISTING_FILE", c) if "EXISTING_FILE" in p else ""
    if not path:
        return "", None
    if not os.path.isfile(path):
        raise QgsProcessingException("The existing-ground LandXML file does not exist.")
    if os.path.realpath(path) == os.path.realpath(doc.path):
        return "", None
    other = load_document(path)
    for label, mine, theirs in (("horizontal", doc.horizontal_unit, other.horizontal_unit),
                                ("vertical", doc.vertical_unit, other.vertical_unit)):
        if mine and theirs and mine.lower() != theirs.lower():
            raise QgsProcessingException(
                f"The design and existing-ground files declare different {label} units ({mine} and {theirs}); "
                "prepare them with the same units first.")
    feedback.pushInfo(f"Existing ground is read from {os.path.basename(path)}; both files must use the same "
                      "coordinate system, which is not checked.")
    return path, other


def _depth_surface(path, surface, existing, method, params, feedback, existing_path=None, keep=None, overlay=True):
    """(xyz, faces, meta) of a depth surface in output coordinates.

    With ``existing`` blank, ``surface`` is read as a volume (depth) surface.
    Otherwise ``surface`` is the design and the depth surface is built from both TINs
    in source coordinates, then transformed. The existing ground may come from another
    file (``existing_path``) and is read only where it meets the design area, so a very
    large survey surface costs memory in proportion to the site, not the survey.
    """
    try:
        xyz, faces, meta = read_tin(path, surface, cancel=feedback.isCanceled)
    except ValueError as exc:
        raise QgsProcessingException(f"{surface}: {exc}") from exc
    if meta["invisible_face_count"]:
        feedback.pushInfo(f"{surface}: skipped {meta['invisible_face_count']:,} hidden triangles.")
    if existing:
        source = existing_path or path
        if existing == surface and source == path:
            raise QgsProcessingException(f"'{surface}' is both the design and the existing-ground surface.")
        try:
            exyz, efaces, emeta = read_tin(source, existing, cancel=feedback.isCanceled, bounds=meta["bounds"])
            if keep is not None:
                keep.update(design=(xyz, faces), existing=(exyz, efaces))
                if not overlay:            # the caller needs the two surfaces only
                    return None, None, meta
            xyz, faces, stats = depth_tin((xyz, faces), (exyz, efaces))
        except ValueError as exc:
            if "touch the requested area" in str(exc):
                raise QgsProcessingException(
                    f"'{existing}' does not reach the area of '{surface}'. Check that both files use the same "
                    "coordinates.") from exc
            raise QgsProcessingException(f"{surface} − {existing}: {exc}") from exc
        if emeta["invisible_face_count"]:
            feedback.pushInfo(f"{existing}: skipped {emeta['invisible_face_count']:,} hidden triangles.")
        if emeta.get("cropped") and emeta["source_face_count"] > len(efaces):
            feedback.pushInfo(f"{existing}: {len(efaces):,} of {emeta['source_face_count']:,} triangles meet the design area.")
        uncovered = stats["design_area"] - stats["overlay_area"]
        feedback.pushInfo(f"Depth surface '{surface}' − '{existing}': {stats['triangles']:,} triangles over "
                          f"{stats['overlay_area']:,.2f} source units².")
        if uncovered > 1e-6 * max(stats["design_area"], 1.0):
            feedback.pushWarning(f"{uncovered:,.2f} source units² of '{surface}' lie outside '{existing}' and are not counted.")
        meta = dict(meta, depth_from=(surface, existing))
    if feedback.isCanceled():
        raise QgsProcessingException("Cancelled.")
    return transform_vertices(xyz, method=method, **params), faces, meta


FEATURE_LINES_HELP = (
    "\n\nCivil 3D feature lines (optional): a LandXML file of PlanFeatures in the input file's stored "
    "coordinates and units (northing easting elevation for Civil 3D), whatever coordinate interpretation "
    "the GIS outputs use, so it imports back into the source drawing (Insert > LandXML). Daylight lines "
    "outline the earthworks footprint (where design and existing ground differ by more than 0.01 vertical "
    "units); lines shorter than 1 horizontal unit are left out.")


def _feature_lines_param(algorithm, label):
    algorithm.addParameter(QgsProcessingParameterFileDestination(
        "FEATURE_LINES", label, fileFilter="LandXML (*.xml)", optional=True))


def _feature_lines_path(algorithm, p, c):
    return algorithm.parameterAsFileOutput(p, "FEATURE_LINES", c) if "FEATURE_LINES" in p else ""


def _write_feature_lines(out, doc, vertical, features, feedback, group):
    if not out:
        return ""
    if not features:
        feedback.pushWarning("No feature lines were found to write; the feature-line file is empty.")
    try:
        count = write_plan_features(out, features, doc.horizontal_unit, vertical, _epsg_of(doc.root), group)
    except OSError as exc:
        raise QgsProcessingException(f"Could not write '{out}': {exc}") from exc
    feedback.pushInfo(f"{count} feature lines written to {os.path.basename(out)} (source coordinates).")
    return out


def _design_depth(keep):
    """Design vertices (stored coordinates), faces and depth (design − existing) at each vertex.

    Edges and daylight lines are traced on the design TIN itself, whose triangles share edges;
    the overlay of the two surfaces is exact for volumes but its pieces meet at T-junctions."""
    xyz, faces = keep["design"]
    depth = xyz[:, 2] - sample_tin(keep["existing"], xyz)
    covered = np.isfinite(depth)
    faces = faces[covered[faces].all(axis=1)]
    return xyz, faces, np.where(covered, depth, 0.0)


def _split_pair(text):
    """'Design | Existing' → ('Design', 'Existing'); a single name → (name, '')."""
    if "|" in text:
        design, existing = (part.strip() for part in text.split("|", 1))
        return design, existing
    return text.strip(), ""


class GradingQuantitiesAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return GradingQuantitiesAlgorithm()

    def name(self):
        return "landxml_grading_quantities"

    def displayName(self):
        return "Grading Quantities (Volume Surface)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Computes exact cut and fill volumes from a volume (depth) surface, such as a Civil 3D TIN volume "
            "surface exported to LandXML. Depth is design minus existing: positive is fill, negative is cut. "
            "Each triangle is integrated as a linear prism and split exactly at zero depth, so no grid is used. "
            "The tool also reports plan, cut, fill and at-grade areas, maximum depths and the exact plan area in "
            "each depth band. Volumes are in place, with no shrinkage or bulking factors. Triangles hidden by the "
            "surface boundary are excluded.\n\n"
            "Leave the surface blank to choose it only when the file identifies exactly one volume surface (a Civil "
            "3D volume record, or a single surface named as earthworks, volume, cut/fill or difference). "
            "To reconcile with the Civil 3D cut/fill summary, enter its unadjusted (factor 1.0) cut, fill and area "
            "in the reported units; each is checked against the tolerance and the result is PASS or FAIL. "
            "The Helmert option's scale changes plan areas and volumes; depths stay in source vertical units."
            + EXISTING_HELP + QUANTITY_HELP + FEATURE_LINES_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("VOLUME_SURFACE", "Volume (depth) surface, or the design surface when an existing ground is given (blank = detect)", "surfaces"))
        self.addParameter(QgsProcessingParameterFile(
            "EXISTING_FILE", "Existing-ground LandXML (blank = the same file; read only where it meets the design)",
            extension="xml", optional=True))
        self.addParameter(name_param("EXISTING_SURFACE", "Existing-ground surface (blank = the surface above is a volume surface)",
                                     "surfaces", file_param="EXISTING_FILE", fallback_file_param="INPUT"))
        self.addParameter(_volume_parameter())
        self.addParameter(
            QgsProcessingParameterString(
                "DEPTH_BANDS", "Depth band limits (source vertical units; blank = defaults)",
                defaultValue="-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5", optional=True,
            )
        )
        for name, label in (
            ("CIVIL3D_CUT", "Civil 3D cut volume to check (optional)"),
            ("CIVIL3D_FILL", "Civil 3D fill volume to check (optional)"),
            ("CIVIL3D_AREA", "Civil 3D plan area to check (optional)"),
        ):
            self.addParameter(
                QgsProcessingParameterNumber(
                    name, label, type=QgsProcessingParameterNumber.Type.Double, optional=True, minValue=0.0
                )
            )
        self.addParameter(number_param("TOLERANCE", "Reconciliation tolerance (reported units)", 0.01, 0, 1e12, decimals=3))
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
        _feature_lines_param(self, "Daylight lines (Civil 3D feature lines, LandXML; needs design and existing surfaces)")
        self.addParameter(
            QgsProcessingParameterFileDestination("BANDS_CSV", "Depth-band table (CSV)", fileFilter="CSV (*.csv)", optional=True)
        )
        for name, label in (
            ("CUT_VOLUME", "Cut volume"), ("FILL_VOLUME", "Fill volume"), ("NET_VOLUME", "Net volume (fill − cut)"),
            ("PLAN_AREA", "Plan area"), ("CUT_AREA", "Cut area"), ("FILL_AREA", "Fill area"),
        ):
            self.addOutput(QgsProcessingOutputNumber(name, label))
        self.addOutput(QgsProcessingOutputString("CHECK", "Reconciliation (PASS, FAIL or NOT CHECKED)"))
        self.addOutput(QgsProcessingOutputString("VOLUME_UNIT", "Volume unit (CY, CF or m³)"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Grading quantities require an output CRS in metres, feet or US survey feet.")
        existing_path, existing_doc = _existing_file(self, p, c, doc, fb)
        units, _vertical = _grading_units(self, p, c, [doc] + ([existing_doc] if existing_doc else []), fb,
                                          horizontal, _volume_unit(self, p, c))
        try:
            bands = parse_bands(self.parameterAsString(p, "DEPTH_BANDS", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

        catalog = read_catalog(path)
        existing = (_resolve(read_catalog(existing_path or path).surfaces,
                             self.parameterAsString(p, "EXISTING_SURFACE", c).strip(), "existing-ground surface")
                    if self.parameterAsString(p, "EXISTING_SURFACE", c).strip() else "")
        surface = _resolve(catalog.surfaces, self.parameterAsString(p, "VOLUME_SURFACE", c), "volume surface")
        if not surface and existing:
            raise QgsProcessingException("Choose the design surface to compare with the existing ground.")
        if not surface:
            surface, reason = suggest_volume_surface(catalog.surfaces, catalog.surface_volumes)
            if not surface:
                raise QgsProcessingException(
                    f"Choose the volume surface: {reason}. Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
                )
            fb.pushInfo(f"Volume surface chosen automatically: '{surface}' ({reason}).")
        lines_out = _feature_lines_path(self, p, c)
        keep = {} if lines_out and existing else None
        xyz, faces, meta = _depth_surface(path, surface, existing, method, params, fb, existing_path, keep)
        daylight = []
        if lines_out:
            if keep:
                dxyz, dfaces, ddepth = _design_depth(keep)
                daylight = [{"name": f"Daylight {k}", "points": line,
                             "description": f"Earthworks limit of {surface} on {existing}",
                             "properties": {"source": "daylight"}}
                            for k, line in enumerate(footprint_lines(dxyz, dfaces, ddepth), 1)]
            else:
                fb.pushWarning("Daylight feature lines need the design and existing-ground surfaces (a volume "
                               "surface holds depths, not levels); none were written.")
        try:
            summary = volume_summary(xyz, faces, bands)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        warnings = [text for text in (depth_warning(summary),) if text]
        for text in warnings:
            fb.pushWarning(text)
        reported = units.volumes(summary)

        tolerance = self.parameterAsDouble(p, "TOLERANCE", c)
        reference = {
            "cut_volume": _optional_number(p, "CIVIL3D_CUT"),
            "fill_volume": _optional_number(p, "CIVIL3D_FILL"),
            "plan_area": _optional_number(p, "CIVIL3D_AREA"),
        }
        try:
            checks, status = reconcile(reported, reference, tolerance)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

        fb.pushInfo(f"Cut {reported['cut_volume']:,.3f} {units.volume}; fill {reported['fill_volume']:,.3f} {units.volume}; "
                    f"net {reported['net_volume']:,.3f} {units.volume} (fill − cut).")
        fb.pushInfo(f"Plan area {reported['plan_area']:,.3f} {units.area} (cut {reported['cut_area']:,.3f}, "
                    f"fill {reported['fill_area']:,.3f}, at grade {reported['grade_area']:,.3f}); "
                    f"{summary['triangle_count']:,} triangles.")
        for key, value, ref, difference, state in checks:
            fb.pushInfo(f"Check {key.replace('_', ' ')}: {value:,.3f} vs {ref:,.3f} (difference {difference:+,.3f}) {state}")
        if checks:
            (fb.pushInfo if status == "PASS" else fb.pushWarning)(f"Reconciliation: {status} (tolerance ±{tolerance:g}).")

        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, grading_report_html(
                reported, f"{surface} − {existing}" if existing else surface, units, os.path.basename(path), checks, status,
                tolerance, warnings))
        bands_csv = self.parameterAsFileOutput(p, "BANDS_CSV", c)
        if bands_csv:
            try:
                with open(bands_csv, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["band", f"depth_from_{units.depth}", f"depth_to_{units.depth}", f"area_{units.area}"])
                    for row in summary["bands"]:
                        writer.writerow([row["band"], "" if row["low"] is None else row["low"],
                                         "" if row["high"] is None else row["high"], f"{row['area']:.6f}"])
            except OSError as exc:
                raise QgsProcessingException(f"Could not write '{bands_csv}': {exc}") from exc
        return {
            "CUT_VOLUME": reported["cut_volume"], "FILL_VOLUME": reported["fill_volume"],
            "NET_VOLUME": reported["net_volume"], "PLAN_AREA": reported["plan_area"],
            "CUT_AREA": reported["cut_area"], "FILL_AREA": reported["fill_area"],
            "CHECK": status, "VOLUME_UNIT": units.volume,
            "REPORT": report or "", "BANDS_CSV": bands_csv or "",
            "FEATURE_LINES": _write_feature_lines(lines_out, doc, _vertical, daylight, fb, "Daylight lines")
            if lines_out and keep else "",
        }


PLATFORM_FIELDS = (
    ("platform", FIELD_STRING), ("area", FIELD_DOUBLE), ("level_mean", FIELD_DOUBLE),
    ("level_min", FIELD_DOUBLE), ("level_max", FIELD_DOUBLE), ("fall_pct", FIELD_DOUBLE),
    ("fall_bearing", FIELD_DOUBLE), ("mean_grade_pct", FIELD_DOUBLE), ("flatter_than_min_pct", FIELD_DOUBLE),
    ("below_min_fall", FIELD_INT), ("triangles", FIELD_INT),
)


def _polygon(polygons):
    parts = [[[QgsPointXY(float(x), float(y)) for x, y in ring] + [QgsPointXY(float(ring[0][0]), float(ring[0][1]))]
              for ring in [outer, *holes]] for outer, holes in polygons]
    return QgsGeometry.fromMultiPolygonXY(parts)


class PlatformLevelsAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return PlatformLevelsAlgorithm()

    def name(self):
        return "landxml_platform_levels"

    def displayName(self):
        return "Platform Levels and Falls"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Finds the platforms on a design (finished-level) TIN surface and checks their falls. Each triangle is "
            "classed by slope as flat (below the flat threshold), sloping (up to 30 %), batter (30 % to 250 %, "
            "1V:3.3H to 1V:0.4H) or near-vertical (retained edges). Flat triangles that share an edge form a "
            "platform. For each platform the tool reports area, mean, minimum and maximum level, the overall fall "
            "and the direction it falls towards (bearing in the output coordinates, from an area-weighted plane "
            "fit), the mean local grade and the share of its area flatter than the minimum fall. Platforms whose "
            "overall fall is below the minimum are flagged, because water can pond on them.\n\n"
            "Outputs are platform polygons with these fields and an HTML report with the area in each slope class. "
            "Slopes account for the horizontal and vertical units; the Helmert option's scale changes plan "
            "distances. Leave the surface blank only when the file has a single surface." + FEATURE_LINES_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("DESIGN_SURFACE", "Design (finished-level) surface", "surfaces"))
        self.addParameter(number_param("FLAT_PCT", "Flat threshold (% slope)", 2.0, 0.01, 30.0, decimals=2))
        self.addParameter(number_param("MIN_FALL_PCT", "Minimum platform fall (%)", 0.5, 0.0, 30.0, decimals=2))
        self.addParameter(number_param("MIN_AREA", "Smallest platform to list (output area units)", 50.0, 0.0, 1e12, decimals=1))
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(
            QgsProcessingParameterFeatureSink("OUTPUT", "Platforms", type=QgsProcessing.SourceType.TypeVectorPolygon)
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
        _feature_lines_param(self, "Platform outlines (Civil 3D feature lines, LandXML)")
        self.addOutput(QgsProcessingOutputNumber("PLATFORM_COUNT", "Platforms listed"))
        self.addOutput(QgsProcessingOutputNumber("FLAGGED_COUNT", "Platforms below the minimum fall"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Platform falls require an output CRS in metres, feet or US survey feet.")
        units, vertical = _grading_units(self, p, c, [doc], fb, horizontal)
        vertical_factor = LINEAR_UNITS[vertical.lower()][0] / LINEAR_UNITS[horizontal.lower()][0]

        catalog = read_catalog(path)
        requested = self.parameterAsString(p, "DESIGN_SURFACE", c).strip()
        if requested:
            surface = _resolve(catalog.surfaces, requested, "design surface")
        elif len(catalog.surfaces) == 1:
            surface = catalog.surfaces[0]
        else:
            raise QgsProcessingException(
                f"Choose the design (finished-level) surface. Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
            )
        try:
            xyz, faces, meta = read_tin(path, surface, cancel=fb.isCanceled)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if meta["invisible_face_count"]:
            fb.pushInfo(f"{surface}: skipped {meta['invisible_face_count']:,} hidden triangles.")
        if fb.isCanceled():
            raise QgsProcessingException("Platform levels cancelled.")
        try:
            summary = platform_summary(
                transform_vertices(xyz, method=method, **params), faces, vertical_factor,
                self.parameterAsDouble(p, "FLAT_PCT", c) / 100.0,
                self.parameterAsDouble(p, "MIN_FALL_PCT", c) / 100.0,
                self.parameterAsDouble(p, "MIN_AREA", c),
            )
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        warnings = []
        if summary["plan_area"] and summary["class_areas"]["flat"] / summary["plan_area"] < 0.05:
            warnings.append("Less than 5 % of this surface is flat. Check that it is the design (finished-level) "
                            "surface, not existing ground or a volume surface.")
        for text in warnings:
            fb.pushWarning(text)

        fields = QgsFields()
        for name, kind in PLATFORM_FIELDS:
            fields.append(QgsField(name, kind))
        sink, dest = self.parameterAsSink(p, "OUTPUT", c, fields, QgsWkbTypes.Type.MultiPolygon, crs)
        if sink is None:
            raise QgsProcessingException("Could not create the platforms output.")
        for item in summary["platforms"]:
            feature = QgsFeature(fields)
            feature.setGeometry(_polygon(item["polygons"]))
            feature.setAttributes([
                item["id"], item["area"], item["level_mean"], item["level_min"], item["level_max"],
                100.0 * item["fall"], item["fall_bearing"], 100.0 * item["mean_grade"],
                100.0 * item["share_below_min_fall"], int(item["below_min_fall"]), item["triangles"],
            ])
            sink.addFeature(feature)
            bearing = "" if item["fall_bearing"] is None else f" towards {item['fall_bearing']:.0f}°"
            fb.pushInfo(f"{item['id']}: {item['area']:,.1f} {units.area}, level {item['level_mean']:.3f} "
                        f"({item['level_min']:.3f}–{item['level_max']:.3f}) {units.depth}, fall {100 * item['fall']:.2f} %"
                        f"{bearing}" + ("; below minimum fall" if item["below_min_fall"] else ""))
        flagged = sum(1 for item in summary["platforms"] if item["below_min_fall"])
        fb.pushInfo(f"{len(summary['platforms'])} platforms; {flagged} below the minimum fall.")
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, platform_report_html(summary, surface, units, os.path.basename(path), warnings))
        lines_out = _feature_lines_path(self, p, c)
        outlines = []
        if lines_out:
            for item in summary["platforms"]:
                for k, (outer, holes) in enumerate(outline_polygons(xyz, faces[item["members"]], dims=3), 1):
                    for h, ring in enumerate([outer, *holes]):
                        suffix = (f" part {k}" if len(item["polygons"]) > 1 else "") + (f" hole {h}" if h else "")
                        outlines.append({"name": f"{item['id']} outline{suffix}", "points": np.vstack([ring, ring[:1]]),
                                         "description": f"Platform {item['id']}, mean level {item['level_mean']:.3f}",
                                         "properties": {"platform": item["id"]}})
        return {"OUTPUT": dest, "REPORT": report or "", "PLATFORM_COUNT": len(summary["platforms"]),
                "FLAGGED_COUNT": flagged,
                "FEATURE_LINES": _write_feature_lines(lines_out, doc, vertical, outlines, fb, "Platform outlines")}


class BasinStorageAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return BasinStorageAlgorithm()

    def name(self):
        return "landxml_basin_storage"

    def displayName(self):
        return "Basin Stage–Storage"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Builds the stage–storage table of a pond or basin from its TIN surface, such as a Civil 3D surface "
            "of the pond grading. The storage below each level is integrated exactly over the triangles (water "
            "depth is linear on each triangle), so no grid is used. The table runs from the basin floor to the "
            "rim in the chosen step, and also lists the water area at each level.\n\n"
            "Leave the rim blank to use the spill level: the lowest point on the surface's outer edge, where "
            "water would overflow. A rim above the spill level is flagged. With a required volume the tool checks "
            "the storage with freeboard (rim minus freeboard) and reports PASS, PASS TO RIM ONLY (enough only "
            "when filled to the rim), FAIL or NOT CHECKED. Enter the required volume in the reported units."
            + QUANTITY_HELP + FEATURE_LINES_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("BASIN_SURFACE", "Basin (pond) surface", "surfaces"))
        self.addParameter(
            QgsProcessingParameterNumber("RIM_LEVEL", "Rim level (source vertical units; blank = spill level)",
                                         type=QgsProcessingParameterNumber.Type.Double, optional=True)
        )
        self.addParameter(number_param("FREEBOARD", "Freeboard (source vertical units)", 0.5, 0.0, 1000.0, decimals=3))
        self.addParameter(
            QgsProcessingParameterNumber("REQUIRED_VOLUME", "Required storage (reported units; optional)",
                                         type=QgsProcessingParameterNumber.Type.Double, optional=True, minValue=0.0)
        )
        self.addParameter(number_param("STAGE_STEP", "Stage step (source vertical units)", 0.25, 0.001, 1000.0, decimals=3))
        self.addParameter(_volume_parameter())
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
        _feature_lines_param(self, "Pond rim and design water level (Civil 3D feature lines, LandXML)")
        self.addParameter(
            QgsProcessingParameterFileDestination("TABLE_CSV", "Stage–storage table (CSV)", fileFilter="CSV (*.csv)", optional=True)
        )
        for name, label in (("RIM_VOLUME", "Storage to the rim"), ("FREEBOARD_VOLUME", "Storage with freeboard"),
                            ("RIM_LEVEL_USED", "Rim level used"), ("SPILL_LEVEL", "Spill level"),
                            ("FLOOR_LEVEL", "Floor level")):
            self.addOutput(QgsProcessingOutputNumber(name, label))
        self.addOutput(QgsProcessingOutputString("CHECK", "Capacity (PASS, PASS TO RIM ONLY, FAIL or NOT CHECKED)"))
        self.addOutput(QgsProcessingOutputString("VOLUME_UNIT", "Volume unit (CY, CF or m³)"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Basin storage requires an output CRS in metres, feet or US survey feet.")
        units, _vertical = _grading_units(self, p, c, [doc], fb, horizontal, _volume_unit(self, p, c))
        catalog = read_catalog(path)
        requested = self.parameterAsString(p, "BASIN_SURFACE", c).strip()
        if not requested:
            raise QgsProcessingException(
                f"Choose the basin (pond) surface. Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
            )
        surface = _resolve(catalog.surfaces, requested, "basin surface")
        try:
            xyz, faces, meta = read_tin(path, surface, cancel=fb.isCanceled)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if meta["invisible_face_count"]:
            fb.pushInfo(f"{surface}: skipped {meta['invisible_face_count']:,} hidden triangles.")
        required = _optional_number(p, "REQUIRED_VOLUME")
        try:
            summary = stage_storage(
                transform_vertices(xyz, method=method, **params), faces, _optional_number(p, "RIM_LEVEL"),
                self.parameterAsDouble(p, "FREEBOARD", c), self.parameterAsDouble(p, "STAGE_STEP", c),
                None if required is None else required / units.volume_factor,
            )
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        warnings = []
        if summary["rim"] > summary["spill"] + 1e-9:
            warnings.append(f"The rim ({summary['rim']:.3f}) is above the spill level ({summary['spill']:.3f}): water "
                            "overflows the surface edge first, so storage above the spill level is not available "
                            "unless the basin is closed beyond this surface.")
        for text in warnings:
            fb.pushWarning(text)
        factor = units.volume_factor
        reported = dict(summary, rim_volume=summary["rim_volume"] * factor,
                        design_volume=summary["design_volume"] * factor, required_volume=required,
                        rows=[dict(row, volume=row["volume"] * factor) for row in summary["rows"]])
        fb.pushInfo(f"{surface}: floor {summary['floor']:.3f}, spill {summary['spill']:.3f}, rim {summary['rim']:.3f} {units.depth}.")
        fb.pushInfo(f"Storage to the rim {reported['rim_volume']:,.3f} {units.volume}; with {summary['freeboard']:g} "
                    f"{units.depth} freeboard {reported['design_volume']:,.3f} {units.volume}.")
        if required is not None:
            (fb.pushInfo if summary["status"] == "PASS" else fb.pushWarning)(
                f"Capacity {summary['status']}: required {required:,.3f} {units.volume}.")
        lines_out = _feature_lines_path(self, p, c)
        rims = []
        if lines_out:
            for label, level in (("Rim", summary["rim"]), ("Design water level", summary["design_level"])):
                if label != "Rim" and not (summary["freeboard"] and level > summary["floor"]):
                    continue
                for k, line in enumerate(level_lines(xyz, faces, level), 1):
                    line[:, 2] = level
                    rims.append({"name": f"{surface} {label.lower()} {k}", "points": line,
                                 "description": f"{label} {level:.3f}", "properties": {"level": f"{level:.3f}"}})
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, basin_report_html(reported, surface, units, os.path.basename(path), warnings))
        table = self.parameterAsFileOutput(p, "TABLE_CSV", c)
        if table:
            try:
                with open(table, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow([f"level_{units.depth}", f"depth_{units.depth}", f"water_area_{units.area}",
                                     f"storage_{units.volume}"])
                    for row in reported["rows"]:
                        writer.writerow([f"{row['level']:.4f}", f"{row['depth']:.4f}", f"{row['area']:.4f}", f"{row['volume']:.4f}"])
            except OSError as exc:
                raise QgsProcessingException(f"Could not write '{table}': {exc}") from exc
        return {
            "RIM_VOLUME": reported["rim_volume"], "FREEBOARD_VOLUME": reported["design_volume"],
            "RIM_LEVEL_USED": summary["rim"], "SPILL_LEVEL": summary["spill"], "FLOOR_LEVEL": summary["floor"],
            "CHECK": summary["status"], "VOLUME_UNIT": units.volume, "REPORT": report or "", "TABLE_CSV": table or "",
            "FEATURE_LINES": _write_feature_lines(lines_out, doc, _vertical, rims, fb, "Pond rims"),
        }


class EarthworkBalanceAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return EarthworkBalanceAlgorithm()

    def name(self):
        return "landxml_earthwork_balance"

    def displayName(self):
        return "Earthwork Balance (Volume Surfaces)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Balances cut and fill across one or more volume (depth) surfaces, each treated as a site, under "
            "shrinkage scenarios. Cut and fill per site are the exact in-place volumes used by Grading Quantities. "
            "A shrinkage factor is the compacted volume from one unit of in-place cut (default 0.85, 0.90 and "
            "0.95). Each site uses its own cut first; surplus from other sites then supplies the shortfalls. The "
            "report gives, for each factor, the volume moved between sites, the import still needed (compacted, "
            "and its in-place borrow equivalent) and the spoil (in place).\n\n"
            "List the surfaces separated by semicolons, or leave blank to use every surface named as a volume "
            "surface (earthworks, volume, cut/fill, difference). An unsuitable share of cut is removed before "
            "balancing and goes to spoil. Topsoil stripping (plan area × depth) is reported separately and not "
            "used as fill.\n\nWithout volume surfaces (OpenRoads), give each site as 'Design | Existing', e.g. "
            "'Site 1 FG | Site 1 EG; Site 2 FG | EG': the exact depth surface is built from the two TINs on the area "
            "both cover."
            + QUANTITY_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(QgsProcessingParameterString(
            "VOLUME_SURFACES", "Volume surfaces, one per site, or 'Design | Existing' pairs (separate with ; — blank = all named as volume surfaces)",
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterFile(
            "EXISTING_FILE", "Existing-ground LandXML for the 'Design | Existing' pairs (blank = the same file)",
            extension="xml", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "SHRINKAGE", "Shrinkage factors, compacted per in-place (blank = 0.85, 0.90, 0.95)",
            defaultValue="0.85, 0.90, 0.95", optional=True))
        self.addParameter(number_param("UNSUITABLE_PCT", "Unsuitable share of cut (%)", 0.0, 0.0, 99.0, decimals=1))
        self.addParameter(number_param("TOPSOIL_DEPTH", "Topsoil stripping depth (source vertical units; 0 = none)", 0.0, 0.0, 100.0, decimals=3))
        self.addParameter(_volume_parameter())
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("TABLE_CSV", "Balance table (CSV)", fileFilter="CSV (*.csv)", optional=True)
        )
        for name, label in (("TOTAL_CUT", "Total cut (in place)"), ("TOTAL_FILL", "Total fill"),
                            ("NET_VOLUME", "Net (fill − cut, in place)"), ("SITE_COUNT", "Sites")):
            self.addOutput(QgsProcessingOutputNumber(name, label))
        self.addOutput(QgsProcessingOutputString("VOLUME_UNIT", "Volume unit (CY, CF or m³)"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Earthwork balance requires an output CRS in metres, feet or US survey feet.")
        existing_path, existing_doc = _existing_file(self, p, c, doc, fb)
        units, _vertical = _grading_units(self, p, c, [doc] + ([existing_doc] if existing_doc else []), fb,
                                          horizontal, _volume_unit(self, p, c))
        try:
            factors = parse_factors(self.parameterAsString(p, "SHRINKAGE", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        catalog = read_catalog(path)
        requested = [name.strip() for name in self.parameterAsString(p, "VOLUME_SURFACES", c).split(";") if name.strip()]
        pairs = []
        if requested:
            for entry in requested:
                design, existing = _split_pair(entry)
                pairs.append((_resolve(catalog.surfaces, design, "volume or design surface"),
                              _resolve(read_catalog(existing_path or path).surfaces, existing, "existing-ground surface")
                              if existing else ""))
            names = [f"{d} − {e}" if e else d for d, e in pairs]
        else:
            names = [name for name in catalog.surfaces if any(hint in name.lower() for hint in VOLUME_SURFACE_HINTS)]
            if not names:
                raise QgsProcessingException(
                    "No surface is named as a volume surface; list the volume surfaces separated by semicolons. "
                    f"Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
                )
            fb.pushInfo("Volume surfaces chosen by name: " + "; ".join(names) + ".")
            pairs = [(name, "") for name in names]
        if len(set(names)) != len(names):
            raise QgsProcessingException("Each volume surface may be listed only once.")
        sites = []
        warnings = []
        for name, (design, existing) in zip(names, pairs):
            if fb.isCanceled():
                raise QgsProcessingException("Earthwork balance cancelled.")
            xyz, faces, _meta = _depth_surface(path, design, existing, method, params, fb, existing_path)
            try:
                summary = volume_summary(xyz, faces, ())
            except ValueError as exc:
                raise QgsProcessingException(f"{name}: {exc}") from exc
            text = depth_warning(summary)
            if text:
                warnings.append(f"{name}: {text}")
            sites.append({"name": name, "cut": summary["cut_volume"] * units.volume_factor,
                          "fill": summary["fill_volume"] * units.volume_factor, "area": summary["plan_area"]})
        for text in warnings:
            fb.pushWarning(text)
        topsoil_depth = self.parameterAsDouble(p, "TOPSOIL_DEPTH", c)
        try:
            balance = earthwork_balance(sites, factors, self.parameterAsDouble(p, "UNSUITABLE_PCT", c) / 100.0, topsoil_depth)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        # topsoil = area (h units²) × depth (v units): express in the reported volume unit
        balance["topsoil"] = sum(site["area"] for site in sites) * topsoil_depth * units.volume_factor
        for site in sites:
            fb.pushInfo(f"{site['name']}: cut {site['cut']:,.3f}, fill {site['fill']:,.3f} {units.volume} (in place).")
        for item in balance["scenarios"]:
            fb.pushInfo(f"Factor {item['factor']:.2f}: moved between sites {item['transfer']:,.2f}, import "
                        f"{item['import_compacted']:,.2f} compacted ({item['import_in_place']:,.2f} in place), spoil "
                        f"{item['spoil_in_place']:,.2f} {units.volume} in place.")
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, balance_report_html(balance, units, os.path.basename(path), warnings))
        table = self.parameterAsFileOutput(p, "TABLE_CSV", c)
        if table:
            try:
                with open(table, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    # TOTAL rows: surplus = spoil and shortfall = import after site-to-site transfer (compacted)
                    writer.writerow(["factor", "site", f"cut_in_place_{units.volume}", f"usable_compacted_{units.volume}",
                                     f"fill_{units.volume}", f"surplus_{units.volume}", f"shortfall_{units.volume}"])
                    for item in balance["scenarios"]:
                        for site, row in zip(sites, item["sites"]):
                            writer.writerow([f"{item['factor']:.3f}", row["name"], f"{site['cut']:.3f}", f"{row['usable']:.3f}",
                                             f"{row['fill']:.3f}", f"{row['surplus']:.3f}", f"{row['shortfall']:.3f}"])
                        writer.writerow([f"{item['factor']:.3f}", "TOTAL", f"{balance['cut']:.3f}",
                                         f"{sum(r['usable'] for r in item['sites']):.3f}", f"{balance['fill']:.3f}",
                                         f"{item['spoil_in_place'] * item['factor']:.3f}", f"{item['import_compacted']:.3f}"])
            except OSError as exc:
                raise QgsProcessingException(f"Could not write '{table}': {exc}") from exc
        return {"TOTAL_CUT": balance["cut"], "TOTAL_FILL": balance["fill"], "NET_VOLUME": balance["fill"] - balance["cut"],
                "SITE_COUNT": len(sites), "VOLUME_UNIT": units.volume, "REPORT": report or "", "TABLE_CSV": table or ""}


def _zone_polygons(source, field, crs, context):
    """[(name, [(outer, [holes]), ...]), ...] from a polygon source, in ``crs``."""
    transform = None
    if source.sourceCrs().isValid() and source.sourceCrs() != crs:
        transform = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
    zones, seen = [], {}
    for number, feature in enumerate(source.getFeatures(), 1):
        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            continue
        if transform is not None:
            geometry.transform(transform)
        parts = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
        polygons = [([(p.x(), p.y()) for p in rings[0]], [[(p.x(), p.y()) for p in ring] for ring in rings[1:]])
                    for rings in parts if rings]
        name = str(feature[field]) if field else f"Zone {number}"
        if name in seen:
            zones[seen[name]][1].extend(polygons)
        else:
            seen[name] = len(zones)
            zones.append((name, polygons))
    return zones


COMPARISON_FIELDS = (("zone", FIELD_STRING), ("cut_a", FIELD_DOUBLE), ("fill_a", FIELD_DOUBLE), ("cut_b", FIELD_DOUBLE),
                     ("fill_b", FIELD_DOUBLE), ("cut_change", FIELD_DOUBLE), ("fill_change", FIELD_DOUBLE),
                     ("net_change", FIELD_DOUBLE), ("area_a", FIELD_DOUBLE), ("area_b", FIELD_DOUBLE))


class RevisionComparisonAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return RevisionComparisonAlgorithm()

    def name(self):
        return "landxml_grading_revision_comparison"

    def displayName(self):
        return "Grading Revision Comparison (Volume Surfaces)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return (
            "Compares two grading revisions through their volume (depth) surfaces, from one LandXML file or two. "
            "Cut and fill of each revision are the exact in-place volumes used by Grading Quantities; the table "
            "gives both revisions and the change (second minus first) overall and, with a zone polygon layer, by "
            "zone. Triangles are assigned to zones by their centroid, so zone totals near zone edges are "
            "approximate while the overall totals are exact.\n\n"
            "The optional change raster maps the depth change (positive = more fill or less cut); where only one "
            "revision has earthworks, the other counts as zero depth. A change can also come from a revised "
            "existing-ground surface: compare the existing-ground surfaces with Surface Cut/Fill before "
            "attributing a change to the design. Use the same coordinate interpretation for both files."
            + EXISTING_HELP.replace("enter the design surface and the existing-ground surface", "enter each revision's design surface and its existing-ground surface")
            + QUANTITY_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("SURFACE_A", "First revision: volume surface (or design surface)", "surfaces"))
        self.addParameter(QgsProcessingParameterFile(
            "EXISTING_FILE", "Existing-ground LandXML for both revisions (blank = each revision's own file)",
            extension="xml", optional=True))
        self.addParameter(name_param("EXISTING_A", "First revision: existing-ground surface (blank = volume surface above)",
                                     "surfaces", file_param="EXISTING_FILE", fallback_file_param="INPUT"))
        self.addParameter(QgsProcessingParameterFile(
            "INPUT_B", "Second revision LandXML (blank = same file)", extension="xml", optional=True))
        self.addParameter(QgsProcessingParameterString("SURFACE_B", "Second revision: volume surface (or design surface)"))
        self.addParameter(QgsProcessingParameterString("EXISTING_B", "Second revision: existing-ground surface (blank = volume surface above)",
                                                       defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString("LABEL_A", "First revision label", defaultValue="Revision A"))
        self.addParameter(QgsProcessingParameterString("LABEL_B", "Second revision label", defaultValue="Revision B"))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "ZONES", "Zones (optional polygons)", [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterField(
            "ZONE_FIELD", "Zone name field (blank = numbered)", parentLayerParameterName="ZONES", optional=True))
        self.addParameter(_volume_parameter())
        _param_coordinates(self)
        _vertical_unit_param(self)
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Comparison table", type=QgsProcessing.SourceType.TypeVector))
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            "CHANGE_RASTER", "Depth change raster (second − first)", optional=True, createByDefault=False))
        self.addParameter(number_param("RESOLUTION", "Change raster cell size (output units)", 1.0, 0.01, 1000.0, decimals=3))
        for name, label in (("CUT_CHANGE", "Cut change"), ("FILL_CHANGE", "Fill change"), ("NET_CHANGE", "Net change")):
            self.addOutput(QgsProcessingOutputNumber(name, label))
        self.addOutput(QgsProcessingOutputString("VOLUME_UNIT", "Volume unit (CY, CF or m³)"))

    def processAlgorithm(self, p, c, fb):
        from .landxml.parser import load_document

        path_a = self.parameterAsFile(p, "INPUT", c)
        if not path_a or not os.path.isfile(path_a):
            raise QgsProcessingException("First revision LandXML file does not exist.")
        path_b = self.parameterAsFile(p, "INPUT_B", c) or path_a
        if not os.path.isfile(path_b):
            raise QgsProcessingException("Second revision LandXML file does not exist.")
        method, crs, _src, params, doc = coordinate_choices(self, p, c, fb, path_a)
        documents = [doc]
        if path_b != path_a:
            other = load_document(path_b)
            if other.horizontal_unit != doc.horizontal_unit:
                raise QgsProcessingException(
                    f"The two files declare different horizontal units ({doc.horizontal_unit} and {other.horizontal_unit}); "
                    "prepare them with the same units first.")
            documents.append(other)
        horizontal = HORIZONTAL_UNITS.get(crs.mapUnits())
        if horizontal is None:
            raise QgsProcessingException("Revision comparison requires an output CRS in metres, feet or US survey feet.")
        existing_path, existing_doc = _existing_file(self, p, c, doc, fb)
        if existing_doc:
            documents.append(existing_doc)
        units, _vertical = _grading_units(self, p, c, documents, fb, horizontal, _volume_unit(self, p, c))
        surfaces = []
        for path, key, existing_key in ((path_a, "SURFACE_A", "EXISTING_A"), (path_b, "SURFACE_B", "EXISTING_B")):
            requested = self.parameterAsString(p, key, c).strip()
            catalog = read_catalog(path)
            if not requested:
                raise QgsProcessingException(f"Choose the volume surface for both revisions. Surfaces in "
                                             f"{os.path.basename(path)}: {', '.join(catalog.surfaces) or 'none'}.")
            name = _resolve(catalog.surfaces, requested, "volume surface")
            existing_name = self.parameterAsString(p, existing_key, c).strip()
            existing_name = (_resolve(read_catalog(existing_path or path).surfaces, existing_name, "existing-ground surface")
                             if existing_name else "")
            xyz, faces, _meta = _depth_surface(path, name, existing_name, method, params, fb, existing_path)
            surfaces.append((xyz, faces))
            if fb.isCanceled():
                raise QgsProcessingException("Revision comparison cancelled.")
        zones = []
        source = self.parameterAsSource(p, "ZONES", c)
        if source is not None:
            zones = _zone_polygons(source, self.parameterAsString(p, "ZONE_FIELD", c), crs, c)
            fb.pushInfo(f"{len(zones)} zones.")
        try:
            comparison = scale_comparison(revision_comparison(surfaces[0], surfaces[1], zones), units.volume_factor)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        labels = (self.parameterAsString(p, "LABEL_A", c) or "Revision A", self.parameterAsString(p, "LABEL_B", c) or "Revision B")
        fields = QgsFields()
        for name, kind in COMPARISON_FIELDS:
            fields.append(QgsField(name, kind))
        sink, dest = self.parameterAsSink(p, "OUTPUT", c, fields, QgsWkbTypes.Type.NoGeometry, crs)
        if sink is None:
            raise QgsProcessingException("Could not create the comparison table.")
        for row in comparison["zones"] + [comparison["total"]]:
            feature = QgsFeature(fields)
            feature.setAttributes([row["name"], row["cut_a"], row["fill_a"], row["cut_b"], row["fill_b"], row["cut_change"],
                                   row["fill_change"], row["net_change"], row["area_a"], row["area_b"]])
            sink.addFeature(feature)
        total = comparison["total"]
        fb.pushInfo(f"{labels[0]}: cut {total['cut_a']:,.2f}, fill {total['fill_a']:,.2f}; {labels[1]}: cut {total['cut_b']:,.2f}, "
                    f"fill {total['fill_b']:,.2f} {units.volume}. Change: cut {total['cut_change']:+,.2f}, fill "
                    f"{total['fill_change']:+,.2f}, net {total['net_change']:+,.2f} {units.volume}.")
        raster = self.parameterAsOutputLayer(p, "CHANGE_RASTER", c) if p.get("CHANGE_RASTER") else ""
        if raster:
            resolution = self.parameterAsDouble(p, "RESOLUTION", c)
            (xa, fa), (xb, fbb) = surfaces
            ea, eb = bounds(xa), bounds(xb)
            grid = grid_for_bounds((min(ea[0], eb[0]), min(ea[1], eb[1]), max(ea[2], eb[2]), max(ea[3], eb[3])), resolution)
            nodata = -9999.0
            try:
                first, xmin, ymax, _ = rasterize_tin(xa, fa, resolution, nodata, cancel=fb.isCanceled, grid=grid,
                                                     warning=fb.pushWarning)
                second, _, _, _ = rasterize_tin(xb, fbb, resolution, nodata, cancel=fb.isCanceled, grid=grid, warn_cells=0)
            except ValueError as exc:
                raise QgsProcessingException(str(exc)) from exc
            write_geotiff(raster, change_grid(first, second, nodata), xmin, ymax, resolution, nodata, wkt=crs.toWkt())
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            _write_text(report, comparison_report_html(comparison, labels, units,
                                                       (os.path.basename(path_a), os.path.basename(path_b))))
        return {"OUTPUT": dest, "REPORT": report or "", "CHANGE_RASTER": raster, "CUT_CHANGE": total["cut_change"],
                "FILL_CHANGE": total["fill_change"], "NET_CHANGE": total["net_change"], "VOLUME_UNIT": units.volume}
