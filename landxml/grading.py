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
