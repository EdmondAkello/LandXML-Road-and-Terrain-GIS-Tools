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
    from .reports import _document, _table

    v, a, d = units.volume, units.area, units.depth
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
    from .reports import _document, _table

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
        "<h2>Slope classes</h2>",
        _table(["Class", f"Area ({a})", "Share (%)"], class_rows, 2),
        f"<h2>Platforms ({len(rows)}; {flagged} below minimum fall)</h2>",
        _table(["Platform", f"Area ({a})", f"Mean level ({d})", f"Min ({d})", f"Max ({d})", "Overall fall (%)",
                "Falls towards", "Mean local grade (%)", "Area flatter than minimum (%)", "Flag"], rows, 2),
    ]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Platform levels and falls", "".join(body))
