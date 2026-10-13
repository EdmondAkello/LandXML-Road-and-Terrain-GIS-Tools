"""Cross-section records from standard LandXML.

``PntList3D`` lists are absolute X/Y/Z and are returned here. ``PntList2D``
lists inside ``CrossSect`` are offset/elevation pairs relative to the
alignment (LandXML CrossSectSurf semantics); they are never returned as map
X/Y. Use :mod:`landxml.corridor` with an alignment to position them.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .common import descendants, local_name


@dataclass(frozen=True)
class CrossSection:
    alignment_name: str | None
    station: float | None
    name: str | None
    description: str | None
    points: tuple[tuple[float, ...], ...]
    surface_name: str | None = None


def _absolute_points(node, label):
    tokens = (node.text or "").split()
    if len(tokens) % 3:
        raise ValueError(f"{label} has {len(tokens)} coordinate values, not a multiple of 3")
    if len(tokens) < 6:
        raise ValueError(f"{label} requires at least two points")
    try:
        values = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"{label} contains a nonnumeric coordinate") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} contains a nonfinite coordinate")
    return tuple(tuple(values[i : i + 3]) for i in range(0, len(values), 3))


def read_cross_sections(root) -> tuple[list[CrossSection], list[str]]:
    """Read absolute PntList3D sections; report station-relative sections separately."""
    result = []
    warnings = []
    relative = 0
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
            try:
                station = float(section.attrib["sta"]) if section.attrib.get("sta") else None
            except ValueError:
                warnings.append(f"{label} has a nonnumeric station.")
                continue
            absolute = [node for node in descendants(section, "PntList3D")]
            if not absolute:
                if any(True for _ in descendants(section, "PntList2D")) or any(
                    True for _ in descendants(section, "CrossSectPnt")
                ):
                    relative += 1
                else:
                    warnings.append(f"{label} has no section points.")
                continue
            for node in absolute:
                parent = next(
                    (
                        item
                        for item in section.iter()
                        if node in list(item) and local_name(item.tag) == "CrossSectSurf"
                    ),
                    None,
                )
                surface_name = parent.attrib.get("name") if parent is not None else None
                try:
                    points = _absolute_points(node, label)
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
                        surface_name=surface_name,
                    )
                )
    if relative:
        warnings.append(
            f"{relative} cross section(s) hold station-relative offset/elevation data; "
            "they are positioned along their alignment, never read as map X/Y."
        )
    return result, warnings
