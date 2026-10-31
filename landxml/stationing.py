"""Station/offset positioning along a sampled horizontal alignment.

Vertices carry true distances along the source geometry (arc length on
curves and spirals), so station lookups do not accumulate chord error. Offsets
follow the Civil 3D/LandXML convention: negative left, positive right of the
direction of increasing station. All values are in source coordinates.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
import re

from .quantity_units import LINEAR_UNITS
from .common import local_name


class StationMap:
    """Increasing roadway station regions; geometry always uses internal station."""

    def __init__(self, start, end, equations=(), name="Alignment"):
        self.start, self.end = float(start), float(end)
        if not all(math.isfinite(v) for v in (self.start, self.end)) or self.end < self.start:
            raise ValueError(f"{name}: invalid internal station range.")
        self.equations = []
        previous, shift = None, 0.0
        for index, attributes in enumerate(equations, 1):
            label = f"{name}: station equation {index}"
            try:
                internal = float(attributes["staInternal"])
                ahead = float(attributes["staAhead"])
                back = float(attributes.get("staBack", internal + shift))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{label}: invalid staInternal, staBack or staAhead.") from exc
            if not all(math.isfinite(v) for v in (internal, ahead, back)):
                raise ValueError(f"{label}: stations must be finite.")
            if not self.start <= internal <= self.end or (previous is not None and internal <= previous):
                raise ValueError(f"{label}: positions must be within the alignment and strictly increasing.")
            if attributes.get("staIncrement", "increasing") != "increasing":
                raise ValueError(f"{label}: only increasing staIncrement is supported; decreasing or unknown progression is unsupported.")
            if not math.isclose(back, internal + shift, rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"{label}: staBack disagrees with the preceding station region.")
            self.equations.append(dict(attributes, staInternal=internal, staBack=back, staAhead=ahead))
            previous, shift = internal, ahead - internal
        self.breaks = [e["staInternal"] for e in self.equations]

    @classmethod
    def from_element(cls, element, end):
        equations = [dict(node.attrib) for node in element if local_name(node.tag) == "StaEquation"]
        if not equations:
            return None
        try:
            start = float(element.attrib.get("staStart", element.attrib.get("startStation", "")))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{element.attrib.get('name', 'Alignment')}: station equations require a finite declared start station.") from exc
        return cls(start, end, equations, element.attrib.get("name", "Alignment"))

    def region(self, internal):
        return bisect_right(self.breaks, internal)

    def station(self, internal, region=None):
        """Ahead label owns a boundary unless an incoming region is explicit."""
        if not math.isfinite(internal) or not self.start <= internal <= self.end:
            raise ValueError("Internal station is outside the alignment.")
        region = self.region(internal) if region is None else region
        if not 0 <= region <= len(self.equations):
            raise ValueError("Unknown station region.")
        low = self.start if region == 0 else self.breaks[region - 1]
        high = self.end if region == len(self.equations) else self.breaks[region]
        if not low <= internal <= high:
            raise ValueError("Internal station is outside the selected region.")
        shift = 0 if region == 0 else self.equations[region - 1]["staAhead"] - low
        return internal + shift

    def candidates(self, station):
        """Return (internal station, region) candidates, including both boundary labels."""
        if not math.isfinite(station):
            raise ValueError("Station must be finite.")
        result = []
        bounds = [self.start, *self.breaks, self.end]
        for region, (low, high) in enumerate(zip(bounds, bounds[1:])):
            shift = 0 if region == 0 else self.equations[region - 1]["staAhead"] - low
            internal = station - shift
            if low <= internal <= high:
                result.append((internal, region))
        return result

    def resolve(self, station, region=None):
        candidates = self.candidates(station)
        if region is not None:
            candidates = [c for c in candidates if c[1] == region]
        positions = {c[0] for c in candidates}
        if not positions:
            raise ValueError("Station does not exist in the alignment or selected region.")
        if len(positions) > 1:
            raise ValueError("Station is ambiguous; choose a station region.")
        return candidates[-1]


def parse_station(text, horizontal_unit=None):
    """Parse numeric or signed engineering stationing in declared source units."""
    match = re.fullmatch(r"([+-]?)([0-9]+)\s*\+\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)", text.strip())
    if match:
        unit = (horizontal_unit or "").lower()
        if unit not in LINEAR_UNITS and unit != "kilometre":
            raise ValueError("Engineering station input requires declared horizontal units; enter a numeric station instead.")
        base = 100 if unit in {"foot", "internationalfoot", "ussurveyfoot"} else 1000
        minor = float(match[3])
        if minor >= base:
            raise ValueError(f"Station value after '+' must be less than {base}.")
        station = float(match[2]) * base + minor
        if match[1] == "-":
            station = -station
    else:
        station = float(text)
    if not math.isfinite(station):
        raise ValueError("Station must be a finite number.")
    return station


class StationedPolyline:
    def __init__(self, points, distances, station_start, station_map=None):
        self.station_map = station_map
        if len(points) != len(distances):
            raise ValueError("Alignment vertices and distances do not match")
        self.points = []
        self.stations = []
        start = float(station_start)
        for point, distance in zip(points, distances):
            station = start + float(distance)
            if self.stations and station - self.stations[-1] <= 1e-9:
                continue
            self.points.append((float(point[0]), float(point[1])))
            self.stations.append(station)
        if len(self.points) < 2:
            raise ValueError("Alignment needs at least two distinct vertices")

    @classmethod
    def from_alignment(cls, alignment):
        """Build from a read_alignments() record; None without a start station."""
        if alignment.get("sta_start") in (None, ""):
            return None
        distances = alignment.get("vertex_distances")
        if not distances:
            distances = [0.0]
            for a, b in zip(alignment["points"], alignment["points"][1:]):
                distances.append(distances[-1] + math.dist(a, b))
        return cls(alignment["points"], distances, float(alignment["sta_start"]), alignment.get("station_map"))

    @property
    def station_start(self):
        return self.stations[0]

    @property
    def station_end(self):
        return self.stations[-1]

    def _segment(self, station):
        index = bisect_left(self.stations, station)
        return min(max(index, 1), len(self.stations) - 1)

    def frame(self, station):
        """Return (x, y, unit_dx, unit_dy) at a station, or None outside the range."""
        tolerance = 1e-6
        if station < self.station_start - tolerance or station > self.station_end + tolerance:
            return None
        station = min(max(station, self.station_start), self.station_end)
        i = self._segment(station)
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        s0, s1 = self.stations[i - 1], self.stations[i]
        fraction = (station - s0) / (s1 - s0)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            raise ValueError("Alignment segment has no direction")
        return x0 + fraction * dx, y0 + fraction * dy, dx / length, dy / length

    def point_at(self, station, offset=0.0):
        """Return source XY at station/offset (right positive), or None outside."""
        frame = self.frame(station)
        if frame is None:
            return None
        x, y, ux, uy = frame
        return x + uy * offset, y - ux * offset

    def locate(self, x, y):
        """Return (station, offset, distance) of the nearest alignment position."""
        best = None
        for i in range(1, len(self.points)):
            (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
            dx, dy = x1 - x0, y1 - y0
            length2 = dx * dx + dy * dy
            t = 0.0 if length2 <= 0 else ((x - x0) * dx + (y - y0) * dy) / length2
            t = min(max(t, 0.0), 1.0)
            px, py = x0 + t * dx, y0 + t * dy
            distance = math.hypot(x - px, y - py)
            if best is None or distance < best[2]:
                length = math.sqrt(length2) or 1.0
                # Positive cross product = point left of travel direction.
                cross = (dx * (y - y0) - dy * (x - x0)) / length
                station = self.stations[i - 1] + t * (self.stations[i] - self.stations[i - 1])
                best = (station, -cross, distance)
        return best
