"""Exact earthwork quantities from a volume (depth) TIN surface.

A volume surface, such as a Civil 3D TIN volume surface, stores depth as
design minus existing at each vertex: positive is fill, negative is cut.
Every triangle is a linear prism, so its cut and fill volumes and the plan
areas above and below any depth have closed forms. No grid is used.

Plan areas and volumes use the horizontal units of the supplied coordinates;
depths stay in the source vertical units. Unit conversion is the caller's job.
"""

from __future__ import annotations

import html
import math

import numpy as np

VOLUME_SURFACE_HINTS = ("earthwork", "volume", "cut fill", "cut/fill", "cutfill", "cut & fill",
                        "difference", "isopach", "depth")
DEFAULT_BANDS = (-5.0, -3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
ELEVATION_WARNING = 100.0


def triangles(xyz, faces):
    """Return sorted vertex depths (n, 3) and plan areas (n,) of each triangle."""
    xyz = np.asarray(xyz, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    depths = np.sort(np.stack([a[:, 2], b[:, 2], c[:, 2]], axis=1), axis=1)
    return depths, area


def fraction_below(depths, level, inclusive=False):
    """Plan-area fraction of each linear triangle with depth below ``level``.

    ``depths`` are sorted per row (a <= b <= c). For a < t <= b the fraction
    is (t - a)^2 / ((b - a)(c - a)); for b < t < c it is
    1 - (c - t)^2 / ((c - a)(c - b)). Flat triangles count as below only when
    ``inclusive`` and their depth equals ``level``.
    """
    a, b, c = depths.T
    t = float(level)
    out = np.where((c < t) | ((c == t) & (c > a)), 1.0, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lower = (a < t) & (t <= b) & (c > a)
        out = np.where(lower, (t - a) ** 2 / ((b - a) * (c - a)), out)
        upper = (b < t) & (t < c)
        out = np.where(upper, 1.0 - (c - t) ** 2 / ((c - a) * (c - b)), out)
    if inclusive:
        out = np.where((c == t) & (a <= t), 1.0, out)
    return np.clip(out, 0.0, 1.0)


def split_volumes(depths, area):
    """Exact fill (+) and cut (+) prism volumes per triangle, split at depth zero."""
    a, b, c = depths.T
    total = area * (a + b + c) / 3.0
    fill = np.where(a >= 0, total, 0.0)
    cut = np.where(c <= 0, -total, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lone_cut = (a < 0) & (c > 0) & (b >= 0)
        fraction = np.where(lone_cut, (a / (a - b)) * (a / (a - c)), 0.0)
        under = area * fraction * a / 3.0
        cut = np.where(lone_cut, -under, cut)
        fill = np.where(lone_cut, total - under, fill)
        lone_fill = (a < 0) & (c > 0) & (b < 0)
        fraction = np.where(lone_fill, (c / (c - a)) * (c / (c - b)), 0.0)
        over = area * fraction * c / 3.0
        fill = np.where(lone_fill, over, fill)
        cut = np.where(lone_fill, over - total, cut)
    return fill, cut


def volume_summary(xyz, faces, bands=DEFAULT_BANDS):
    """Cut, fill and net volumes, plan areas and plan area by depth band."""
    depths, area = triangles(xyz, faces)
    if not len(area):
        raise ValueError("The volume surface has no triangles.")
    fill, cut = split_volumes(depths, area)
    below_zero = fraction_below(depths, 0.0)
    at_or_below_zero = fraction_below(depths, 0.0, inclusive=True)
    edges = sorted(float(value) for value in bands)
    if len(set(edges)) != len(edges) or not all(math.isfinite(value) for value in edges):
        raise ValueError("Depth band limits must be finite and distinct.")
    cumulative = [0.0] + [float((area * fraction_below(depths, edge)).sum()) for edge in edges] + [float(area.sum())]
    labels = ([f"< {edges[0]:g}"] if edges else []) + [
        f"{low:g} to {high:g}" for low, high in zip(edges, edges[1:])
    ] + ([f"≥ {edges[-1]:g}"] if edges else ["all"])
    band_rows = [
        {"band": label, "low": low, "high": high, "area": max(hi_area - lo_area, 0.0)}
        for label, low, high, lo_area, hi_area in zip(
            labels, [None] + edges, edges + [None], cumulative, cumulative[1:]
        )
    ]
    zmin, zmax = float(depths[:, 0].min()), float(depths[:, 2].max())
    cut_volume, fill_volume = float(cut.sum()), float(fill.sum())
    return {
        "cut_volume": cut_volume,
        "fill_volume": fill_volume,
        "net_volume": fill_volume - cut_volume,
        "plan_area": float(area.sum()),
        "cut_area": float((area * below_zero).sum()),
        "fill_area": float((area * (1.0 - at_or_below_zero)).sum()),
        "grade_area": float((area * (at_or_below_zero - below_zero)).sum()),
        "max_cut": max(-zmin, 0.0),
        "max_fill": max(zmax, 0.0),
        "triangle_count": int(len(area)),
        "bands": band_rows,
    }


def parse_bands(text):
    """Comma- or space-separated depth limits; blank gives the defaults."""
    text = (text or "").strip()
    if not text:
        return DEFAULT_BANDS
    try:
        values = tuple(float(item) for item in text.replace(";", ",").replace(",", " ").split())
    except ValueError as exc:
        raise ValueError("Depth bands must be numbers separated by commas, e.g. -2, -1, 0, 1, 2.") from exc
    if len(set(values)) != len(values) or not all(math.isfinite(v) for v in values):
        raise ValueError("Depth band limits must be finite and distinct.")
    return tuple(sorted(values))


def depth_warning(summary, limit=ELEVATION_WARNING):
    """Return a warning when the surface looks like elevations rather than depths."""
    if summary["max_cut"] > limit or summary["max_fill"] > limit:
        return (f"Depths reach {max(summary['max_cut'], summary['max_fill']):,.1f} source vertical units. "
                "This looks like an elevation surface; select the volume (depth) surface.")
    return None


def suggest_volume_surface(names, recorded_volumes=()):
    """Choose a volume surface only from unambiguous evidence; return (name, reason) or (None, reason)."""
    recorded = [name for name, _base, _compare in recorded_volumes if name in names]
    if len(set(recorded)) == 1:
        return recorded[0], "Civil 3D volume record names this surface"
    hinted = [name for name in names if any(hint in name.lower() for hint in VOLUME_SURFACE_HINTS)]
    if len(hinted) == 1:
        return hinted[0], "its name identifies it as a volume surface"
    if hinted:
        return None, "several surfaces could be volume surfaces: " + ", ".join(hinted)
    return None, "no surface is named or recorded as a volume surface"


def reconcile(computed, reference, tolerance):
    """Compare computed quantities with reference values (same units).

    ``reference`` maps a key of ``computed`` to a value or None. Returns rows
    (key, computed, reference, difference, status) and an overall status:
    "PASS", "FAIL" or "NOT CHECKED".
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Reconciliation tolerance must be zero or positive.")
    rows = []
    for key, value in reference.items():
        if value is None:
            continue
        difference = computed[key] - float(value)
        rows.append((key, computed[key], float(value), difference,
                     "PASS" if abs(difference) <= tolerance else "FAIL"))
    if not rows:
        return rows, "NOT CHECKED"
    return rows, "FAIL" if any(row[4] == "FAIL" for row in rows) else "PASS"


LABELS = {"cut_volume": "Cut volume", "fill_volume": "Fill volume", "net_volume": "Net (fill − cut)",
          "plan_area": "Plan area"}


def grading_report_html(summary, surface, units, source_file="", checks=(), status="NOT CHECKED",
                        tolerance=None, warnings=()):
    """HTML report; ``summary`` volumes already converted to ``units.volume``."""
    from .grading_charts import depth_bands_svg
    from .reports import _cards, _document, _table

    v, a, d = units.volume, units.area, units.depth
    cards = _cards([(f"Cut ({v})", f"{summary['cut_volume']:,.2f}"), (f"Fill ({v})", f"{summary['fill_volume']:,.2f}"),
                    (f"Net, fill − cut ({v})", f"{summary['net_volume']:+,.2f}"), (f"Plan area ({a})", f"{summary['plan_area']:,.2f}")]
                   + ([("Reconciliation", status)] if checks else []))
    rows = [
        (f"Cut volume ({v})", summary["cut_volume"]),
        (f"Fill volume ({v})", summary["fill_volume"]),
        (f"Net, fill − cut ({v})", summary["net_volume"]),
        (f"Plan area ({a})", summary["plan_area"]),
        (f"Cut area ({a})", summary["cut_area"]),
        (f"Fill area ({a})", summary["fill_area"]),
        (f"At grade ({a})", summary["grade_area"]),
        (f"Maximum cut depth ({d})", summary["max_cut"]),
        (f"Maximum fill depth ({d})", summary["max_fill"]),
        ("Triangles", summary["triangle_count"]),
    ]
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · volume surface: {html.escape(surface)}</p>",
        "<p class='note'>Exact prism volumes of the volume surface's triangles, split at zero depth. "
        "Positive depth is fill and negative is cut. Volumes are in place, with no shrinkage or bulking factors.</p>",
        cards,
        "<h2>Quantities</h2>",
        _table(["Quantity", "Value"], rows, 3),
    ]
    if checks:
        unit = {"plan_area": a}
        body += [
            f"<h2>Reconciliation: {html.escape(status)}</h2>",
            f"<p class='note'>Tolerance ±{tolerance:g} in the reported units. Compare with unadjusted "
            "(factor 1.0) Civil 3D volumes.</p>",
            _table(["Quantity", "This tool", "Reference", "Difference", "Status"],
                   [(f"{LABELS[key]} ({unit.get(key, v)})", value, ref, diff, state)
                    for key, value, ref, diff, state in checks], 3),
        ]
    band_rows = [(row["band"], row["area"], 100.0 * row["area"] / summary["plan_area"] if summary["plan_area"] else 0.0)
                 for row in summary["bands"]]
    body += [
        f"<h2>Plan area by depth ({d})</h2>",
        "<p class='note'>Exact areas of the linear triangles between the depth limits; a band includes "
        "its lower limit.</p>",
        depth_bands_svg(summary["bands"], units),
        _table([f"Depth band ({d})", f"Area ({a})", "Share (%)"], band_rows, 2),
    ]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Grading quantities", "".join(body))


# --------------------------------------------------------------------- platforms
SLOPE_CLASSES = (
    ("flat", "Flat (platforms)"),
    ("sloping", "Sloping (ramps, graded areas)"),
    ("batter", "Batter (1V:3.3H to 1V:0.4H)"),
    ("steep", "Near-vertical (retained edges)"),
)
BATTER_FROM = 0.3   # V/H
STEEP_FROM = 2.5    # V/H


def _oriented_faces(xyz, faces):
    """Faces reordered counter-clockwise in plan, so shared edges run in opposite directions."""
    faces = np.asarray(faces, dtype=np.int64).copy()
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    clockwise = cross < 0
    faces[clockwise, 1], faces[clockwise, 2] = faces[clockwise, 2], faces[clockwise, 1].copy()
    return faces


def triangle_slopes(xyz, faces, vertical_factor=1.0):
    """Slope of each triangle as V/H, with depths/elevations scaled into horizontal units."""
    xyz = np.asarray(xyz, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    u, v = b - a, c - a
    u[:, 2] *= vertical_factor
    v[:, 2] *= vertical_factor
    n = np.cross(u, v)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = np.hypot(n[:, 0], n[:, 1]) / np.abs(n[:, 2])
    return np.where(np.isfinite(slope), slope, np.inf)


def _edge_keys(faces):
    return np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])


def connected_triangles(faces, mask):
    """Label triangles in ``mask`` by edge-connected component (-1 elsewhere)."""
    faces = np.asarray(faces, dtype=np.int64)
    labels = np.full(len(faces), -1, dtype=np.int64)
    index = np.flatnonzero(mask)
    if not len(index):
        return labels
    sub = faces[index]
    edges = np.sort(_edge_keys(sub), axis=1)
    owner = np.tile(np.arange(len(sub)), 3)
    span = int(faces.max()) + 1
    key = edges[:, 0] * span + edges[:, 1]
    order = np.argsort(key, kind="stable")
    key, owner = key[order], owner[order]
    same = np.flatnonzero(key[1:] == key[:-1])
    i, j = owner[same], owner[same + 1]
    parent = np.arange(len(sub))
    while True:
        ri, rj = parent[i], parent[j]
        low = np.minimum(ri, rj)
        before = parent.copy()
        np.minimum.at(parent, ri, low)
        np.minimum.at(parent, rj, low)
        while True:
            jumped = parent[parent]
            if np.array_equal(jumped, parent):
                break
            parent = jumped
        if np.array_equal(parent, before):
            break
    _roots, compact = np.unique(parent, return_inverse=True)
    labels[index] = compact
    return labels


def _ring_area(ring):
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _inside(point, ring):
    x, y = point
    xs, ys = ring[:, 0], ring[:, 1]
    xn, yn = np.roll(xs, -1), np.roll(ys, -1)
    crosses = ((ys > y) != (yn > y)) & (x < (xn - xs) * (y - ys) / np.where(yn == ys, 1e-300, yn - ys) + xs)
    return bool(np.count_nonzero(crosses) % 2)


def outline_polygons(xyz, faces):
    """Plan outline of a set of triangles as [(outer ring, [holes]), ...]; rings are (n, 2) arrays."""
    faces = _oriented_faces(xyz, faces)
    directed = _edge_keys(faces)
    span = int(faces.max()) + 1
    forward = directed[:, 0] * span + directed[:, 1]
    reverse = directed[:, 1] * span + directed[:, 0]
    boundary = directed[~np.isin(forward, reverse)]
    outgoing = {}
    for start, end in boundary.tolist():
        outgoing.setdefault(start, []).append(end)
    rings = []
    while outgoing:
        start = next(iter(outgoing))
        ring, vertex = [start], start
        while True:
            ends = outgoing.get(vertex)
            if not ends:
                break
            nxt = ends.pop()
            if not ends:
                del outgoing[vertex]
            if nxt == start:
                break
            ring.append(nxt)
            vertex = nxt
        if len(ring) >= 3:
            rings.append(xyz[ring, :2])
    outers = [ring for ring in rings if _ring_area(ring) > 0]
    holes = [ring for ring in rings if _ring_area(ring) < 0]
    polygons = [(outer, []) for outer in sorted(outers, key=_ring_area, reverse=True)]
    for hole in holes:
        for outer, inner in sorted(polygons, key=lambda item: _ring_area(item[0])):
            if _inside(hole.mean(axis=0) if len(hole) < 3 else hole[0], outer):
                inner.append(hole)
                break
    return polygons


def platform_summary(xyz, faces, vertical_factor=1.0, flat=0.02, min_fall=0.005, min_area=50.0, outlines=True):
    """Slope-class areas and edge-connected flat platforms of a design (finished-level) surface.

    ``vertical_factor`` converts elevation units to horizontal units for slopes.
    ``flat`` and ``min_fall`` are V/H ratios; ``min_area`` is in plan units squared.
    Each platform reports area, area-weighted mean/min/max level, the overall fall and
    its downhill bearing from an area-weighted plane fit, the mean local grade and the
    share of its area flatter than ``min_fall``.
    """
    if not 0 < flat <= BATTER_FROM:
        raise ValueError(f"The flat threshold must be above 0 and at most {BATTER_FROM * 100:g} %.")
    if min_fall < 0 or min_area < 0:
        raise ValueError("Minimum fall and minimum platform area must be zero or positive.")
    xyz = np.asarray(xyz, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    depths, area = triangles(xyz, faces)
    slope = triangle_slopes(xyz, faces, vertical_factor)
    classes = {
        "flat": slope < flat,
        "sloping": (slope >= flat) & (slope < BATTER_FROM),
        "batter": (slope >= BATTER_FROM) & (slope < STEEP_FROM),
        "steep": slope >= STEEP_FROM,
    }
    class_areas = {key: float(area[mask].sum()) for key, mask in classes.items()}
    labels = connected_triangles(faces, classes["flat"])
    centroid = xyz[faces].mean(axis=1)
    mean_z = depths.mean(axis=1)
    platforms = []
    for label in range(int(labels.max()) + 1 if (labels >= 0).any() else 0):
        member = labels == label
        weight = area[member]
        total = float(weight.sum())
        if total < min_area or total <= 0:
            continue
        cx, cy = centroid[member, 0], centroid[member, 1]
        x0, y0 = np.average(cx, weights=weight), np.average(cy, weights=weight)
        design = np.column_stack([cx - x0, cy - y0, np.ones_like(cx)]) * np.sqrt(weight)[:, None]
        (gx, gy, _c), *_ = np.linalg.lstsq(design, mean_z[member] * np.sqrt(weight), rcond=None)
        fall = float(np.hypot(gx, gy)) * vertical_factor
        bearing = float(np.degrees(np.arctan2(-gx, -gy)) % 360.0) if fall > 1e-5 else None
        zs = xyz[np.unique(faces[member]), 2]
        platforms.append({
            "area": total,
            "level_mean": float(np.average(mean_z[member], weights=weight)),
            "level_min": float(zs.min()),
            "level_max": float(zs.max()),
            "fall": fall,
            "fall_bearing": bearing,
            "mean_grade": float(np.average(slope[member], weights=weight)),
            "share_below_min_fall": float(weight[slope[member] < min_fall].sum() / total),
            "below_min_fall": fall < min_fall,
            "triangles": int(member.sum()),
            "polygons": outline_polygons(xyz, faces[member]) if outlines else [],
        })
    platforms.sort(key=lambda item: -item["area"])
    for number, item in enumerate(platforms, 1):
        item["id"] = f"P{number}"
    return {
        "plan_area": float(area.sum()),
        "class_areas": class_areas,
        "platforms": platforms,
        "flat": flat,
        "min_fall": min_fall,
        "min_area": min_area,
        "flat_area_in_small_patches": class_areas["flat"] - sum(item["area"] for item in platforms),
    }


def platform_report_html(summary, surface, units, source_file="", warnings=()):
    """HTML report of slope classes and platforms; areas in ``units.area``, levels in ``units.depth``."""
    from .grading_charts import platforms_svg
    from .reports import _cards, _document, _table

    a, d = units.area, units.depth
    total = summary["plan_area"] or 1.0
    class_rows = [(label, summary["class_areas"][key], 100.0 * summary["class_areas"][key] / total)
                  for key, label in SLOPE_CLASSES]
    rows = [
        (item["id"], item["area"], item["level_mean"], item["level_min"], item["level_max"],
         100.0 * item["fall"], "–" if item["fall_bearing"] is None else f"{item['fall_bearing']:.0f}°",
         100.0 * item["mean_grade"], 100.0 * item["share_below_min_fall"],
         "below minimum fall" if item["below_min_fall"] else "")
        for item in summary["platforms"]
    ]
    flagged = sum(1 for item in summary["platforms"] if item["below_min_fall"])
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · design surface: {html.escape(surface)}</p>",
        f"<p class='note'>Flat means a triangle slope below {100 * summary['flat']:g} %. Touching flat triangles "
        f"form a platform; platforms under {summary['min_area']:g} {a} are not listed. Overall fall and its "
        "downhill direction (bearing in the output coordinates) come from an area-weighted plane fit; mean "
        f"local grade averages the triangle slopes. Platforms whose overall fall is below {100 * summary['min_fall']:g} % "
        "are flagged, because water can pond on them.</p>",
        _cards([("Platforms", str(len(rows))), ("Below minimum fall", str(flagged)),
                (f"Flat area ({a})", f"{summary['class_areas']['flat']:,.0f}"),
                (f"Batter area ({a})", f"{summary['class_areas']['batter']:,.0f}")]),
        "<h2>Slope classes</h2>",
        _table(["Class", f"Area ({a})", "Share (%)"], class_rows, 2),
        f"<h2>Platforms ({len(rows)}; {flagged} below minimum fall)</h2>",
        platforms_svg(summary["platforms"]),
        _table(["Platform", f"Area ({a})", f"Mean level ({d})", f"Min ({d})", f"Max ({d})", "Overall fall (%)",
                "Falls towards", "Mean local grade (%)", "Area flatter than minimum (%)", "Flag"], rows, 2),
    ]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Platform levels and falls", "".join(body))


# ----------------------------------------------------------------- basins
def stored_volume(xyz, faces, level):
    """Exact water volume and wet plan area of a TIN basin filled to ``level``.

    Water depth (level - z) is linear on each triangle, so the stored volume is the
    positive part of that depth integrated exactly (the same split as cut/fill).
    """
    depths, area = triangles(xyz, faces)
    water = np.sort(level - depths, axis=1)
    volume, _below = split_volumes(water, area)
    wet = area * fraction_below(depths, level)
    return float(volume.sum()), float(wet.sum())


def spill_level(xyz, faces):
    """Lowest elevation on the TIN's outer boundary: water overflows the surface there."""
    faces = _oriented_faces(xyz, faces)
    directed = _edge_keys(faces)
    span = int(faces.max()) + 1
    forward = directed[:, 0] * span + directed[:, 1]
    reverse = directed[:, 1] * span + directed[:, 0]
    boundary = np.unique(directed[~np.isin(forward, reverse)])
    return float(xyz[boundary, 2].min()) if len(boundary) else float(xyz[:, 2].max())


def stage_storage(xyz, faces, rim=None, freeboard=0.5, step=0.1, required=None):
    """Stage–storage table and capacity check for a basin surface.

    ``rim`` defaults to the spill level. Levels run from the basin floor to the rim in
    ``step`` increments (vertical units), with the freeboard level and rim included.
    The check compares the storage at the freeboard level (rim - freeboard) with
    ``required``: PASS, PASS TO RIM ONLY, FAIL or NOT CHECKED.
    """
    xyz = np.asarray(xyz, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if not len(faces):
        raise ValueError("The basin surface has no triangles.")
    if step <= 0 or freeboard < 0:
        raise ValueError("Stage step must be positive and freeboard zero or positive.")
    floor = float(xyz[faces].reshape(-1, 3)[:, 2].min())
    spill = spill_level(xyz, faces)
    rim = spill if rim is None else float(rim)
    if rim <= floor:
        raise ValueError(f"The rim level {rim:.3f} is not above the basin floor {floor:.3f}.")
    count = int(math.floor((rim - floor) / step + 1e-9))
    levels = sorted({round(floor + k * step, 9) for k in range(1, count + 1)} | {rim}
                    | ({rim - freeboard} if freeboard and rim - freeboard > floor else set()))
    rows = []
    for level in levels:
        volume, wet = stored_volume(xyz, faces, level)
        rows.append({"level": level, "depth": level - floor, "area": wet, "volume": volume})
    at_rim = next(row for row in rows if abs(row["level"] - rim) < 1e-9)
    design_level = rim - freeboard
    at_design = (next(row for row in rows if abs(row["level"] - design_level) < 1e-9)
                 if design_level > floor else {"level": design_level, "depth": 0.0, "area": 0.0, "volume": 0.0})
    if required is None:
        status = "NOT CHECKED"
    elif at_design["volume"] >= required:
        status = "PASS"
    elif at_rim["volume"] >= required:
        status = "PASS TO RIM ONLY"
    else:
        status = "FAIL"
    return {
        "floor": floor, "spill": spill, "rim": rim, "freeboard": freeboard, "design_level": design_level,
        "rim_volume": at_rim["volume"], "rim_area": at_rim["area"],
        "design_volume": at_design["volume"], "design_area": at_design["area"],
        "required_volume": required, "status": status, "rows": rows,
        "plan_area": float(triangles(xyz, faces)[1].sum()),
    }


def basin_report_html(summary, surface, units, source_file="", warnings=()):
    """HTML report of a basin's stage–storage table; volumes already converted to ``units.volume``."""
    from .grading_charts import stage_storage_svg
    from .reports import _cards, _document, _table

    v, a, d = units.volume, units.area, units.depth
    required = summary["required_volume"]
    rows = [
        (f"Floor level ({d})", summary["floor"]),
        (f"Spill level, lowest point on the surface edge ({d})", summary["spill"]),
        (f"Rim level used ({d})", summary["rim"]),
        (f"Freeboard ({d})", summary["freeboard"]),
        (f"Storage to the rim ({v})", summary["rim_volume"]),
        (f"Water area at the rim ({a})", summary["rim_area"]),
        (f"Storage with freeboard, to {summary['design_level']:.3f} ({v})", summary["design_volume"]),
        (f"Required storage ({v})", "–" if required is None else required),
    ]
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · basin surface: {html.escape(surface)}</p>",
        "<p class='note'>Storage below each level is integrated exactly over the surface's triangles "
        "(water depth is linear on each triangle). The check uses the storage with freeboard; "
        "PASS TO RIM ONLY means the required volume fits only if the basin is filled to the rim.</p>",
        f"<h2>Capacity: {html.escape(summary['status'])}</h2>",
        _cards([(f"Storage to the rim ({v})", f"{summary['rim_volume']:,.1f}"),
                (f"With freeboard ({v})", f"{summary['design_volume']:,.1f}"),
                (f"Required ({v})", "–" if required is None else f"{required:,.1f}"), ("Capacity", summary["status"])]),
        stage_storage_svg(summary, units),
        _table(["Quantity", "Value"], rows, 3),
        "<h2>Stage–storage table</h2>",
        _table([f"Level ({d})", f"Depth ({d})", f"Water area ({a})", f"Storage ({v})"],
               [(row["level"], row["depth"], row["area"], row["volume"]) for row in summary["rows"]], 3),
    ]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Basin stage–storage", "".join(body))


# ---------------------------------------------------------------- balance
DEFAULT_SHRINKAGE = (0.85, 0.90, 0.95)


def parse_factors(text, default=DEFAULT_SHRINKAGE):
    """Shrinkage factors (compacted volume per in-place volume) from a comma list."""
    text = (text or "").strip()
    if not text:
        return tuple(default)
    try:
        values = tuple(float(item) for item in text.replace(";", ",").replace(",", " ").split())
    except ValueError as exc:
        raise ValueError("Shrinkage factors must be numbers separated by commas, e.g. 0.85, 0.90, 0.95.") from exc
    if not values or not all(math.isfinite(v) and 0 < v <= 2 for v in values):
        raise ValueError("Each shrinkage factor must be above 0 and at most 2 (compacted per in-place volume).")
    return values


def earthwork_balance(sites, factors=DEFAULT_SHRINKAGE, unsuitable=0.0, topsoil_depth=0.0):
    """Earthwork balance for one or more sites under shrinkage scenarios.

    ``sites`` is a list of dicts with ``name``, ``cut``, ``fill`` (in-place volumes) and
    ``area`` (plan area). For each factor f, the usable cut of a site is
    cut × (1 − unsuitable) × f (compacted). Each site uses its own cut first; surplus
    from other sites then supplies the shortfalls; what remains is import (compacted
    fill from borrow, and its in-place equivalent ÷ f) or spoil (in place). Topsoil
    stripping (plan area × depth) is reported separately and not used as fill.
    """
    if not sites:
        raise ValueError("No sites to balance.")
    if not 0 <= unsuitable < 1:
        raise ValueError("The unsuitable share of cut must be at least 0 and below 1.")
    if topsoil_depth < 0:
        raise ValueError("Topsoil stripping depth must be zero or positive.")
    scenarios = []
    for factor in factors:
        rows = []
        for site in sites:
            usable = site["cut"] * (1.0 - unsuitable) * factor
            rows.append({"name": site["name"], "usable": usable, "fill": site["fill"],
                         "surplus": max(usable - site["fill"], 0.0), "shortfall": max(site["fill"] - usable, 0.0)})
        surplus = sum(row["surplus"] for row in rows)
        shortfall = sum(row["shortfall"] for row in rows)
        transfer = min(surplus, shortfall) if len(rows) > 1 else 0.0
        import_compacted = max(shortfall - surplus, 0.0)
        spoil_compacted = max(surplus - shortfall, 0.0)
        scenarios.append({
            "factor": factor, "sites": rows, "transfer": transfer,
            "import_compacted": import_compacted, "import_in_place": import_compacted / factor,
            "spoil_in_place": spoil_compacted / factor,
            "unsuitable_in_place": sum(site["cut"] for site in sites) * unsuitable,
        })
    return {
        "cut": sum(site["cut"] for site in sites), "fill": sum(site["fill"] for site in sites),
        "area": sum(site["area"] for site in sites), "sites": list(sites),
        "topsoil": sum(site["area"] for site in sites) * topsoil_depth, "topsoil_depth": topsoil_depth,
        "unsuitable": unsuitable, "scenarios": scenarios,
    }


def balance_report_html(balance, units, source_file="", warnings=()):
    """HTML report; volumes already in ``units.volume``, areas in ``units.area``."""
    from .grading_charts import bars_svg
    from .reports import CUT_COLOR, FILL_COLOR, _document, _table

    v, a = units.volume, units.area
    site_rows = [(site["name"], site["area"], site["cut"], site["fill"], site["fill"] - site["cut"])
                 for site in balance["sites"]]
    site_rows.append(("Total", balance["area"], balance["cut"], balance["fill"], balance["fill"] - balance["cut"]))
    scenario_rows = [
        (f"{item['factor']:.2f}", item["transfer"], item["import_compacted"], item["import_in_place"], item["spoil_in_place"])
        for item in balance["scenarios"]
    ]
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)}</p>",
        "<p class='note'>Cut and fill are exact in-place volumes from each volume surface. A shrinkage factor is the "
        "compacted volume obtained from one unit of in-place cut. Each site uses its own cut first; surplus from other "
        "sites then supplies the shortfalls. Import is the compacted fill still needed (and its in-place borrow "
        "equivalent at the same factor); spoil is surplus cut in place."
        + (f" {100 * balance['unsuitable']:g} % of cut is treated as unsuitable." if balance["unsuitable"] else "")
        + "</p>",
        "<h2>Sites (in place)</h2>",
        _table(["Site", f"Plan area ({a})", f"Cut ({v})", f"Fill ({v})", f"Net, fill − cut ({v})"], site_rows, 2),
        bars_svg([site["name"] for site in balance["sites"]],
                 [("Cut (in place)", CUT_COLOR, [site["cut"] for site in balance["sites"]]),
                  ("Fill", FILL_COLOR, [site["fill"] for site in balance["sites"]])], v),
        "<h2>Balance by shrinkage factor</h2>",
        bars_svg([f"Factor {item['factor']:.2f}" for item in balance["scenarios"]],
                 [("Moved between sites (compacted)", "#1f5f8b", [item["transfer"] for item in balance["scenarios"]]),
                  ("Import (compacted)", "#c0392b", [item["import_compacted"] for item in balance["scenarios"]]),
                  ("Spoil (in place)", "#7b4fbf", [item["spoil_in_place"] for item in balance["scenarios"]])], v),
        _table(["Shrinkage factor", f"Moved between sites ({v}, compacted)", f"Import ({v}, compacted)",
                f"Import ({v}, in place)", f"Spoil ({v}, in place)"], scenario_rows, 2),
    ]
    for item in balance["scenarios"]:
        body += [f"<h3>Factor {item['factor']:.2f}: by site</h3>",
                 _table(["Site", f"Usable cut ({v}, compacted)", f"Fill ({v})", f"Surplus ({v})", f"Shortfall ({v})"],
                        [(row["name"], row["usable"], row["fill"], row["surplus"], row["shortfall"]) for row in item["sites"]], 2)]
    if balance["topsoil_depth"]:
        body.append(f"<p class='note'>Topsoil stripping at {balance['topsoil_depth']:g} {units.depth} over the plan area: "
                    f"{balance['topsoil']:,.2f} {v} in place, reported separately and not used as fill.</p>")
    if balance["unsuitable"]:
        body.append(f"<p class='note'>Unsuitable cut (in place), to spoil in addition to any surplus: "
                    f"{balance['scenarios'][0]['unsuitable_in_place']:,.2f} {v}.</p>")
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Earthwork balance", "".join(body))


# ------------------------------------------------------- revision comparison
def _inside_many(points, ring):
    """Vectorised even-odd test of many (n, 2) points against one closed ring."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    xs, ys = ring[:, 0], ring[:, 1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(np.roll(xs, -1), np.roll(ys, -1))):
        if y0 == y1:
            continue
        crosses = ((y0 > y) != (y1 > y)) & (x < (x1 - x0) * (y - y0) / (y1 - y0) + x0)
        inside ^= crosses
    return inside


def zone_labels(points, zones, outside="Outside zones"):
    """Zone name for each point; ``zones`` is [(name, [(outer, [holes]), ...]), ...]. First match wins."""
    labels = np.full(len(points), outside, dtype=object)
    free = np.ones(len(points), dtype=bool)
    for name, polygons in zones:
        hit = np.zeros(len(points), dtype=bool)
        for outer, holes in polygons:
            part = _inside_many(points, np.asarray(outer, dtype=float))
            for hole in holes:
                part &= ~_inside_many(points, np.asarray(hole, dtype=float))
            hit |= part
        hit &= free
        labels[hit] = name
        free &= ~hit
    return labels


def _zone_volumes(xyz, faces, labels):
    depths, area = triangles(xyz, faces)
    fill, cut = split_volumes(depths, area)
    out = {}
    for name in dict.fromkeys(labels.tolist()):
        member = labels == name
        out[name] = {"cut": float(cut[member].sum()), "fill": float(fill[member].sum()), "area": float(area[member].sum())}
    return out


def revision_comparison(first, second, zones=()):
    """Compare two volume (depth) surfaces, each ``(xyz, faces)`` in the same coordinates.

    Returns totals and per-zone cut, fill and plan area for both revisions and the
    change (second − first). Triangles are assigned to zones by their centroid.
    """
    result = {"zones": []}
    totals = []
    for xyz, faces in (first, second):
        xyz = np.asarray(xyz, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.int64)
        summary = volume_summary(xyz, faces, ())
        labels = zone_labels(xyz[faces].mean(axis=1)[:, :2], zones) if zones else np.full(len(faces), "Outside zones", dtype=object)
        totals.append((summary, _zone_volumes(xyz, faces, labels)))
    (a, zones_a), (b, zones_b) = totals
    names = [name for name, _polygons in zones] + ["Outside zones"]
    for name in names:
        za = zones_a.get(name, {"cut": 0.0, "fill": 0.0, "area": 0.0})
        zb = zones_b.get(name, {"cut": 0.0, "fill": 0.0, "area": 0.0})
        if not (za["area"] or zb["area"]):
            continue
        result["zones"].append(_change_row(name, za["cut"], za["fill"], za["area"], zb["cut"], zb["fill"], zb["area"]))
    result["total"] = _change_row("All", a["cut_volume"], a["fill_volume"], a["plan_area"],
                                  b["cut_volume"], b["fill_volume"], b["plan_area"])
    return result


def _change_row(name, cut_a, fill_a, area_a, cut_b, fill_b, area_b):
    return {"name": name, "cut_a": cut_a, "fill_a": fill_a, "area_a": area_a, "cut_b": cut_b, "fill_b": fill_b,
            "area_b": area_b, "cut_change": cut_b - cut_a, "fill_change": fill_b - fill_a,
            "net_change": (fill_b - cut_b) - (fill_a - cut_a)}


def scale_comparison(comparison, factor):
    """Volumes × ``factor`` (unit conversion); areas unchanged."""
    keys = ("cut_a", "fill_a", "cut_b", "fill_b", "cut_change", "fill_change", "net_change")
    scale = lambda row: {k: (v * factor if k in keys else v) for k, v in row.items()}  # noqa: E731
    return {"zones": [scale(row) for row in comparison["zones"]], "total": scale(comparison["total"])}


def comparison_report_html(comparison, labels, units, sources=("", ""), warnings=()):
    """HTML report; ``labels`` names the two revisions, e.g. ("Rev B", "Rev C")."""
    from .grading_charts import bars_svg
    from .reports import CUT_COLOR, FILL_COLOR, _cards, _document, _table

    v, a = units.volume, units.area
    first, second = labels
    header = ["Zone", f"{first} cut ({v})", f"{first} fill ({v})", f"{second} cut ({v})", f"{second} fill ({v})",
              f"Cut change ({v})", f"Fill change ({v})", f"Net change, fill − cut ({v})"]
    as_row = lambda r: (r["name"], r["cut_a"], r["fill_a"], r["cut_b"], r["fill_b"], r["cut_change"],  # noqa: E731
                        r["fill_change"], r["net_change"])
    area_rows = [(r["name"], r["area_a"], r["area_b"], r["area_b"] - r["area_a"])
                 for r in comparison["zones"] + [comparison["total"]]]
    body = [
        f"<p class='meta'>{html.escape(first)}: {html.escape(sources[0])} · {html.escape(second)}: {html.escape(sources[1])}</p>",
        "<p class='note'>Exact in-place cut and fill of each revision's volume surface. Changes are the second revision "
        "minus the first. Triangles are assigned to zones by their centroid, so zone totals near zone edges are approximate; "
        "the overall totals are exact. A change can come from the design or from a revised existing-ground surface: "
        "compare the existing-ground surfaces separately (Surface Cut/Fill) before attributing it to the design.</p>",
        _cards([(f"Cut change ({v})", f"{comparison['total']['cut_change']:+,.2f}"),
                (f"Fill change ({v})", f"{comparison['total']['fill_change']:+,.2f}"),
                (f"Net change ({v})", f"{comparison['total']['net_change']:+,.2f}")]),
        "<h2>Overall</h2>", _table(header, [as_row(comparison["total"])], 2),
    ]
    zoned = comparison["zones"] and not (len(comparison["zones"]) == 1 and comparison["zones"][0]["name"] == "Outside zones")
    rows = comparison["zones"] if zoned else [comparison["total"]]
    body.append(bars_svg([row["name"] for row in rows],
                         [(f"{first} cut", "#f0b47a", [row["cut_a"] for row in rows]), (f"{second} cut", CUT_COLOR, [row["cut_b"] for row in rows]),
                          (f"{first} fill", "#9fd1b0", [row["fill_a"] for row in rows]), (f"{second} fill", FILL_COLOR, [row["fill_b"] for row in rows])], v))
    if zoned:
        body += ["<h2>By zone</h2>", _table(header, [as_row(row) for row in comparison["zones"]], 2)]
    body += [f"<h3>Graded plan area ({a})</h3>",
             _table(["Zone", f"{first} ({a})", f"{second} ({a})", f"Change ({a})"], area_rows if zoned else area_rows[-1:], 2)]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Grading revision comparison", "".join(body))


def change_grid(first_grid, second_grid, nodata):
    """Depth change (second − first) on a shared grid; missing depth counts as zero where the other exists."""
    a_valid = first_grid != np.float32(nodata)
    b_valid = second_grid != np.float32(nodata)
    a = np.where(a_valid, first_grid, 0.0).astype(np.float64)
    b = np.where(b_valid, second_grid, 0.0).astype(np.float64)
    out = np.full(first_grid.shape, np.float32(nodata), dtype=np.float32)
    either = a_valid | b_valid
    out[either] = (b - a)[either].astype(np.float32)
    return out
