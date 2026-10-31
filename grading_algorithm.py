"""Grading Quantities (Volume Surface) Processing tool.

Exact cut/fill volumes, plan areas and depth bands from a volume (depth) TIN
surface such as a Civil 3D TIN volume surface, with an optional check
against the Civil 3D cut/fill summary.
"""

from __future__ import annotations

import csv
import os

from qgis.core import (
    Qgis,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

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
from .landxml.grading import (
    depth_warning,
    grading_report_html,
    parse_bands,
    reconcile,
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
