"""Design (ProfAlign) and surface (ProfSurf) longitudinal profiles per alignment.

Civil 3D writes existing-ground and other surface profiles as ``ProfSurf``
station/elevation lists. Where the surface does not cover the alignment the
list restarts at a lower station; each monotonic run is kept as a separate
part and nothing is interpolated across a gap.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
import math

from .common import descendants, local_name
from .profile import read_vertical_profile


@dataclass
class ProfileLine:
    alignment_name: str | None
    name: str
    kind: str  # "design" (ProfAlign) or "surface" (ProfSurf)
    state: str | None = None
    description: str | None = None
    parts: list = field(default_factory=list)
    element: object = None

    @property
    def label(self):
        return f"{self.name} ({'design' if self.kind == 'design' else 'surface'})"

    @property
    def station_range(self):
        stations = [point[0] for part in self.parts for point in part]
        return (min(stations), max(stations)) if stations else None

    @property
    def coverage(self):
        return sum(part[-1][0] - part[0][0] for part in self.parts if len(part) > 1)

    def elevation_at(self, station):
        """Linear elevation at a station, or None outside every covered part."""
        cache = self.__dict__.get("_station_cache")
        if cache is None or len(cache) != len(self.parts):
            cache = [[point[0] for point in part] for part in self.parts]
            self.__dict__["_station_cache"] = cache
        for part, stations in zip(self.parts, cache):
            if len(part) < 2 or station < part[0][0] - 1e-9 or station > part[-1][0] + 1e-9:
                continue
            index = min(max(bisect_right(stations, station), 1), len(part) - 1)
            (s0, z0), (s1, z1) = part[index - 1], part[index]
            if s1 - s0 <= 1e-12:
                return z1
            return z0 + (z1 - z0) * (station - s0) / (s1 - s0)
        return None

    def breakpoints(self):
        return [point[0] for part in self.parts for point in part]


def _surface_parts(text, label):
    tokens = (text or "").split()
    if len(tokens) % 2:
        raise ValueError(f"{label} has an odd number of station/elevation values")
    try:
        values = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"{label} contains a nonnumeric value") from exc
    parts, current = [], []
    for index in range(0, len(values), 2):
        station, elevation = values[index], values[index + 1]
        if not (math.isfinite(station) and math.isfinite(elevation)):
            continue
        if current and station < current[-1][0] - 1e-9:
            parts.append(current)
            current = []
        if current and abs(station - current[-1][0]) <= 1e-9 and abs(elevation - current[-1][1]) <= 1e-9:
            continue
        current.append((station, elevation))
    if current:
        parts.append(current)
    return [part for part in parts if len(part) >= 2]


def read_alignment_profiles(alignment, sample_interval=5.0, warnings=None):
    """Return ProfileLine records for one Alignment element."""
    alignment_name = alignment.attrib.get("name")
    result = []
    for node in descendants(alignment, "ProfAlign"):
        name = node.attrib.get("name") or "Design profile"
        try:
            samples = read_vertical_profile(node, sample_interval)
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f"Profile '{name}' skipped: {exc}")
            continue
        if len(samples) >= 2:
            result.append(
                ProfileLine(alignment_name, name, "design", None, node.attrib.get("desc"), [samples], node)
            )
    for node in descendants(alignment, "ProfSurf"):
        name = node.attrib.get("name") or "Surface profile"
        point_list = next((item for item in node if local_name(item.tag) == "PntList2D"), None)
        if point_list is None:
            continue
        try:
            parts = _surface_parts(point_list.text, f"Surface profile '{name}'")
        except ValueError as exc:
            if warnings is not None:
                warnings.append(str(exc))
            continue
        if parts:
            result.append(
                ProfileLine(alignment_name, name, "surface", node.attrib.get("state"), node.attrib.get("desc"), parts, node)
            )
    return result


def default_pair(profiles):
    """Pick (design, ground): first ProfAlign and the longest existing ProfSurf."""
    design = next((item for item in profiles if item.kind == "design"), None)
    surfaces = [item for item in profiles if item.kind == "surface"]
    existing = [item for item in surfaces if (item.state or "").lower() == "existing"] or surfaces
    ground = max(existing, key=lambda item: item.coverage) if existing else None
    return design, ground


def find_profile(profiles, name, kind=None):
    matches = [item for item in profiles if item.name == name and (kind is None or item.kind == kind)]
    if len(matches) > 1:
        raise ValueError(f"Profile name '{name}' is ambiguous for this alignment.")
    return matches[0] if matches else None
