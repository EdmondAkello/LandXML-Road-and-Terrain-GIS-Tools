"""Surface styles shared by QGIS layers, HTML reports and 3D scenes.

A style colours the triangles of a TIN by one attribute:

* ``elevation`` (vertical units),
* ``slope`` (percent; V/H × 100 with vertical units converted to horizontal ones),
* ``depth`` (design minus existing, vertical units; cut negative, fill positive),
* ``aspect`` (downhill bearing in degrees, 0 = north / +Y, clockwise).

A style is a plain dict, so it can be stored in a project, written into ``scene.json``
and read by any renderer::

    {"attribute": "slope", "breaks": [2, 5, 30, 250], "colors": [...], "labels": [...]}

``breaks`` are the upper limits of every class but the last (n breaks give n + 1
classes). Leave them out to use the preset's, or give ``"classes": n`` with
``"method": "equal"`` or ``"quantile"`` to compute them from the data. ``colors`` may be
a list of hex colours (one per class) or the name of a palette in ``PALETTES``.
"""

from __future__ import annotations

import math

import numpy as np

PALETTES = {
    "cut_fill": ["#7f2704", "#a63603", "#d9822b", "#f0b27a", "#fae0c4", "#fdf3e7",
                 "#eef7ef", "#d5ecd9", "#9fd3ac", "#5fb37b", "#3a9e5f", "#1d6b3a"],
    "slope_classes": ["#2b83ba", "#abdda4", "#fdae61", "#d7191c"],
    "terrain": ["#1a9641", "#73c044", "#c4e687", "#fdf2a3", "#f4c47d", "#d99456", "#a8673d", "#7f4f35", "#f0f0f0"],
    "greys": ["#f7f7f7", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252"],
    "aspect": ["#e41a1c", "#ff7f00", "#ffd92f", "#a6d854", "#4daf4a", "#66c2a5", "#377eb8", "#984ea3"],
}

PRESETS = {
    "depth": {"attribute": "depth", "breaks": [-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5], "colors": "cut_fill"},
    "slope": {"attribute": "slope", "breaks": [2, 30, 250], "colors": "slope_classes",
              "labels": ["Flat (platforms) < 2 %", "Sloping 2–30 %", "Batter 30–250 %", "Near-vertical > 250 %"]},
    "elevation": {"attribute": "elevation", "classes": 9, "method": "equal", "colors": "terrain"},
    "aspect": {"attribute": "aspect", "breaks": [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5],
               "colors": ["#e41a1c", "#ff7f00", "#ffd92f", "#a6d854", "#4daf4a", "#66c2a5", "#377eb8", "#984ea3",
                          "#e41a1c"],
               "labels": ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"]},
}
UNITS = {"elevation": "{v}", "depth": "{v}", "slope": "%", "aspect": "°"}


def triangle_values(xyz, faces, attribute, depth=None, vertical_factor=1.0):
    """Per-triangle value of ``attribute`` (see module notes); ``depth`` is per vertex."""
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    if attribute == "elevation":
        return xyz[faces, 2].mean(axis=1)
    if attribute == "depth":
        if depth is None:
            raise ValueError("A depth style needs the depth (design − existing) at each vertex.")
        return np.asarray(depth, float)[faces].mean(axis=1)
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    u, v = b - a, c - a
    u[:, 2] *= vertical_factor
    v[:, 2] *= vertical_factor
    n = np.cross(u, v)
    n[n[:, 2] < 0] *= -1
    if attribute == "slope":
        with np.errstate(divide="ignore", invalid="ignore"):
            slope = 100.0 * np.hypot(n[:, 0], n[:, 1]) / n[:, 2]
        return np.where(np.isfinite(slope), slope, 1e9)
    if attribute == "aspect":
        return np.degrees(np.arctan2(n[:, 0], n[:, 1])) % 360.0
    raise ValueError(f"Unknown surface attribute: {attribute}")


def _nice(value, step):
    """``value`` rounded to two significant figures of the class width ``step``."""
    if not math.isfinite(value) or step <= 0:
        return value
    return round(value, max(1 - int(math.floor(math.log10(step))), 0))


def resolve(style, values=None, vertical_unit="m"):
    """Classes of a style as [{"low", "high", "color", "label"}], computing data breaks if asked."""
    spec = dict(PRESETS.get(style.get("attribute"), {}), **style) if isinstance(style, dict) else dict(PRESETS[style])
    attribute = spec["attribute"]
    breaks = spec.get("breaks") if "classes" not in style else None
    if breaks is None:
        count = int(spec.get("classes", 9))
        data = np.asarray(values if values is not None else [], float)
        data = data[np.isfinite(data)]
        if count < 2 or not len(data):
            breaks = []
        elif spec.get("method", "equal") == "quantile":
            step = (float(data.max()) - float(data.min())) / count
            breaks = sorted(set(_nice(v, step) for v in np.quantile(data, np.linspace(0, 1, count + 1)[1:-1])))
        else:
            low, high = float(data.min()), float(data.max())
            step = (high - low) / count
            breaks = sorted(set(_nice(low + step * k, step) for k in range(1, count))) if high > low else []
    breaks = [float(b) for b in breaks]
    if breaks != sorted(breaks) or len(set(breaks)) != len(breaks):
        raise ValueError("Style breaks must be increasing.")
    colors = spec.get("colors", "greys")
    palette = PALETTES[colors] if isinstance(colors, str) else list(colors)
    count = len(breaks) + 1
    if len(palette) != count:      # stretch or sample a palette to the class count
        palette = [palette[round(k * (len(palette) - 1) / max(count - 1, 1))] for k in range(count)]
    unit = UNITS[attribute].format(v=vertical_unit)
    labels = spec.get("labels")
    classes = []
    for k in range(count):
        low = breaks[k - 1] if k else None
        high = breaks[k] if k < len(breaks) else None
        if labels and len(labels) == count:
            label = labels[k]
        elif low is None:
            label = f"< {high:g} {unit}"
        elif high is None:
            label = f"≥ {low:g} {unit}"
        else:
            label = f"{low:g} – {high:g} {unit}"
        classes.append({"low": low, "high": high, "color": palette[k], "label": label})
    return {"attribute": attribute, "unit": unit, "classes": classes}


def classify(values, resolved):
    """Class index of each value (a value equal to a break goes to the class above it)."""
    breaks = [item["high"] for item in resolved["classes"][:-1]]
    return np.searchsorted(np.asarray(breaks, float), np.asarray(values, float), side="right")


def parse_style(text, attribute=None):
    """A style from Processing text: a preset name, or ``attribute: b1, b2, ...`` with optional ``| #c1, #c2, ...``.

    ``plain #rrggbb`` gives one colour. Examples: ``slope``, ``slope: 1, 5, 30``, ``elevation: equal 12``, ``elevation: quantile 6 | terrain``,
    ``depth: -2, 0, 2 | #d9822b, #f7d9b8, #c7e9c0, #3a9e5f``."""
    text = (text or "").strip()
    if not text:
        return dict(PRESETS[attribute or "elevation"])
    if text.lower().startswith("plain"):
        color = text[5:].strip() or "#cfd8c4"
        return {"attribute": attribute or "elevation", "breaks": [], "colors": [color], "labels": ["Surface"]}
    head, _, colors = text.partition("|")
    name, _, breaks = head.partition(":")
    name = name.strip().lower()
    if name not in PRESETS:
        raise ValueError(f"Unknown surface style '{name}'. Use elevation, slope, depth or aspect.")
    style = {"attribute": name}
    breaks = breaks.strip()
    if breaks:
        words = breaks.replace(",", " ").split()
        if words[0].lower() in ("equal", "quantile"):
            style.update(method=words[0].lower(), classes=int(words[1]) if len(words) > 1 else 9)
        else:
            style["breaks"] = [float(word) for word in words]
            style.pop("labels", None)
    colors = colors.strip()
    if colors:
        names = [part.strip() for part in colors.replace(",", " ").split()]
        style["colors"] = names[0] if len(names) == 1 and names[0] in PALETTES else names
    return style


def _clip(polygon, values, level, keep_above):
    """Part of a planar polygon (points with a linear ``values``) on one side of ``level``."""
    out_p, out_v = [], []
    n = len(polygon)
    for k in range(n):
        p, q = polygon[k], polygon[(k + 1) % n]
        a, b = values[k], values[(k + 1) % n]
        inside_a = a >= level if keep_above else a <= level
        inside_b = b >= level if keep_above else b <= level
        if inside_a:
            out_p.append(p)
            out_v.append(a)
        if inside_a != inside_b:
            t = (level - a) / (b - a)
            out_p.append(p + t * (q - p))
            out_v.append(level)
    return out_p, out_v


def band_pieces(xyz, faces, values, resolved):
    """Exact class pieces of a surface whose ``values`` vary linearly on each triangle (elevation, depth).

    Every triangle is cut at the class breaks, so class areas are exact, like the depth bands of
    Grading Quantities. Returns {class: [(n, 2) polygons]}; pieces meet at T-junctions, so merge
    them with a geometry union where one outline per class is needed."""
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    values = np.asarray(values, float)
    breaks = [item["high"] for item in resolved["classes"][:-1]]
    tri_v = values[faces]
    lo_index = classify(tri_v.min(axis=1), resolved)
    hi_index = classify(tri_v.max(axis=1), resolved)
    pieces = {}
    same = lo_index == hi_index
    for k in np.unique(lo_index[same]):
        member = faces[same & (lo_index == k)]
        pieces.setdefault(int(k), []).extend(list(xyz[member][:, :, :2]))
    for t in np.flatnonzero(~same):
        corners = list(xyz[faces[t], :2])
        vals = list(tri_v[t])
        for k in range(lo_index[t], hi_index[t] + 1):
            polygon, pv = corners, vals
            if k > 0:
                polygon, pv = _clip([np.asarray(c) for c in polygon], pv, breaks[k - 1], True)
            if k < len(breaks) and len(polygon) >= 3:
                polygon, pv = _clip([np.asarray(c) for c in polygon], pv, breaks[k], False)
            if len(polygon) >= 3:
                pieces.setdefault(int(k), []).append(np.asarray(polygon))
    return pieces


def piece_areas(pieces, count):
    areas = np.zeros(count)
    for k, polygons in pieces.items():
        for ring in polygons:
            x, y = ring[:, 0], ring[:, 1]
            areas[k] += 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))
    return areas


def is_linear(attribute):
    """Elevation and depth vary linearly on a triangle, so they can be banded exactly."""
    return attribute in ("elevation", "depth")


def class_regions(xyz, faces, index, count):
    """Merged plan polygons per class: {class: [(outer, [holes]), ...]}."""
    from .grading import outline_polygons

    regions = {}
    for k in range(count):
        member = faces[index == k]
        if len(member):
            regions[k] = outline_polygons(xyz, member)
    return regions


def class_areas(xyz, faces, index, count):
    a, b, c = xyz[faces[:, 0], :2], xyz[faces[:, 1], :2], xyz[faces[:, 2], :2]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    return np.bincount(index, weights=area, minlength=count)


def _rgb(resolved):
    return np.array([[int(item["color"][i:i + 2], 16) for i in (1, 3, 5)] for item in resolved["classes"]], np.uint8)


def mesh_colors(xyz, faces, resolved, depth=None, vertical_factor=1.0):
    """(xyz, faces, rgb per vertex, depth per vertex) of a mesh coloured by ``resolved``.

    Elevation and depth are coloured at each vertex, so classes follow the surface smoothly.
    Slope and aspect belong to triangles: those meshes get their own vertices per triangle,
    so each triangle keeps its class colour exactly."""
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    attribute = resolved["attribute"]
    rgb = _rgb(resolved)
    if attribute in ("elevation", "depth"):
        if attribute == "depth" and depth is None:
            raise ValueError("A depth style needs the depth (design − existing) at each vertex.")
        values = xyz[:, 2] if attribute == "elevation" else np.asarray(depth, float)
        return xyz, faces, rgb[classify(values, resolved)], depth
    index = classify(triangle_values(xyz, faces, attribute, depth, vertical_factor), resolved)
    split = xyz[faces].reshape(-1, 3)
    return (split, np.arange(len(split)).reshape(-1, 3), np.repeat(rgb[index], 3, axis=0),
            None if depth is None else np.asarray(depth, float)[faces].reshape(-1))


def plan_svg(layers, width=760, height=None, legend=None, lines=(), labels=(), north=True, max_height=620):
    """An SVG plan: ``layers`` is a list of (regions, resolved), drawn in order. ``regions`` maps a class to
    merged polygons [(outer, [holes])] (``class_regions``) or to plain pieces [(n, 2)] (``band_pieces``).
    ``lines`` are (points (n, ≥2), colour, width); ``labels`` (x, y, text). The map fits ``width`` × ``max_height``."""
    import html

    def rings_of(polys):
        for item in polys:
            if isinstance(item, tuple):
                yield from [item[0], *item[1]]
            else:
                yield item

    rings = [ring for regions, _ in layers for polys in regions.values() for ring in rings_of(polys)]
    rings += [np.asarray(points)[:, :2] for points, _c, _w in lines]
    if not rings:
        return ""
    stack = np.vstack(rings)
    x0, y0 = stack.min(axis=0)
    x1, y1 = stack.max(axis=0)
    span = max(x1 - x0, y1 - y0, 1e-9)
    pad = 0.03 * span
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    scale = min(width / (x1 - x0), (height or max_height) / (y1 - y0))
    width = int(math.ceil((x1 - x0) * scale))
    height = int(math.ceil((y1 - y0) * scale))

    def path(ring):
        pts = " ".join(f"{(x - x0) * scale:.1f},{(y1 - y) * scale:.1f}" for x, y in ring[:, :2])
        return f"M{pts}Z"

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='100%' "
             f"style='max-width:{width}px;background:#fff;border:1px solid #ddd'>"]
    for regions, resolved in layers:
        for k, polys in regions.items():
            color = resolved["classes"][k]["color"]
            if polys and isinstance(polys[0], tuple):
                d = " ".join(path(ring) for ring in rings_of(polys))
                parts.append(f"<path d='{d}' fill='{color}' fill-rule='evenodd' stroke='none'/>")
            else:                  # pieces: outlined in their own colour so no seams show between them
                d = " ".join(path(ring) for ring in polys)
                parts.append(f"<path d='{d}' fill='{color}' stroke='{color}' stroke-width='0.4' stroke-linejoin='round'/>")
    for points, color, line_width in lines:
        pts = " ".join(f"{(x - x0) * scale:.1f},{(y1 - y) * scale:.1f}" for x, y in np.asarray(points)[:, :2])
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='{line_width}'/>")
    for x, y, text in labels:
        parts.append(f"<text x='{(x - x0) * scale:.1f}' y='{(y1 - y) * scale:.1f}' font-size='11' text-anchor='middle' "
                     f"paint-order='stroke' stroke='white' stroke-width='3'>{html.escape(str(text))}</text>")
    # scale bar
    bar = 10 ** math.floor(math.log10(span / 4))
    for step in (5, 2, 1):
        if bar * step <= span / 3:
            bar *= step
            break
    parts.append(f"<g font-size='10'><rect x='12' y='{height - 18}' width='{bar * scale:.1f}' height='4' fill='#333'/>"
                 f"<text x='12' y='{height - 22}'>{bar:g}</text></g>")
    if north:
        parts.append(f"<g transform='translate({width - 24},28)'><path d='M0,-14 L6,6 L0,2 L-6,6Z' fill='#333'/>"
                     "<text y='18' font-size='10' text-anchor='middle'>N</text></g>")
    parts.append("</svg>")
    if legend:
        items = "".join(f"<span style='display:inline-block;margin:2px 10px 2px 0'><span style='display:inline-block;"
                        f"width:12px;height:12px;background:{item['color']};border:1px solid #999;vertical-align:middle'>"
                        f"</span> {html.escape(item['label'])}</span>" for item in legend["classes"])
        parts.append(f"<div style='font-size:12px;margin:4px 0 12px'>{items}</div>")
    return "".join(parts)
