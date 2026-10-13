"""Geometric design criteria loaded from JSON files.

Each standard is a JSON file in the plugin's ``criteria`` folder (or any file
the user selects). Tables keyed by design speed are interpolated linearly
between tabulated speeds and clamped at the ends; every check reports the
table it used so values can be traced back to the source document.
"""

from __future__ import annotations

from bisect import bisect_left
import json
import math
import os

CRITERIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "criteria")


def list_standards():
    """Return [(id, name, path)] for the bundled criteria files, Kenya first."""
    result = []
    if os.path.isdir(CRITERIA_DIR):
        for name in sorted(os.listdir(CRITERIA_DIR), reverse=True):
            if name.endswith(".json"):
                path = os.path.join(CRITERIA_DIR, name)
                try:
                    with open(path, encoding="utf-8") as stream:
                        data = json.load(stream)
                except (OSError, ValueError):
                    continue
                result.append((data.get("id", name[:-5]), data.get("name", name[:-5]), path))
    return result


def _table(mapping):
    pairs = sorted((float(k), v) for k, v in mapping.items() if v is not None)
    return [p[0] for p in pairs], [p[1] for p in pairs]


def interpolate(mapping, x):
    if not mapping:
        return None
    keys, values = _table(mapping)
    if x <= keys[0]:
        return values[0]
    if x >= keys[-1]:
        return values[-1]
    i = bisect_left(keys, x)
    if keys[i] == x:
        return values[i]
    t = (x - keys[i - 1]) / (keys[i] - keys[i - 1])
    return values[i - 1] + t * (values[i] - values[i - 1])


class Criteria:
    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.id = data.get("id", "custom")
        self.name = data.get("name", "Custom criteria")
        self.source = data.get("source", "")
        if (data.get("units") or "metric").lower() != "metric":
            raise ValueError("Only metric design criteria are supported.")

    def ref(self, key, fallback=""):
        return self.data.get("references", {}).get(key, fallback)

    def get(self, key, default=None):
        value = self.data.get(key, default)
        return default if value is None else value

    # ---- sight distance and vertical ------------------------------------
    @property
    def eye_height(self):
        return float(self.get("eye_height", 1.08))

    @property
    def object_height(self):
        return float(self.get("object_height", 0.6))

    def ssd(self, speed, grade_percent=0.0):
        """Stopping sight distance; gradient-dependent where the table gives it."""
        by_grade = self.data.get("ssd_by_grade")
        if by_grade:
            grades = by_grade["grades"]

            def at_speed(row):
                pairs = [(g, v) for g, v in zip(grades, row) if v is not None]
                if not pairs:
                    return None
                pairs.sort()
                gs, vs = [p[0] for p in pairs], [p[1] for p in pairs]
                g = min(max(grade_percent, gs[0]), gs[-1])
                return interpolate(dict(zip(map(str, gs), vs)), g)

            rows = {k: at_speed(v) for k, v in by_grade.items() if k != "grades"}
            rows = {k: v for k, v in rows.items() if v is not None}
            if rows:
                value = interpolate(rows, speed)
                if speed > max(float(k) for k in rows):
                    value = max(value, interpolate(self.data.get("ssd"), speed) or 0.0)
                return value
        return interpolate(self.data.get("ssd"), speed)

    def k_crest(self, speed):
        return interpolate(self.data.get("k_crest"), speed)

    def k_sag(self, speed):
        return interpolate(self.data.get("k_sag"), speed)

    def min_vc_length(self, speed):
        table = self.data.get("min_vc_length")
        if table:
            return interpolate(table, speed)
        return float(self.get("min_vc_length_factor", 0.0)) * speed

    def max_grade(self, road_type, terrain):
        table = self.data.get("max_grade")
        if not table:
            return None
        values = table.get(road_type, {}).get(terrain)
        return tuple(values) if values else None

    def max_grade_length(self, grade_percent):
        table = self.data.get("max_grade_length")
        if not table or grade_percent < min(float(k) for k in table):
            return None
        return interpolate(table, grade_percent)

    # ---- horizontal ------------------------------------------------------
    def side_friction(self, speed):
        return interpolate(self.data.get("side_friction"), speed)

    def min_radius(self, speed, emax):
        tables = self.data.get("min_radius")
        if tables and str(int(emax)) in tables:
            return interpolate(tables[str(int(emax))], speed)
        f = self.side_friction(speed)
        if f is None:
            return None
        return speed * speed / (127.0 * (emax / 100.0 + f))

    def required_superelevation(self, radius, speed, emax):
        """Return (rate %, label) where label is NC, RC, a rate, or None if the radius is too small."""
        tables = self.data.get("superelevation_rates")
        key = str(int(emax))
        if tables and key in tables:
            rows = sorted(((float(r), v) for r, v in tables[key].items()), reverse=True)
            speeds = sorted({float(s) for _r, row in rows for s in row})
            column = min(speeds, key=lambda s: (abs(s - speed), -s))
            if speed > column:
                higher = [s for s in speeds if s >= speed]
                column = higher[0] if higher else column
            label = None
            for row_radius, row in rows:
                value = row.get(str(int(column)))
                if row_radius <= radius + 1e-6:
                    label = value if value is not None else label
                    break
                label = value
            if label is None:
                return None, None
            if label == "NC":
                return -abs(float(self.get("normal_crown_percent", 2.5))), "NC"
            if label == "RC":
                return abs(float(self.get("normal_crown_percent", 2.5))), "RC"
            return float(label), f"{float(label):g}%"
        f = self.side_friction(speed)
        if f is None:
            return None, None
        rate = max(speed * speed / (127.0 * radius) - f, 0.0) * 100.0
        return rate, f"{rate:.1f}%"

    def runoff_min(self, speed, rate_percent, lane_width=None):
        lane_width = lane_width or float(self.get("lane_width", 3.5))
        values = []
        table = self.data.get("runoff_min")
        if table:
            values.append(interpolate(table, speed))
        seconds = self.data.get("runoff_min_seconds")
        if seconds:
            values.append(speed / 3.6 * seconds)
        delta = interpolate(self.data.get("max_relative_gradient_percent"), speed)
        if delta:
            values.append(lane_width * abs(rate_percent) / delta)
        return max(values) if values else None

    def transition_required_radius(self, speed):
        table = self.data.get("transition_required_radius")
        if not table or speed < min(float(k) for k in table):
            return None
        return interpolate(table, speed)

    def spiral_min_parameter(self, speed, radius):
        coefficient = self.data.get("spiral_a_coefficient")
        if coefficient:
            return coefficient * speed**1.5
        p_min = self.data.get("spiral_min_offset")
        jerk = self.data.get("spiral_jerk")
        if p_min and jerk:
            length = max(math.sqrt(24 * p_min * radius), 0.0214 * speed**3 / (radius * jerk))
            return math.sqrt(radius * length)
        return None


def load_criteria(identifier_or_path):
    path = identifier_or_path
    if not os.path.isfile(str(path)):
        match = [item for item in list_standards() if item[0] == identifier_or_path]
        if not match:
            raise ValueError(f"Unknown design standard '{identifier_or_path}'.")
        path = match[0][2]
    with open(path, encoding="utf-8") as stream:
        return Criteria(json.load(stream), path)
