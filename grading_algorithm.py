"""Grading Processing tools.

* Grading Quantities (Volume Surface): exact cut/fill volumes, plan areas and
  depth bands from a volume (depth) TIN such as a Civil 3D TIN volume surface,
  with an optional check against the Civil 3D cut/fill summary.
* Platform Levels and Falls: slope classes, platforms, levels and falls of a
  design (finished-level) surface.
* Earthwork Balance: cut/fill balance of one or more volume surfaces under
  shrinkage scenarios, with site-to-site transfer, import and spoil.
* Basin Stage–Storage: exact storage below each level of a basin surface, with a
  capacity check at the rim and with freeboard.
"""

from __future__ import annotations

import csv
import os

from qgis.core import (
    Qgis,
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
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from .compat import FIELD_DOUBLE, FIELD_INT, FIELD_STRING
from .core import read_tin, transform_vertices
from .earthworks_algorithm import (
    GROUP,
    GROUP_ID,
    QUANTITY_HELP,
    _input_param,
    _quantity_units,
    _resolve,
    _volume_parameter,
    _volume_unit,
    _write_text,
)
from .landxml.catalog import read_catalog
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.grading import (
    depth_warning,
    earthwork_balance,
    grading_report_html,
    parse_bands,
    parse_factors,
    VOLUME_SURFACE_HINTS,
    balance_report_html,
    basin_report_html,
    platform_report_html,
    platform_summary,
    reconcile,
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
            + QUANTITY_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("VOLUME_SURFACE", "Volume (depth) surface (blank = detect)", "surfaces"))
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
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
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
        units = _quantity_units(doc, fb, horizontal, allow_mixed=True, volume_unit=_volume_unit(self, p, c))
        try:
            bands = parse_bands(self.parameterAsString(p, "DEPTH_BANDS", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

        catalog = read_catalog(path)
        surface = _resolve(catalog.surfaces, self.parameterAsString(p, "VOLUME_SURFACE", c), "volume surface")
        if not surface:
            surface, reason = suggest_volume_surface(catalog.surfaces, catalog.surface_volumes)
            if not surface:
                raise QgsProcessingException(
                    f"Choose the volume surface: {reason}. Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
                )
            fb.pushInfo(f"Volume surface chosen automatically: '{surface}' ({reason}).")
        try:
            xyz, faces, meta = read_tin(path, surface, cancel=fb.isCanceled)
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        if meta["invisible_face_count"]:
            fb.pushInfo(f"{surface}: skipped {meta['invisible_face_count']:,} hidden triangles.")
        if fb.isCanceled():
            raise QgsProcessingException("Grading quantities cancelled.")
        try:
            summary = volume_summary(transform_vertices(xyz, method=method, **params), faces, bands)
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
                reported, surface, units, os.path.basename(path), checks, status, tolerance, warnings))
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
            "distances. Leave the surface blank only when the file has a single surface."
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(name_param("DESIGN_SURFACE", "Design (finished-level) surface", "surfaces"))
        self.addParameter(number_param("FLAT_PCT", "Flat threshold (% slope)", 2.0, 0.01, 30.0, decimals=2))
        self.addParameter(number_param("MIN_FALL_PCT", "Minimum platform fall (%)", 0.5, 0.0, 30.0, decimals=2))
        self.addParameter(number_param("MIN_AREA", "Smallest platform to list (output area units)", 50.0, 0.0, 1e12, decimals=1))
        _param_coordinates(self)
        self.addParameter(
            QgsProcessingParameterFeatureSink("OUTPUT", "Platforms", type=QgsProcessing.SourceType.TypeVectorPolygon)
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
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
        units = _quantity_units(doc, fb, horizontal, allow_mixed=True)
        vertical_factor = LINEAR_UNITS[doc.vertical_unit.lower()][0] / LINEAR_UNITS[horizontal.lower()][0]

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
        return {"OUTPUT": dest, "REPORT": report or "", "PLATFORM_COUNT": len(summary["platforms"]),
                "FLAGGED_COUNT": flagged}


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
            + QUANTITY_HELP
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
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML report", fileFilter="HTML (*.html)", optional=True)
        )
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
        units = _quantity_units(doc, fb, horizontal, allow_mixed=True, volume_unit=_volume_unit(self, p, c))
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
            "used as fill."
            + QUANTITY_HELP
        )

    def initAlgorithm(self, config=None):
        _input_param(self)
        self.addParameter(QgsProcessingParameterString(
            "VOLUME_SURFACES", "Volume surfaces, one per site (separate with ; — blank = all named as volume surfaces)",
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "SHRINKAGE", "Shrinkage factors, compacted per in-place (blank = 0.85, 0.90, 0.95)",
            defaultValue="0.85, 0.90, 0.95", optional=True))
        self.addParameter(number_param("UNSUITABLE_PCT", "Unsuitable share of cut (%)", 0.0, 0.0, 99.0, decimals=1))
        self.addParameter(number_param("TOPSOIL_DEPTH", "Topsoil stripping depth (source vertical units; 0 = none)", 0.0, 0.0, 100.0, decimals=3))
        self.addParameter(_volume_parameter())
        _param_coordinates(self)
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
        units = _quantity_units(doc, fb, horizontal, allow_mixed=True, volume_unit=_volume_unit(self, p, c))
        try:
            factors = parse_factors(self.parameterAsString(p, "SHRINKAGE", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        catalog = read_catalog(path)
        requested = [name.strip() for name in self.parameterAsString(p, "VOLUME_SURFACES", c).split(";") if name.strip()]
        if requested:
            names = [_resolve(catalog.surfaces, name, "volume surface") for name in requested]
        else:
            names = [name for name in catalog.surfaces if any(hint in name.lower() for hint in VOLUME_SURFACE_HINTS)]
            if not names:
                raise QgsProcessingException(
                    "No surface is named as a volume surface; list the volume surfaces separated by semicolons. "
                    f"Available surfaces: {', '.join(catalog.surfaces) or 'none'}."
                )
            fb.pushInfo("Volume surfaces chosen by name: " + "; ".join(names) + ".")
        if len(set(names)) != len(names):
            raise QgsProcessingException("Each volume surface may be listed only once.")
        sites = []
        warnings = []
        for name in names:
            if fb.isCanceled():
                raise QgsProcessingException("Earthwork balance cancelled.")
            try:
                xyz, faces, _meta = read_tin(path, name, cancel=fb.isCanceled)
                summary = volume_summary(transform_vertices(xyz, method=method, **params), faces, ())
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
