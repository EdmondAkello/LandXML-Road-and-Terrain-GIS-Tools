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
    # Append the new standard so saved Processing enum selections keep their indices.
    return sorted(result, key=lambda item: item[0] == "aashto_2018_us_customary")


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
        self.units = (data.get("units") or "metric").lower()
        if self.units not in ("metric", "us_customary"):
            raise ValueError("Criteria units must be metric or us_customary.")
        self.length_unit = "ft" if self.units == "us_customary" else "m"
        self.speed_unit = "mph" if self.units == "us_customary" else "km/h"
        self.metres_per_unit = 0.3048 if self.units == "us_customary" else 1.0
        self.length_scale = 1.0

    def set_linear_unit(self, unit, vertical_unit=None):
        """Match criteria lengths to the file, distinguishing survey and international feet."""
        unit = (unit or "meter").lower()
        factors = {"meter": 1.0, "metre": 1.0, "foot": 0.3048,
                   "internationalfoot": 0.3048, "ussurveyfoot": 1200 / 3937}
        if unit not in factors or (self.units == "metric") != (factors[unit] == 1.0):
            raise ValueError(f"{self.name} requires {'metres' if self.units == 'metric' else 'feet'}; the file declares '{unit}'.")
        if vertical_unit and factors.get(vertical_unit.lower()) != factors[unit]:
            raise ValueError("Design review requires matching horizontal and vertical length units.")
        self.metres_per_unit = factors[unit]
        self.length_scale = (0.3048 if self.units == "us_customary" else 1.0) / self.metres_per_unit

    def metres(self, value):
        return value / self.metres_per_unit

    def ref(self, key, fallback=""):
        return self.data.get("references", {}).get(key, fallback)

    def get(self, key, default=None):
        value = self.data.get(key, default)
        if value is None:
            return default
        if key in ("eye_height", "object_height", "headlight_height", "lane_width", "sightline_height", "spiral_min_offset") and key in self.data:
            return value * self.length_scale
        if key == "barrier" and value:
            return {k: (v * self.length_scale if k in ("warrant_height", "consider_height", "min_length") else v)
                    for k, v in value.items()}
        return value

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
                return value * self.length_scale
        value = interpolate(self.data.get("ssd"), speed)
        return value * self.length_scale if value is not None else None

    def k_crest(self, speed):
        value = interpolate(self.data.get("k_crest"), speed)
        return value * self.length_scale if value is not None else None

    def k_sag(self, speed):
        value = interpolate(self.data.get("k_sag"), speed)
        return value * self.length_scale if value is not None else None

    def min_vc_length(self, speed):
        table = self.data.get("min_vc_length")
        if table:
            return interpolate(table, speed) * self.length_scale
        return float(self.get("min_vc_length_factor", 0.0)) * speed * self.length_scale

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
            return interpolate(tables[str(int(emax))], speed) * self.length_scale
        f = self.side_friction(speed)
        if f is None:
            return None
        denominator = 15.0 / self.length_scale if self.units == "us_customary" else 127.0
        return speed * speed / (denominator * (emax / 100.0 + f))

    def required_superelevation(self, radius, speed, emax):
        """Return (rate %, label) where label is NC, RC, a rate, or None if the radius is too small."""
        tables = self.data.get("superelevation_rates")
        key = str(int(emax))
        by_speed = self.data.get("superelevation_by_speed", {}).get(key)
        if by_speed:
            speeds = sorted(float(s) for s in by_speed)
            column = next((s for s in speeds if s >= speed), speeds[-1])
            rows = sorted(((float(r) * self.length_scale, v) for r, v in by_speed[f"{column:g}"].items()), reverse=True)
            if radius < rows[-1][0] - 1e-6:
                return None, None
            label = next((v for r, v in rows if radius >= r - 1e-6), rows[-1][1])
            if label == "NC":
                return -abs(float(self.get("normal_crown_percent", 2.0))), "NC"
            if label == "RC":
                return abs(float(self.get("normal_crown_percent", 2.0))), "RC"
            return float(label), f"{float(label):g}%"
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
        denominator = 15.0 / self.length_scale if self.units == "us_customary" else 127.0
        rate = max(speed * speed / (denominator * radius) - f, 0.0) * 100.0
        return rate, f"{rate:.1f}%"

    def runoff_min(self, speed, rate_percent, lane_width=None):
        lane_width = lane_width or float(self.get("lane_width", 3.5))
        by_rate = self.data.get("runoff_by_rate")
        if by_rate:
            lengths = {rate: interpolate(row, speed) for rate, row in by_rate.items()}
            return interpolate(lengths, abs(rate_percent)) * self.length_scale * max(1.0, lane_width / (12 * self.length_scale))
        values = []
        table = self.data.get("runoff_min")
        if table:
            values.append(interpolate(table, speed))
        seconds = self.data.get("runoff_min_seconds")
        if seconds:
            velocity = speed * 0.44704 if self.units == "us_customary" else speed / 3.6
            values.append(self.metres(velocity * seconds))
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
            speed_kmh = speed * 1.609344 if self.units == "us_customary" else speed
            length = max(math.sqrt(24 * p_min * radius), self.metres(0.0214 * speed_kmh**3 / (radius * self.metres_per_unit * jerk)))
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
