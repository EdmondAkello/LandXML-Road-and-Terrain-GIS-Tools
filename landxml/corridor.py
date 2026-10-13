"""Station-relative corridor cross-sections (Civil 3D sample-line exports).

LandXML ``CrossSect`` records hold, per station:

* ``CrossSectSurf/PntList2D`` — surface sections as offset/elevation pairs
  (absolute elevations, offsets negative left of the alignment);
* ``DesignCrossSectSurf`` — corridor links and shapes as ``CrossSectPnt``
  offset/elevation pairs. Civil 3D writes these relative to the profile grade
  (crown 0 0); ``closedArea="true"`` shapes carry their area (for example
  pavement, base and subbase layers). Open links named ``Datum`` describe the
  bottom of the pavement structure.

Nothing here assigns map coordinates; tools position offsets along the
horizontal alignment explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .common import descendants, local_name


@dataclass
class SectionSurface:
    name: str
    points: list  # [(offset, elevation)], sorted by offset


@dataclass
class DesignShape:
    name: str
    side: str | None
    area: float
    declared_area: float | None
    points: list  # [(offset, elevation relative to grade or absolute)]


@dataclass
class CorridorSection:
    alignment_name: str | None
    station: float
    name: str | None
    surfaces: list = field(default_factory=list)
    shapes: list = field(default_factory=list)
    datum_links: list = field(default_factory=list)
    other_links: int = 0

    def surface(self, name):
        return next((item for item in self.surfaces if item.name == name), None)

    def shape_extent(self):
        offsets = [point[0] for shape in self.shapes for point in shape.points]
        return (min(offsets), max(offsets)) if offsets else None


def _pairs(text, label):
    tokens = (text or "").split()
    if len(tokens) % 2:
        raise ValueError(f"{label} has an odd number of offset/elevation values")
    values = [float(token) for token in tokens]
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} contains a nonfinite value")
    return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]


def shoelace(points):
    area = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def read_corridor_sections(root, alignment_filter=None):
    """Return (sections, warnings, surface names, shape names) for every alignment."""
    sections, warnings = [], []
    surface_names, shape_names = [], []
    for alignment in descendants(root, "Alignment"):
        alignment_name = alignment.attrib.get("name")
        if alignment_filter and alignment_name != alignment_filter:
            continue
        for node in descendants(alignment, "CrossSect"):
            label = f"CrossSect '{node.attrib.get('name') or node.attrib.get('sta', '?')}'"
            try:
                station = float(node.attrib["sta"])
            except (KeyError, ValueError):
                warnings.append(f"{label} has no numeric station and was skipped.")
                continue
            section = CorridorSection(alignment_name, station, node.attrib.get("name"))
            for item in node:
                tag = local_name(item.tag)
                if tag == "CrossSectSurf":
                    point_list = next((c for c in item if local_name(c.tag) == "PntList2D"), None)
                    if point_list is None:
                        continue
                    name = item.attrib.get("name") or "Unnamed surface"
                    try:
                        points = sorted(_pairs(point_list.text, f"{label} surface '{name}'"), key=lambda p: p[0])
                    except ValueError as exc:
                        warnings.append(str(exc))
                        continue
                    if len(points) < 2:
                        continue
                    existing = section.surface(name)
                    if existing is not None:
                        # Civil 3D can split one surface section into several lists.
                        existing.points = sorted(existing.points + points, key=lambda p: p[0])
                    else:
                        section.surfaces.append(SectionSurface(name, points))
                        if name not in surface_names:
                            surface_names.append(name)
                elif tag == "DesignCrossSectSurf":
                    try:
                        points = []
                        for point in item:
                            if local_name(point.tag) != "CrossSectPnt":
                                continue
                            parts = (point.text or "").split()
                            if len(parts) < 2:
                                raise ValueError(f"{label} has an incomplete CrossSectPnt")
                            points.append((float(parts[0]), float(parts[1])))
                    except ValueError as exc:
                        warnings.append(str(exc))
                        continue
                    name = item.attrib.get("name")
                    if (item.attrib.get("closedArea") or "").lower() == "true" and len(points) >= 3:
                        ring = points[:-1] if points[0] == points[-1] else points
                        declared = item.attrib.get("area")
                        try:
                            declared = float(declared) if declared not in (None, "") else None
                        except ValueError:
                            declared = None
                        computed = shoelace(ring)
                        shape_name = name or "Unnamed shape"
                        section.shapes.append(
                            DesignShape(shape_name, item.attrib.get("side"), declared if declared is not None else computed, declared, ring)
                        )
                        if shape_name not in shape_names:
                            shape_names.append(shape_name)
                    elif (name or "").lower() == "datum" and len(points) >= 2:
                        section.datum_links.append(points)
                    else:
                        section.other_links += 1
            sections.append(section)
    sections.sort(key=lambda item: (item.alignment_name or "", item.station))
    return sections, warnings, surface_names, shape_names


def _profile_name_matches(surface_name, ground_profiles):
    return any(surface_name and surface_name in (profile.name or "") for profile in ground_profiles)


def guess_surfaces(sections, ground_profiles=(), design_refs=()):
    """Suggest (ground, design) CrossSectSurf names from explicit source evidence.

    Ground: a section surface whose name appears inside an existing-ground
    ProfSurf name. Design: a section surface whose name contains a Roadway
    ``surfaceRefs`` token (corridor surfaces). Returns None for either when the
    evidence is missing; the caller must then ask the user.
    """
    names = []
    for section in sections:
        for surface in section.surfaces:
            if surface.name not in names:
                names.append(surface.name)
    ground = next((name for name in names if _profile_name_matches(name, ground_profiles)), None)
    refs = [token.lower() for token in design_refs if token]
    design = next(
        (
            name
            for name in names
            if name != ground and any(name.lower() == ref or name.lower().endswith(" " + ref) for ref in refs)
        ),
        None,
    )
    if design is None:
        remaining = [name for name in names if name != ground]
        if len(remaining) == 1 and ground is not None:
            design = remaining[0]
    return ground, design


def roadway_surface_refs(root):
    refs = []
    for roadway in descendants(root, "Roadway"):
        refs.extend((roadway.attrib.get("surfaceRefs") or "").split())
    return refs


def place_offsets(polyline, station, points):
    """Map [(offset, elevation)] at a station to source [(x, y, z)]; None if off-alignment."""
    placed = []
    for offset, elevation in points:
        xy = polyline.point_at(station, offset)
        if xy is None:
            return None
        placed.append((xy[0], xy[1], elevation))
    return placed


def placed_section_surfaces(sections, polylines):
    """Yield placed surface sections and count those that could not be placed.

    ``polylines`` maps alignment name to a StationedPolyline. Each yielded
    record has alignment_name, station, name, surface_name, offsets and
    source XYZ points.
    """
    placed, unplaced = [], 0
    for section in sections:
        polyline = polylines.get(section.alignment_name)
        for surface in section.surfaces:
            points = place_offsets(polyline, section.station, surface.points) if polyline else None
            if points is None:
                unplaced += 1
                continue
            placed.append(
                {
                    "alignment_name": section.alignment_name,
                    "station": section.station,
                    "name": section.name,
                    "surface_name": surface.name,
                    "offsets": [offset for offset, _ in surface.points],
                    "points": points,
                }
            )
    return placed, unplaced
