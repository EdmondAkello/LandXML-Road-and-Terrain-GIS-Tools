"""Station/offset positioning along a sampled horizontal alignment.

Vertices carry true distances along the source geometry (arc length on
curves and spirals), so station lookups do not accumulate chord error. Offsets
follow the Civil 3D/LandXML convention: negative left, positive right of the
direction of increasing station. All values are in source coordinates.
"""

from __future__ import annotations

from bisect import bisect_left
import math


class StationedPolyline:
    def __init__(self, points, distances, station_start):
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
        return cls(alignment["points"], distances, float(alignment["sta_start"]))

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
