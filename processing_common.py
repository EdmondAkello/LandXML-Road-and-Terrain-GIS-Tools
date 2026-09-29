"""Processing checks shared by terrain and road outputs."""

from __future__ import annotations

from qgis.core import Qgis, QgsProcessingException

from .landxml.parser import load_document


class BoundsTracker:
    """Accumulate actual transformed X/Y bounds without guessing from corners."""

    def __init__(self):
        self.minimum_x = self.minimum_y = float("inf")
        self.maximum_x = self.maximum_y = float("-inf")

    def add(self, coordinates):
        if len(coordinates):
            self.minimum_x = min(self.minimum_x, float(coordinates[:, 0].min()))
            self.minimum_y = min(self.minimum_y, float(coordinates[:, 1].min()))
            self.maximum_x = max(self.maximum_x, float(coordinates[:, 0].max()))
            self.maximum_y = max(self.maximum_y, float(coordinates[:, 1].max()))

    def report(self, feedback):
        if self.minimum_x != float("inf"):
            feedback.pushInfo(
                f"Transformed output bounds: X {self.minimum_x:.3f}–{self.maximum_x:.3f}; "
                f"Y {self.minimum_y:.3f}–{self.maximum_y:.3f}"
            )


def coordinate_choices(algorithm, parameters, context, feedback, path):
    """Read explicitly supplied CRS/transform settings; never infer source CRS."""
    document = load_document(path)
    report = document.inspect()
    feedback.pushInfo(
        f"LandXML: vendor={report['vendor']}; version={report['version'] or 'undeclared'}; "
        f"horizontal unit={report['horizontal_unit'] or 'undeclared'}; "
        f"vertical unit={report['vertical_unit'] or 'undeclared'}; "
        f"declared CRS={report['declared_crs'] or 'undeclared'} (reference only)"
    )
    for warning in report["warnings"]:
        feedback.pushWarning(warning)
    if report["bounds"] is not None:
        x0, y0, x1, y1 = report["bounds"]
        feedback.pushInfo(f"Source bounds: X {x0:.3f}–{x1:.3f}; Y {y0:.3f}–{y1:.3f}")
    if report["elevation_range"] is not None:
        low, high = report["elevation_range"]
        feedback.pushInfo(
            f"Source elevation range: {low:.3f}–{high:.3f} (source vertical units)"
        )
    if parameters.get("OUTPUT_CRS") in (None, ""):
        raise QgsProcessingException(
            "Choose an output CRS explicitly. LandXML coordinates will not be assigned an inferred CRS."
        )
    out = algorithm.parameterAsCrs(parameters, "OUTPUT_CRS", context)
    if not out.isValid():
        raise QgsProcessingException("The selected output CRS is invalid.")
    method_index = algorithm.parameterAsInt(parameters, "METHOD", context)
    methods = ["stored", "swap", "offset", "swap_offset", "helmert", "reproject"]
    if method_index not in range(len(methods)):
        raise QgsProcessingException(
            f"Unknown coordinate interpretation option {method_index}."
        )
    method = methods[method_index]
    src = algorithm.parameterAsCrs(parameters, "SOURCE_CRS", context)
    if method == "reproject" and (
        parameters.get("SOURCE_CRS") in (None, "") or not src.isValid()
    ):
        raise QgsProcessingException(
            "Reprojection requires an explicitly selected source CRS."
        )
    unit_lookup = {
        "ussurveyfoot": Qgis.DistanceUnit.FeetUSSurvey,
        "foot": Qgis.DistanceUnit.Feet,
        "internationalfoot": Qgis.DistanceUnit.Feet,
        "meter": Qgis.DistanceUnit.Meters,
        "metre": Qgis.DistanceUnit.Meters,
    }
    declared = unit_lookup.get((document.horizontal_unit or "").lower())
    if declared is not None:
        interpreted = src if method == "reproject" else out
        if interpreted.isValid() and interpreted.mapUnits() != declared:
            role = "source" if method == "reproject" else "output"
            raise QgsProcessingException(
                f"LandXML declares horizontal unit '{document.horizontal_unit}', but the selected {role} CRS "
                f"uses {interpreted.mapUnits()}. No implicit ftUS/foot/meter conversion is permitted; "
                f"select a matching {role} CRS."
            )
    elif document.horizontal_unit:
        feedback.pushWarning(
            f"Unrecognized LandXML horizontal unit '{document.horizontal_unit}'; verify CRS units manually."
        )
    params = dict(
        dx=algorithm.parameterAsDouble(parameters, "DX", context),
        dy=algorithm.parameterAsDouble(parameters, "DY", context),
        rotation_deg=algorithm.parameterAsDouble(parameters, "ROTATION", context),
        scale=algorithm.parameterAsDouble(parameters, "SCALE", context),
        src_wkt=src.toWkt() if src.isValid() else None,
        dst_wkt=out.toWkt(),
    )
    if method == "stored" and report["horizontal_unit"] is None:
        feedback.pushWarning(
            "Source horizontal unit is unknown. Verify that stored coordinates match the selected output CRS units."
        )
    if method != "stored":
        feedback.pushInfo(f"Explicit coordinate operation: {method}")
    return method, out, src, params, document
