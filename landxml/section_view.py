"""Drawable cross-section content: lines, cut/fill regions, layers and labels.

Everything is in source offset/elevation units; renderers only scale it.
"""

from __future__ import annotations

from .earthworks import SectionLine, design_line, section_areas, side_slopes


def difference_regions(upper, lower):
    """Polygons between two offset/elevation lines, as (kind, points) pairs.

    ``kind`` is "fill" where ``upper`` (design) lies above ``lower`` (ground)
    and "cut" where it lies below. Boundaries include the exact crossings.
    """
    upper, lower = SectionLine(upper), SectionLine(lower)
    low, high = max(upper.low, lower.low), min(upper.high, lower.high)
    if high <= low:
        return []
    xs = sorted({low, high} | {x for x in upper.offsets + lower.offsets if low < x < high})
    samples = []
    for x0, x1 in zip(xs, xs[1:]):
        a = (x0, upper.at(x0, "right"), lower.at(x0, "right"))
        b = (x1, upper.at(x1, "left"), lower.at(x1, "left"))
        d0, d1 = a[1] - a[2], b[1] - b[2]
        samples.append(a)
        if d0 * d1 < 0:
            t = d0 / (d0 - d1)
            z = a[1] + t * (b[1] - a[1])
            samples.append((x0 + t * (x1 - x0), z, z))
        samples.append(b)
    regions, current, kind = [], [], None
    for x, zu, zl in samples:
        d = zu - zl
        point_kind = "fill" if d > 1e-9 else "cut" if d < -1e-9 else None
        if current and point_kind is not None and kind is not None and point_kind != kind:
            regions.append((kind, current))
            current = [current[-1]] if abs(current[-1][1] - current[-1][2]) <= 1e-9 else []
        if point_kind is not None:
            kind = point_kind
        current.append((x, zu, zl))
    if current and kind is not None:
        regions.append((kind, current))
    result = []
    for kind, points in regions:
        if len(points) < 2:
            continue
        outline = [(x, zu) for x, zu, _ in points] + [(x, zl) for x, _, zl in reversed(points)]
        result.append((kind, outline))
    return result


def _slope_labels(design, hinge_left, hinge_right, flat_ratio, steep_ratio, ground):
    line = SectionLine(design)
    ground_line = SectionLine(ground) if ground else None
    labels = []
    for x0, z0, x1, z1 in zip(line.offsets, line.values, line.offsets[1:], line.values[1:]):
        dx, dz = x1 - x0, z1 - z0
        if dx <= 1e-9 or dz == 0 or abs(dz) / dx < 1.0 / flat_ratio:
            continue
        mid = (x0 + x1) / 2.0
        if hinge_left is not None and hinge_right is not None and hinge_left < mid < hinge_right:
            continue
        if (dx * dx + dz * dz) ** 0.5 < 0.75:
            continue
        g = ground_line.at(mid) if ground_line is not None else None
        z_mid = (z0 + z1) / 2.0
        kind = ("fill" if z_mid >= g - 1e-6 else "cut") if g is not None else ("fill" if (dz < 0) == (mid > 0) else "cut")
        labels.append(
            {
                "offset": mid,
                "elevation": z_mid,
                "ratio": dx / abs(dz),
                "text": f"1:{dx / abs(dz):.1f}",
                "kind": kind,
                "steep": abs(dz) / dx > 1.0 / steep_ratio,
            }
        )
    return labels


def build_section_view(
    section,
    ground_name,
    design_name,
    grade=None,
    use_datum=False,
    flat_ratio=10.0,
    steep_ratio=1.5,
):
    """Return a dict describing one section for plotting and tabulation."""
    ground_surface = section.surface(ground_name) if ground_name else None
    design_surface = section.surface(design_name) if design_name else None
    ground = list(ground_surface.points) if ground_surface else None
    design = list(design_surface.points) if design_surface else None
    view = {
        "station": section.station,
        "name": section.name,
        "alignment": section.alignment_name,
        "ground_name": ground_name,
        "design_name": design_name,
        "ground": ground,
        "design": design,
        "datum": None,
        "others": [(s.name, list(s.points)) for s in section.surfaces if s.name not in (ground_name, design_name)],
        "shapes": [],
        "regions": [],
        "slope_labels": [],
        "grade": grade,
        "cut_area": None,
        "fill_area": None,
        "ground_cl": SectionLine(ground).at(0.0) if ground else None,
        "design_cl": SectionLine(design).at(0.0) if design else None,
        "daylight": (None, None),
        "materials": {},
    }
    if grade is not None:
        for shape in section.shapes:
            relative = all(abs(z - grade) >= abs(z) for _o, z in shape.points)
            points = [(o, grade + z if relative else z) for o, z in shape.points]
            view["shapes"].append((shape.name, points))
            view["materials"][shape.name] = view["materials"].get(shape.name, 0.0) + shape.area
    else:
        for shape in section.shapes:
            view["materials"][shape.name] = view["materials"].get(shape.name, 0.0) + shape.area
    if design is None:
        return view
    earthworks_line, datum_used = design_line(section, design_name, use_datum, grade)
    if datum_used:
        view["datum"] = earthworks_line
    extent = section.shape_extent()
    hinge_left, hinge_right = (extent if extent else (None, None))
    if ground:
        areas = section_areas(earthworks_line, ground)
        if areas is not None:
            view["cut_area"], view["fill_area"] = areas[0], areas[1]
            view["regions"] = difference_regions(earthworks_line, ground)
        slopes = side_slopes(design, ground, hinge_left, hinge_right, flat_ratio, steep_ratio)
        view["daylight"] = (slopes["left"]["daylight"], slopes["right"]["daylight"])
        hinge_left, hinge_right = slopes["left"]["hinge"], slopes["right"]["hinge"]
    else:
        view["daylight"] = (design[0][0], design[-1][0])
    view["slope_labels"] = _slope_labels(design, hinge_left, hinge_right, flat_ratio, steep_ratio, ground)
    return view


def view_extent(views, margin=2.0, symmetric=True):
    """Common offset range covering every section's design (or ground) width."""
    lows, highs = [], []
    for view in views:
        line = view["design"] or view["ground"]
        if not line:
            continue
        lows.append(min(p[0] for p in line))
        highs.append(max(p[0] for p in line))
    if not lows:
        return (-10.0, 10.0)
    low, high = min(lows) - margin, max(highs) + margin
    if symmetric:
        reach = max(abs(low), abs(high))
        return (-reach, reach)
    return (low, high)
