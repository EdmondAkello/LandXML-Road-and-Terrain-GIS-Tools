"""Earthwork reporting units; geometry and calculation results stay in source units."""

from dataclasses import dataclass


LINEAR_UNITS = {
    "meter": (1.0, "m"), "metre": (1.0, "m"),
    "millimeter": (0.001, "mm"), "centimeter": (0.01, "cm"), "kilometer": (1000.0, "km"),
    "foot": (0.3048, "ft"), "internationalfoot": (0.3048, "ft"),
    "ussurveyfoot": (1200.0 / 3937.0, "ftUS"),
}

VOLUME_FACTORS = {"CY": 0.9144 ** 3, "CF": 0.3048 ** 3, "m³": 1.0}
VOLUME_NAMES = {"CY": "cubic yards", "CF": "cubic feet", "m³": "cubic metres"}


@dataclass(frozen=True)
class QuantityUnits:
    length: str
    depth: str
    area: str
    volume: str
    volume_factor: float

    @property
    def volume_name(self):
        return VOLUME_NAMES[self.volume]

    def volumes(self, values):
        return {
            key: value * self.volume_factor if key.endswith("_volume") or key == "mass_ordinate" else value
            for key, value in values.items()
        }


def quantity_units(horizontal, vertical, allow_mixed=False, volume_unit="auto"):
    try:
        h, length = LINEAR_UNITS[(horizontal or "").lower()]
        v, depth = LINEAR_UNITS[(vertical or "").lower()]
    except KeyError as exc:
        raise ValueError(
            "Earthwork volumes require known horizontal and vertical linear units. "
            "Use Prepare LandXML to supply missing units explicitly."
        ) from exc
    if not allow_mixed and h != v:
        raise ValueError("Earthwork section/profile quantities require matching horizontal and vertical units; no unit conversion is implicit.")
    imperial = length in ("ft", "ftUS")
    if volume_unit == "auto":
        volume_unit = "CY" if imperial else "m³"
    if volume_unit not in VOLUME_FACTORS:
        raise ValueError(f"Unsupported earthwork volume unit '{volume_unit}'.")
    return QuantityUnits(length, depth, f"{length}²", volume_unit, h * h * v / VOLUME_FACTORS[volume_unit])


def recorded_volume_factor(source_unit, target_unit):
    factors = {"cubicmeter": 1.0, "cubicfoot": 0.3048 ** 3,
               "cubicfeet": 0.3048 ** 3, "cubicyard": 0.9144 ** 3}
    try:
        return factors[(source_unit or "").lower()] / VOLUME_FACTORS[target_unit]
    except KeyError as exc:
        raise ValueError("Recorded volume unit is missing or unsupported; comparison omitted.") from exc
