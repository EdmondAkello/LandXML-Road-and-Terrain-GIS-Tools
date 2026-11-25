"""Cross-section standards as data (pure Python, no QGIS).

Each standard is a JSON file in ``criteria/sections`` (or any file the user picks) with ``id``, ``name``,
``source``, ``units`` and ``references`` like the design review criteria, plus:

* ``limits``: advisory ranges keyed ``"<element>.<parameter>"``, optionally ``@<kind>`` (``"kerb.height@barrier"``),
  each ``{"min", "max", "ref", "note"}``. A value outside a range is reported as a departure; it is never blocked
  or changed, because real designs often depart from the ideal.
* ``slope_bands``: batter slopes by height of slope, per material (``[[max height or null, horizontal per 1 vertical]]``).
* ``side_drains``: named drain shapes; ``widening``: curve widening by radius; ``superelevation``: normal crown,
  rotation axis and runoff placement; ``relative_gradient`` (optional) by design speed.
* ``buildups``: named pavement build-ups (ordered courses); ``templates``: road classes as element lists.

Values are in the file's ``units`` (metric metres or US customary feet); :func:`length_factor` converts explicitly.
"""

from __future__ import annotations

import copy
import json
import os
from bisect import bisect_right

SECTIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "criteria", "sections")
FOOT = 0.3048
UNIT_METRES = {"meter": 1.0, "metre": 1.0, "metric": 1.0, "foot": FOOT, "internationalfoot": FOOT,
               "us_customary": FOOT, "ussurveyfoot": 1200 / 3937}


def list_section_standards(extra_dir=None):
    """[(id, name, path)] for the bundled standards (Generic last) and any in ``extra_dir``."""
    out = []
    for folder in [SECTIONS_DIR] + ([extra_dir] if extra_dir else []):
        if not folder or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as stream:
                    data = json.load(stream)
            except (OSError, ValueError):
                continue
            out.append((data.get("id", name[:-5]), data.get("name", name[:-5]), path))
    return sorted(out, key=lambda item: (item[0] == "generic_sections", item[0]))


def load_section_standard(identifier_or_path, extra_dir=None):
    path = identifier_or_path
    if not os.path.isfile(str(path)):
        match = [item for item in list_section_standards(extra_dir) if item[0] == identifier_or_path]
        if not match:
            raise ValueError(f"Unknown section standard '{identifier_or_path}'.")
        path = match[0][2]
    with open(path, encoding="utf-8") as stream:
        return SectionStandard(json.load(stream), path)


def length_factor(from_units, to_units):
    """Multiply a length in ``from_units`` by this to get ``to_units`` (explicit; never guessed)."""
    a = UNIT_METRES.get(str(from_units or "").lower().replace(" ", ""))
    b = UNIT_METRES.get(str(to_units or "").lower().replace(" ", ""))
    if a is None or b is None:
        raise ValueError(f"Unknown length unit: {from_units if a is None else to_units}.")
    return a / b


class SectionStandard:
    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.id = data.get("id", "custom_sections")
        self.name = data.get("name", "Custom standard")
        self.source = data.get("source", "")
        self.units = (data.get("units") or "metric").lower()
        if self.units not in ("metric", "us_customary"):
            raise ValueError("Section standard units must be metric or us_customary.")
        self.limits = data.get("limits", {})
        self.references = data.get("references", {})

    # ---- lookups -------------------------------------------------------------------------------------------
    def limit(self, element_type, parameter, kind=None):
        """The advisory range for a parameter: the kind-specific one first (``lane.crossfall@gravel``)."""
        if kind:
            found = self.limits.get(f"{element_type}.{parameter}@{kind}")
            if found:
                return found
        return self.limits.get(f"{element_type}.{parameter}")

    def buildups(self):
        return copy.deepcopy(self.data.get("buildups", {}))

    def templates(self):
        return copy.deepcopy(self.data.get("templates", {}))

    def side_drain(self, name):
        found = self.data.get("side_drains", {}).get(name)
        return dict(found) if isinstance(found, dict) else None

    def side_drain_names(self):
        return [k for k, v in self.data.get("side_drains", {}).items() if isinstance(v, dict)]

    def superelevation(self):
        defaults = {"normal_crown": 2.5, "rotation": "centreline", "runoff_on_tangent": 0.5, "shoulder_rule": "own",
                    "breakover_max": 8.0}
        defaults.update(self.data.get("superelevation", {}))
        return defaults

    def slope_bands(self, material="earth"):
        bands = self.data.get("slope_bands", {})
        found = bands.get(material) or bands.get("earth") or {"fill": [[None, 2.0]], "cut": [[None, 1.5]]}
        return copy.deepcopy(found)

    def materials(self):
        return [k for k, v in self.data.get("slope_bands", {}).items() if isinstance(v, dict)]

    def relative_gradient(self, speed):
        table = (self.data.get("relative_gradient") or {}).get("values")
        if not table:
            return None
        return _interpolate({float(k): float(v) for k, v in table.items()}, speed)

    def widening(self, radius, speed=None, lanes=2):
        """(widening, reference) for a curve of ``radius`` (file units), 0 when none applies."""
        spec = self.data.get("widening") or {}
        if not radius or radius <= 0:
            return 0.0, ""
        rows = None
        if spec.get("by_speed") and speed:
            speeds = sorted(float(s) for s in spec["by_speed"])
            if speeds:
                column = next((s for s in speeds if s >= speed - 1e-9), speeds[-1])
                rows = spec["by_speed"][f"{column:g}"]
        elif lanes <= 1 and spec.get("single_lane"):
            rows = spec["single_lane"]
        elif spec.get("two_lanes"):
            rows = spec["two_lanes"]
        if not rows:
            return 0.0, ""
        rows = sorted((float(r), float(w)) for r, w in rows)
        if spec.get("mode") == "interpolate":
            if radius >= rows[-1][0]:
                value = rows[-1][1]
            elif radius <= rows[0][0]:
                value = rows[0][1]
            else:
                value = _interpolate(dict(rows), radius)
        else:                                            # bands: [lowest radius of the band, widening]
            keys = [r for r, _w in rows]
            i = bisect_right(keys, radius) - 1
            value = rows[max(i, 0)][1]
        minimum = float(spec.get("minimum", 0.0) or 0.0)
        if value < minimum - 1e-9:
            value = 0.0
        return value, spec.get("ref", "")

    def fill_widening(self, height):
        rows = (self.data.get("widening") or {}).get("fill")
        return _band(rows, height) if rows else 0.0

    def cutoff_offset(self, height):
        rows = (self.data.get("cutoff_offset") or {}).get("rows")
        return _band(rows, height) if rows else None

    # ---- checks --------------------------------------------------------------------------------------------
    def check(self, element_type, parameter, value, kind=None, label=None):
        """[] or one advisory message for ``value`` against the standard's range (never raises)."""
        found = self.limit(element_type, parameter, kind)
        if not found or value is None:
            return []
        name = label or f"{element_type.replace('_', ' ')} {parameter}"
        low, high = found.get("min"), found.get("max")
        problem = None
        if low is not None and value < low - 1e-9:
            problem = f"below {low:g}"
        elif high is not None and value > high + 1e-9:
            problem = f"above {high:g}"
        if problem is None:
            return []
        note = f" {found['note']}" if found.get("note") else ""
        return [{"level": "departure", "element": element_type, "parameter": parameter,
                 "message": f"{name} {value:g} is {problem} ({self.name}, {found.get('ref', '')}).{note}",
                 "ref": found.get("ref", "")}]

    def to_units(self, value, file_units):
        """A standard length in the file's units (explicit conversion)."""
        return value * length_factor(self.units, file_units)

    def as_dict(self):
        return copy.deepcopy(self.data)


def _interpolate(table, x):
    keys = sorted(table)
    if x <= keys[0]:
        return table[keys[0]]
    if x >= keys[-1]:
        return table[keys[-1]]
    i = bisect_right(keys, x)
    a, b = keys[i - 1], keys[i]
    return table[a] + (table[b] - table[a]) * (x - a) / (b - a)


def _band(rows, value):
    """Rows [[upper bound or None, result], ...]: the first row whose bound is at least ``value``."""
    for bound, result in rows:
        if bound is None or value <= float(bound) + 1e-9:
            return float(result)
    return float(rows[-1][1])


def save_user_standard(data, path):
    """Write a user standard (``Save as standard…``); it must keep the schema's identity fields."""
    for key in ("id", "name", "units"):
        if not data.get(key):
            raise ValueError(f"A standard needs '{key}'.")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=1)
    return path


__all__ = ["SECTIONS_DIR", "SectionStandard", "list_section_standards", "load_section_standard", "length_factor",
           "save_user_standard"]
