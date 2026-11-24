"""Unit bases for Processing inputs; labels change, parameter values never do."""


from qgis.core import QgsCoordinateReferenceSystem, QgsUnitTypes

from .landxml.catalog import read_units
from .landxml.quantity_units import LINEAR_UNITS


SOURCE_LENGTHS = set(
    "SEGMENT ALIGNMENT_SEGMENT STATION_INTERVAL POINT_INTERVAL PROFILE_INTERVAL SECTION_INTERVAL LEFT_WIDTH RIGHT_WIDTH MAP_OFFSET INTERVAL FORMATION_WIDTH MAX_SPACING EXCLUDE RANGE STRIP SHEET_LENGTH STATION_FROM STATION_TO STATION_LABELS STRETCH HALF_WIDTH".split()
)
DEPTHS = set(
    "HIGH_FILL DEEP_CUT MAX_CHANGE FREEBOARD STAGE_STEP RIM_LEVEL TOPSOIL_DEPTH TIE_TOLERANCE MIN_STEP HEIGHT_BANDS DEPTH_BANDS CONTOURS CONTOUR_INTERVAL RASTER_CONTOURS WATER EXTRUDE_HEIGHT EXTRUDE_FIELD NODATA".split()
)
OUTPUT_LENGTHS = set(
    "DX DY RESOLUTION CORRIDOR_WIDTH SYN_CARRIAGEWAY SYN_SHOULDER SYN_MAX_WIDTH SYN_BRIDGE_SPAN SYN_FIT_WIDTH CONTEXT_MARGIN CAMERA_DISTANCE ROAD_WIDTH".split()
)
VOLUMES = {"CIVIL3D_CUT", "CIVIL3D_FILL", "REQUIRED_VOLUME"}
STYLES = {
    "STYLE",
    "MAP_STYLES",
    "SURFACES",
    "DESIGN_STYLE",
    "CONTEXT_STYLE",
    "RASTER_STYLE",
}


def unit_basis(tool, name):
    if name == "TOLERANCE":
        return "reconciliation" if tool == "landxml_grading_quantities" else "vertical"
    if name in SOURCE_LENGTHS:
        return "horizontal"
    if name in DEPTHS:
        return "vertical"
    if name in OUTPUT_LENGTHS:
        return "output"
    if name in VOLUMES:
        return "volume"
    if name in {"MIN_AREA", "CIVIL3D_AREA"}:
        return "area"
    if name == "DESIGN_SPEED":
        return "speed"
    if name == "SPEED_RANGES":
        return "speed_ranges"
    if name in {"SYN_STRUCTURES", "SYN_STRUCTURES_CSV"}:
        return "structures"
    if name == "LABELS":
        return "labels"
    if name in STYLES:
        return "style"
    return None


BASE_LABELS = {
    "horizontal": "source horizontal units",
    "vertical": "source elevation units",
    "output": "output CRS units",
    "volume": "selected volume units",
    "area": "output CRS units²",
    "reconciliation": "selected volume / output area units",
    "speed": "criteria speed units",
    "speed_ranges": "source stations:criteria speeds",
    "structures": "source stations; output spans; source rises/inverts",
    "labels": "output XY; source elevation lift",
    "style": "elevation/depth: source units; road widths: output units",
}


def short_unit(unit):
    return LINEAR_UNITS.get((unit or "").lower(), (None, "unknown"))[1]


def input_labels(algorithm, values, horizontal=None, vertical=None):
    """Resolved labels, also used by tests and native QGIS dialogs."""
    choices = (None, "meter", "foot", "USSurveyFoot")
    horizontal = horizontal or choices[int(values.get("HORIZONTAL_UNIT") or 0)]
    vertical = vertical or choices[int(values.get("VERTICAL_UNIT") or 0)]
    h, v = short_unit(horizontal), short_unit(vertical)
    crs = values.get("OUTPUT_CRS")
    crs = (
        crs
        if isinstance(crs, QgsCoordinateReferenceSystem)
        else QgsCoordinateReferenceSystem(crs or "")
    )
    out = (
        QgsUnitTypes.toAbbreviatedString(crs.mapUnits()) if crs.isValid() else "unknown"
    )
    # QGIS abbreviates both feet definitions as ft in some versions.
    if crs.isValid():
        from .grading_algorithm import HORIZONTAL_UNITS

        out = (
            short_unit(HORIZONTAL_UNITS.get(crs.mapUnits()))
            if crs.mapUnits() in HORIZONTAL_UNITS
            else out
        )
    unit = (
        out
        if algorithm.name()
        not in {"landxml_profile_cut_fill", "landxml_corridor_quantities"}
        else h
    )
    volume = ("auto", "CY", "CF", "m³")[int(values.get("VOLUME_UNITS") or 0)]
    if volume == "auto":
        volume = (
            "CY" if unit in {"ft", "ftUS"} else "m³" if unit != "unknown" else "unknown"
        )
    speed = "mph" if h in {"ft", "ftUS"} else "km/h" if h != "unknown" else "unknown"
    standard = algorithm.parameterDefinition("STANDARD")
    if standard is not None:
        index = int(values.get("STANDARD") or 0)
        if not (algorithm.name() == "landxml_design_report" and index == 0):
            speed = (
                "mph" if "customary" in standard.options()[index].lower() else "km/h"
            )
    if values.get("CRITERIA_FILE"):
        from .landxml.criteria import load_criteria

        try:
            speed = load_criteria(values["CRITERIA_FILE"]).speed_unit
        except (OSError, ValueError, TypeError):
            speed = "unknown"
    resolved = {
        "horizontal": h,
        "vertical": v,
        "output": out,
        "volume": volume,
        "area": out + "²",
        "reconciliation": f"{volume} / {out}²",
        "speed": speed,
        "speed_ranges": f"stations {h}; speeds {speed}",
        "structures": f"stations {h}; spans {out}; rises/inverts {v}; CSV headings may specify units",
        "labels": f"XY {out}; lift {v}",
        "style": f"elevation/depth {v}; road widths {out}; slope %; aspect °",
    }
    return {
        p.name(): resolved[unit_basis(algorithm.name(), p.name())]
        for p in algorithm.parameterDefinitions()
        if unit_basis(algorithm.name(), p.name())
    }


def connect_unit_labels(dialog, algorithm):
    """Refresh actual units on existing QGIS labels, including advanced inputs."""
    from qgis.PyQt.QtWidgets import QLabel

    panel = dialog.mainWidget()
    labels = {}
    for name, wrapper in panel.wrappers.items():
        basis = unit_basis(algorithm.name(), name)
        if not basis:
            continue
        base = wrapper.parameterDefinition().description()
        # Native wrappers expose wrappedLabel; older Python wrappers expose label.
        label = (
            wrapper.wrappedLabel()
            if hasattr(wrapper, "wrappedLabel")
            else getattr(wrapper, "label", None)
        )
        if label is None:
            label = next(
                (child for child in panel.findChildren(QLabel) if child.text().startswith(base)),
                None,
            )
        if label is not None:
            text = label.text().replace(f" [{BASE_LABELS[basis]}]", "")
            labels[name] = (label, text)

    def refresh(*_args):
        values = {
            n: w.parameterValue()
            for n, w in panel.wrappers.items()
            if n
            in {
                "INPUT",
                "OUTPUT_CRS",
                "HORIZONTAL_UNIT",
                "VERTICAL_UNIT",
                "VOLUME_UNITS",
                "STANDARD",
                "CRITERIA_FILE",
            }
        }
        path = values.get("INPUT")
        h = v = None
        try:
            h, v = read_units(path)  # header only: no full parse in the GUI thread
        except (OSError, ValueError, TypeError, SyntaxError):
            pass  # A missing/partly typed input must leave units visibly unknown.
        for name, unit in input_labels(algorithm, values, h, v).items():
            if name in labels:
                label, text = labels[name]
                label.setText(f"{text} [{unit}]")

    for name in (
        "INPUT",
        "OUTPUT_CRS",
        "HORIZONTAL_UNIT",
        "VERTICAL_UNIT",
        "VOLUME_UNITS",
        "STANDARD",
        "CRITERIA_FILE",
    ):
        if name in panel.wrappers:
            panel.wrappers[name].widgetValueHasChanged.connect(refresh)
    refresh()
