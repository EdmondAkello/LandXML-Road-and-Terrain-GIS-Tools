"""Absolute-coordinate cross-section records from standard LandXML."""

from __future__ import annotations

from dataclasses import dataclass

from .common import descendants, first_descendant
from .features import _points


@dataclass(frozen=True)
class CrossSection:
    alignment_name: str | None
    station: float | None
    name: str | None
    description: str | None
    points: tuple[tuple[float, ...], ...]


def read_cross_sections(root) -> tuple[list[CrossSection], list[str]]:
    """Read absolute point lists; never position station-relative design templates."""
    result = []
    warnings = []
    seen = set()
    scopes = [
        (alignment.attrib.get("name"), alignment)
        for alignment in descendants(root, "Alignment")
    ]
    scopes.append((None, root))
    for alignment_name, scope in scopes:
        for section in descendants(scope, "CrossSect"):
            if id(section) in seen:
                continue
            seen.add(id(section))
            name = section.attrib.get("name")
            label = f"CrossSect '{name or section.attrib.get('sta', '?')}'"
            if (
                first_descendant(section, "PntList3D") is None
                and first_descendant(section, "PntList2D") is None
            ):
                warnings.append(
                    f"{label} has only station-relative or missing points; no GIS geometry was created."
                )
                continue
            try:
                points = _points(section, label)
                station = (
                    float(section.attrib["sta"]) if section.attrib.get("sta") else None
                )
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            result.append(
                CrossSection(
                    alignment_name=alignment_name or section.attrib.get("alignment"),
                    station=station,
                    name=name,
                    description=section.attrib.get("desc"),
                    points=points,
                )
            )
    return result, warnings
