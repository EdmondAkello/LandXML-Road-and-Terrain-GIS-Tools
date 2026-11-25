"""Parametric road assemblies: elements placed outward from a baseline (pure Python + NumPy, no QGIS).

An *assembly* holds an optional ``median`` and the element lists of the ``left`` and ``right`` sides (or
``"left": "mirror"``). Each element is a plain dict ``{"type": ..., "id": ..., <parameters>}``; every parameter is
user-editable and has a default in :data:`SCHEMA`. Standards only supply defaults and advisory checks
(:mod:`landxml.section_standards`); nothing here clamps a value to a standard.

:func:`build_section` places the elements at one station and returns the finished (top) surface, the datum
(bottom of the pavement structure), closed shapes for every pavement course, kerb and lining, coded points
for feature lines, and an explanation of every point ("why this point").

Distances are in the project's linear unit; crossfalls and grades in percent (positive = falls away from the
baseline, outward); batter slopes as horizontal per 1 vertical (2 = 1V:2H). Offsets are negative to the left.
"""

from __future__ import annotations

import copy
import math

import numpy as np

# ----------------------------------------------------------------------------------------------------------------
# Element schema: (parameter, default, kind, label). kind: length, percent, ratio, bool, text, buildup, or a list
# of choices. The dock builds its property editors from this table; the JSON project stores the same keys.
# ----------------------------------------------------------------------------------------------------------------
SCHEMA = {
    "lane": [("name", "", "text", "Name"), ("width", 3.5, "length", "Width"),
             ("crossfall", 2.5, "percent", "Crossfall (normal, falls outward)"),
             ("kind", "through", ["through", "auxiliary", "climbing", "turning", "bus", "parking", "cycle"], "Lane type"),
             ("surface", "paved", ["paved", "gravel", "earth"], "Surface"),
             ("buildup", "Carriageway", "buildup", "Build-up"),
             ("superelevate", True, "bool", "Rotates with superelevation"),
             ("widen", True, "bool", "Takes curve widening")],
    "shoulder": [("name", "", "text", "Name"), ("width", 1.5, "length", "Width"),
                 ("crossfall", 4.0, "percent", "Crossfall (falls outward)"),
                 ("paved", True, "bool", "Paved"), ("buildup", "Carriageway", "buildup", "Build-up"),
                 ("rule", "project", ["project", "own", "follow_low", "breakover", "follow"], "On superelevation"),
                 ("daylight_courses", True, "bool", "Courses run out to the side slope")],
    "kerb": [("name", "", "text", "Name"),
             ("kind", "barrier", ["barrier", "semi_mountable", "mountable", "flush"], "Kerb type"),
             ("height", 0.15, "length", "Height above channel"), ("face", 0.025, "length", "Face width (batter)"),
             ("top", 0.15, "length", "Top width"), ("unit_depth", 0.30, "length", "Kerb unit depth"),
             ("channel", 0.0, "length", "Channel width (0 = none)"),
             ("channel_fall", 2.5, "percent", "Channel crossfall"),
             ("channel_thickness", 0.15, "length", "Channel thickness")],
    "sidewalk": [("name", "", "text", "Name"), ("width", 2.0, "length", "Width"),
                 ("crossfall", 2.0, "percent", "Crossfall towards the road"),
                 ("buildup", "Footway", "buildup", "Build-up")],
    "cycle_track": [("name", "", "text", "Name"), ("width", 1.5, "length", "Width"),
                    ("crossfall", 2.5, "percent", "Crossfall towards the road"),
                    ("buildup", "Footway", "buildup", "Build-up")],
    "verge": [("name", "", "text", "Name"), ("width", 1.0, "length", "Width"),
              ("slope", 4.0, "percent", "Crossfall (falls outward)"), ("topsoil", True, "bool", "Grassed / topsoil")],
    "side_drain": [("name", "", "text", "Name"), ("drain_type", "", "drain", "Standard drain type"),
                   ("shape", "v", ["v", "trapezoidal"], "Shape"), ("foreslope", 2.0, "ratio", "Foreslope (H per 1 V)"),
                   ("depth", 0.4, "length", "Depth"), ("depth_from", "edge", ["edge", "datum"], "Depth measured from"),
                   ("bottom", 0.0, "length", "Bottom width"), ("backslope", 0.0, "ratio", "Back slope (0 = cut slope)"),
                   ("where", "cut", ["cut", "always"], "Where"),
                   ("lining", "none", ["none", "concrete", "masonry", "grass"], "Lining"),
                   ("lining_thickness", 0.1, "length", "Lining thickness")],
    "daylight": [("name", "", "text", "Name"), ("material", "earth", "material", "Material (standard bands)"),
                 ("cut_bands", [], "bands", "Cut slope bands [height, H per 1 V]"),
                 ("fill_bands", [], "bands", "Fill slope bands [height, H per 1 V]"),
                 ("bench_height", 0.0, "length", "Bench every (height, 0 = none)"),
                 ("bench_width", 2.0, "length", "Bench width"), ("bench_fall", 4.0, "percent", "Bench crossfall (falls outward)"),
                 ("benches_in", "both", ["both", "cut", "fill"], "Benches in"),
                 ("max_width", 60.0, "length", "Maximum width"),
                 ("fill_widening", False, "bool", "Fill widening from the standard"),
                 ("toe_drain_depth", 0.0, "length", "Toe drain depth in fill (0 = none)"),
                 ("cutoff_drain", False, "bool", "Cut-off ditch beyond the top of cut"),
                 ("cutoff_offset", 0.0, "length", "Cut-off ditch offset (0 = standard)"),
                 ("cutoff_depth", 0.6, "length", "Cut-off ditch depth"),
                 ("wall_cut", 0.0, "length", "Retaining wall in cut, up to (height, 0 = none)"),
                 ("wall_fill", 0.0, "length", "Retaining wall in fill, up to (height, 0 = none)"),
                 ("wall_batter", 0.1, "ratio", "Wall face batter (H per 1 V)"),
                 ("wall_thickness", 0.4, "length", "Wall thickness (for quantities)")],
    "median": [("name", "", "text", "Name"), ("kind", "raised", ["flush", "raised", "depressed"], "Median type"),
               ("width", 3.0, "length", "Width (edge to edge)"), ("kerb_height", 0.15, "length", "Kerb height (raised)"),
               ("top_fall", 2.0, "percent", "Top crossfall (raised, falls outward)"),
               ("slope", 16.7, "percent", "Side slope (depressed)"), ("buildup", "Median", "buildup", "Build-up")],
    "barrier": [("name", "", "text", "Name"), ("kind", "w_beam", ["w_beam", "concrete", "wire_rope"], "Barrier type"),
                ("offset", 0.6, "length", "Offset from the previous point"), ("height", 0.8, "length", "Height"),
                ("base_width", 0.6, "length", "Base width (concrete)")],
    "custom": [("name", "", "text", "Name"), ("steps", [[1.0, 2.0, None, "C1"]], "steps", "Steps [width, fall %, rise, code]"),
               ("buildup", "", "buildup", "Build-up")],
}
LABELS = {"lane": "Lane", "shoulder": "Shoulder", "kerb": "Kerb", "sidewalk": "Sidewalk / footway",
          "cycle_track": "Cycle track", "verge": "Verge", "side_drain": "Side drain", "daylight": "Daylight",
          "median": "Median", "barrier": "Barrier", "custom": "Custom"}
CODES = {"lane": "LANE", "shoulder": "EPS", "kerb": "KERB", "sidewalk": "SW", "cycle_track": "CY", "verge": "VERGE",
         "custom": "C"}
TERMINAL = {"daylight"}
SIDES = ("left", "right")


def defaults(element_type):
    return {key: copy.deepcopy(value) for key, value, _kind, _label in SCHEMA[element_type]}


def element(element_type, **params):
    """A complete element dict: the defaults of ``element_type`` updated with ``params``."""
    if element_type not in SCHEMA:
        raise ValueError(f"Unknown element type '{element_type}'.")
    out = {"type": element_type}
    out.update(defaults(element_type))
    out.update(params)
    return out


def complete(item):
    base = element(item["type"])
    base.update(item)
    return base


def assign_ids(assembly):
    """Give every element of a raw assembly a stable id (its type and occurrence on its side), in place."""
    for side in SIDES:
        items = assembly.get(side)
        if not isinstance(items, list):
            continue
        taken = {e.get("id") for e in items if e.get("id")}
        seen = {}
        for e in items:
            seen[e["type"]] = seen.get(e["type"], 0) + 1
            if not e.get("id"):
                k = seen[e["type"]]
                while f"{e['type']}{k}" in taken:
                    k += 1
                e["id"] = f"{e['type']}{k}"
                taken.add(e["id"])
    if assembly.get("median") and not assembly["median"].get("id"):
        assembly["median"]["id"] = "median"
    return assembly


def normalise_assembly(assembly, standard=None, buildups=None):
    """Fill defaults, give every element a stable id, apply standard drain types, expand ``"left": "mirror"``."""
    out = copy.deepcopy(assembly)

    def fix(items, side):
        result = []
        seen = {}
        for item in items:
            item = complete(item)
            seen[item["type"]] = seen.get(item["type"], 0) + 1
            if not item.get("id"):
                item["id"] = f"{item['type']}{seen[item['type']]}"
            if item["type"] == "side_drain" and item.get("drain_type") and standard is not None:
                spec = standard.side_drain(item["drain_type"]) or {}
                for key in ("shape", "foreslope", "depth", "bottom", "backslope"):
                    if key in spec and not item.get(f"_set_{key}"):
                        item[key] = spec[key]
            result.append(item)
        return result

    right = fix(out.get("right", []), "right")
    if out.get("left") in (None, "mirror"):
        left = copy.deepcopy(right)
        for item in left:
            item["id"] = item["id"]                  # same ids on both sides: transitions match per side
    else:
        left = fix(out["left"], "left")
    out["left"], out["right"] = left, right
    if out.get("median"):
        out["median"] = complete(out["median"])
        out["median"].setdefault("id", "median")
    return out


# ----------------------------------------------------------------------------------------------------------------
# Section state at one station
# ----------------------------------------------------------------------------------------------------------------
class SideState:
    """What drives one side at a station: the lane fall (superelevation), widening and their sources."""

    def __init__(self, lane_fall=None, widening=0.0, fall_source="", widening_source="", super_active=False):
        self.lane_fall = lane_fall              # None = each lane keeps its own crossfall
        self.widening = widening
        self.fall_source = fall_source
        self.widening_source = widening_source
        self.super_active = super_active


class Point:
    __slots__ = ("d", "z", "code", "element", "why", "kind", "eid")

    def __init__(self, d, z, code="", element="", why="", kind="top", eid=""):
        self.d, self.z, self.code, self.element, self.why, self.kind = float(d), float(z), code, element, why, kind
        self.eid = eid


def _fmt(value, unit=""):
    return f"{value:.3f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")


def _shoulder_fall(item, state, rule, breakover_max):
    own = float(item["crossfall"])
    lane = state.lane_fall
    if rule == "project":
        rule = "own"
    if lane is None or not state.super_active:
        return own, f"crossfall {own:g} % (element)"
    if rule == "follow":
        return lane, f"follows the lane at {lane:g} % (superelevation)"
    if rule == "own":
        return own, f"keeps its own {own:g} % on superelevation"
    if lane >= own:                                     # low side: never flatter than the lane
        return lane, f"low side follows the lane at {lane:g} %"
    if rule == "breakover" and own - lane > breakover_max:
        fall = lane + breakover_max
        return fall, f"high side limited to {breakover_max:g} % breakover from the lane ({lane:g} %)"
    return own, f"keeps {own:g} % on the high side"


def place_side(items, state, start_d, start_z, options):
    """Place the non-daylight elements of one side outward from (start_d, start_z) relative to grade.

    Returns (points, info) where points are :class:`Point` in outward distance, ``info`` keeps per-element
    spans for courses, the deferred drain and daylight elements and barriers."""
    points = [Point(start_d, start_z, "", "", "baseline")]
    mark = 1
    occurrence = {}
    spans = []                    # (item, d0, d1, top line points list) for courses
    drain = daylight = None
    barriers, kerbs = [], []
    lanes = [i for i, item in enumerate(items) if item["type"] == "lane" and item.get("widen", True)]
    widen_index = lanes[-1] if lanes else None
    last_lane = max((i for i, item in enumerate(items) if item["type"] == "lane"), default=None)
    unit = options.get("unit", "m")
    for index, item in enumerate(items):
        if index:
            for p in points[mark:]:
                p.eid = p.eid or items[index - 1].get("id", "")
        mark = len(points)
        kind = item["type"]
        occurrence[kind] = occurrence.get(kind, 0) + 1
        label = item.get("name") or f"{LABELS[kind]} {occurrence[kind]}"
        d0, z0 = points[-1].d, points[-1].z
        if kind == "lane":
            width = float(item["width"])
            why = f"{label}: width {_fmt(width, unit)}"
            if index == widen_index and state.widening:
                width += state.widening
                why += f" + {_fmt(state.widening, unit)} curve widening ({state.widening_source})"
            fall = float(item["crossfall"])
            why_fall = f"crossfall {fall:g} % (element)"
            if item.get("superelevate", True) and state.lane_fall is not None and state.super_active:
                fall = state.lane_fall
                why_fall = f"crossfall {fall:g} % ({state.fall_source})"
            code = "ETW" if index == last_lane else "LANE"
            top = [Point(d0 + width, z0 - width * fall / 100.0, code, label, f"{why}; {why_fall}")]
            spans.append((item, label, [points[-1]] + top))
            points.extend(top)
        elif kind == "shoulder":
            width = float(item["width"])
            rule = item.get("rule", "project")
            if rule == "project":
                rule = options.get("shoulder_rule", "own")
            fall, why_fall = _shoulder_fall(item, state, rule, options.get("breakover_max", 8.0))
            code = "EPS" if item.get("paved", True) else "ESH"
            top = [Point(d0 + width, z0 - width * fall / 100.0, code, label,
                         f"{label}: width {_fmt(width, unit)}; {why_fall}")]
            spans.append((item, label, [points[-1]] + top))
            points.extend(top)
        elif kind == "kerb":
            channel, height = float(item["channel"]), float(item["height"])
            face, top_w = float(item["face"]), float(item["top"])
            if item["kind"] == "flush":
                height = 0.0
            local = []
            if channel > 0:
                zc = z0 - channel * float(item["channel_fall"]) / 100.0
                local.append(Point(d0 + channel, zc, "FL", label,
                                   f"{label}: channel {_fmt(channel, unit)} at {float(item['channel_fall']):g} % (flowline)"))
            base = local[-1] if local else points[-1]
            if not local and not base.code:
                base.code, base.element, base.why = "FL", label, f"{label}: kerb face bottom (flowline)"
            kt = Point(base.d + face, base.z + height, "KERB_T", label,
                       f"{label}: {item['kind'].replace('_', ' ')} kerb, height {_fmt(height, unit)} over a "
                       f"{_fmt(face, unit)} face")
            kb = Point(kt.d + top_w, kt.z, "KERB_BK", label, f"{label}: top width {_fmt(top_w, unit)} (back of kerb)")
            local.extend([kt, kb])
            kerbs.append((item, label, base, kt, kb, channel, local[0] if channel > 0 else None, points[-1]))
            points.extend(local)
        elif kind in ("sidewalk", "cycle_track"):
            width, fall = float(item["width"]), -float(item["crossfall"])
            code = CODES[kind]
            top = [Point(d0 + width, z0 - width * fall / 100.0, f"{code}_B", label,
                         f"{label}: width {_fmt(width, unit)}, crossfall {float(item['crossfall']):g} % towards the road")]
            spans.append((item, label, [points[-1]] + top))
            points.extend(top)
        elif kind == "verge":
            width, fall = float(item["width"]), float(item["slope"])
            points.append(Point(d0 + width, z0 - width * fall / 100.0, "VERGE", label,
                                f"{label}: width {_fmt(width, unit)}, falls {fall:g} % outward"))
            if item.get("topsoil", True):
                spans.append(({"type": "verge", "buildup": "", "_topsoil": True}, label, points[-2:]))
        elif kind == "custom":
            local = []
            for step in item.get("steps") or []:
                width = float(step[0] or 0.0)
                fall = step[1] if len(step) > 1 else None
                rise = step[2] if len(step) > 2 else None
                code = (step[3] if len(step) > 3 else "") or "C"
                prev = (local or points)[-1]
                dz = float(rise) if rise not in (None, "") else -width * float(fall or 0.0) / 100.0
                local.append(Point(prev.d + width, prev.z + dz, code, label,
                                   f"{label}: step {_fmt(width, unit)} " + (f"rise {_fmt(dz, unit)}" if rise not in (None, "")
                                                                         else f"at {float(fall or 0):g} %")))
            if local:
                spans.append((item, label, [points[-1]] + local))
                points.extend(local)
        elif kind == "side_drain":
            drain = (item, label)
        elif kind == "daylight":
            daylight = (item, label)
            break
        elif kind == "barrier":
            barriers.append((item, label, len(points) - 1))
    else:
        if items:
            for p in points[mark:]:
                p.eid = p.eid or items[-1].get("id", "")
    return points, {"spans": spans, "drain": drain, "daylight": daylight, "barriers": barriers, "kerbs": kerbs}


# ----------------------------------------------------------------------------------------------------------------
# Daylight: side slopes to the ground (1-D, on the ground profile across the section)
# ----------------------------------------------------------------------------------------------------------------
def _ground_at(gd, gz, d):
    if gd is None or not len(gd) or d < gd[0] or d > gd[-1]:
        return float("nan")
    return float(np.interp(d, gd, gz))


def _first_hit(path, gd, gz, cut):
    """First crossing of the polyline ``path`` [(d, z)] with the ground; returns (d, z, segment index) or None.

    In ``cut`` the path rises to meet ground above it; in fill it falls to ground below it."""
    for k, ((d0, z0), (d1, z1)) in enumerate(zip(path, path[1:])):
        if d1 <= d0 + 1e-12:
            continue
        inside = (gd > d0) & (gd < d1)
        ds = np.concatenate([[d0], gd[inside], [d1]])
        gs = np.interp(ds, gd, gz, left=np.nan, right=np.nan)
        ps = z0 + (z1 - z0) * (ds - d0) / (d1 - d0)
        diff = ps - gs if not cut else gs - ps          # > 0 while the path is still short of the ground
        ok = np.isfinite(diff)
        if not ok.any():
            continue
        for j in range(len(ds) - 1):
            a, b = diff[j], diff[j + 1]
            if not (math.isfinite(a) and math.isfinite(b)):
                continue
            if a > 0 >= b or (a == 0 and b < 0):              # touching the ground and then going past it counts
                t = a / (a - b) if a != b else 0.0
                d = ds[j] + t * (ds[j + 1] - ds[j])
                return d, z0 + (z1 - z0) * (d - d0) / (d1 - d0), k
    return None


def _batter_path(d0, z0, ratio, direction, max_width, bench_height, bench_width, bench_fall):
    """A batter from (d0, z0) rising (direction +1) or falling (-1) at 1V:``ratio``H, benched every
    ``bench_height`` (0 = none), out to ``max_width``. Returns [(d, z, code)]."""
    out = [(d0, z0, "HINGE")]
    d, z = d0, z0
    ratio = max(float(ratio), 1e-6)
    if bench_height and bench_height > 0:
        while d - d0 < max_width:
            d += bench_height * ratio
            z += direction * bench_height
            out.append((d, z, "BENCH_T" if direction > 0 else "BENCH_B"))
            d += bench_width
            z -= bench_width * bench_fall / 100.0
            out.append((d, z, "BENCH_E"))
    out.append((d0 + max_width + 1e-6 if len(out) == 1 else max(d, d0 + max_width), None, "END"))
    if out[-1][1] is None:
        last_d, last_z = out[-2][0], out[-2][1]
        out[-1] = (out[-1][0], last_z + direction * (out[-1][0] - last_d) / ratio, "END")
    return out


def _bands(item, standard, cut):
    key = "cut_bands" if cut else "fill_bands"
    if item.get(key):
        return [list(b) for b in item[key]], "user bands"
    if standard is not None:
        bands = standard.slope_bands(item.get("material") or "earth").get("cut" if cut else "fill")
        if bands:
            ref = (standard.data.get("slope_bands") or {}).get("ref", "")
            return bands, f"{standard.name} {ref}".strip()
    return ([[None, 1.5]] if cut else [[None, 2.0]]), "default"


def daylight_side(hinge, datum_hinge, drain, day, ground, standard, options):
    """Side slopes from ``hinge`` (Point, absolute level) to the ground.

    Returns (points, info): points after the hinge with codes, info with type (cut/fill/none), height,
    slope used and its source, whether the ground was reached and the daylight offset."""
    item, label = day if day else (element("daylight"), "Daylight")
    gd, gz = ground if ground is not None else (np.array([]), np.array([]))
    unit = options.get("unit", "m")
    max_width = float(item.get("max_width") or 60.0)
    bench_h, bench_w = float(item.get("bench_height") or 0.0), float(item.get("bench_width") or 0.0)
    bench_fall = float(item.get("bench_fall") or 0.0)
    benches_in = item.get("benches_in", "both")
    g0 = _ground_at(gd, gz, hinge.d)
    info = {"type": "none", "reached": False, "height": 0.0, "slope": None, "source": "", "daylight": hinge.d,
            "label": label}
    if not math.isfinite(g0):
        # no ground under the hinge: run a fill slope to the maximum width and report it
        cut = False
    else:
        cut = g0 > hinge.z + 1e-6
    out = []
    start = hinge
    if cut and drain is not None and drain[0].get("where", "cut") in ("cut", "always"):
        ditem, dlabel = drain
        depth = float(ditem["depth"])
        base_z = datum_hinge if ditem.get("depth_from") == "datum" and datum_hinge is not None else hinge.z
        z_inv = base_z - depth
        run = max(hinge.z - z_inv, 0.0) * float(ditem["foreslope"])
        why = (f"{dlabel}: {ditem.get('drain_type') or ditem['shape']} drain, depth {_fmt(depth, unit)} below the "
               f"{'datum' if ditem.get('depth_from') == 'datum' else 'edge'}, foreslope 1:{float(ditem['foreslope']):g}")
        inv = Point(hinge.d + run, z_inv, "DITCH_I", dlabel, why, "drain")
        out.append(inv)
        if float(ditem.get("bottom") or 0.0) > 0:
            out[-1].code = "DITCH_IF"
            out.append(Point(inv.d + float(ditem["bottom"]), z_inv, "DITCH_IB", dlabel,
                             f"{dlabel}: bottom width {_fmt(float(ditem['bottom']), unit)}", "drain"))
        start = out[-1]
        info["drain"] = {"label": dlabel, "invert": z_inv, "front": hinge.d, "back": out[-1].d,
                         "lining": ditem.get("lining", "none"), "thickness": float(ditem.get("lining_thickness") or 0.0),
                         "item": ditem}
        if float(ditem.get("backslope") or 0.0) > 0:
            bands, source = [[None, float(ditem["backslope"])]], f"{dlabel} back slope"
        else:
            bands, source = _bands(item, standard, True)
    else:
        bands, source = _bands(item, standard, cut)
    use_benches = bench_h > 0 and (benches_in == "both" or benches_in == ("cut" if cut else "fill"))
    direction = 1 if cut else -1
    if not cut and item.get("fill_widening") and standard is not None and math.isfinite(g0):
        probe = _batter_path(start.d, start.z, bands[0][1], -1, max_width, 0, 0, 0)
        hit = _first_hit([(p[0], p[1]) for p in probe], gd, gz, False)
        height = start.z - hit[1] if hit else 0.0
        widen = standard.fill_widening(height)
        if widen > 0:
            ref = (standard.data.get("widening") or {}).get("fill_ref", "")
            start = Point(start.d + widen, start.z, "FILL_W", label,
                          f"{label}: fill widening {_fmt(widen, unit)} for a {_fmt(height, unit)} fill ({ref})")
            out.append(start)
    wall_h = float(item.get("wall_cut" if cut else "wall_fill") or 0.0)
    if wall_h > 0 and math.isfinite(g0):
        batter = float(item.get("wall_batter") or 0.0)
        top = Point(start.d + wall_h * batter, start.z + direction * wall_h, "WALL_T", label,
                    f"{label}: retaining wall up to {_fmt(wall_h, unit)} in {'cut' if cut else 'fill'}, face 1:{batter:g}")
        hit = _first_hit([(start.d, start.z), (top.d, top.z)], gd, gz, cut)
        info["wall"] = {"base": (start.d, start.z), "thickness": float(item.get("wall_thickness") or 0.4), "cut": cut}
        start.code = start.code or "WALL_B"
        if hit is not None:
            out.append(Point(hit[0], hit[1], "DAYLIGHT", label,
                             f"{label}: retaining wall {_fmt(abs(hit[1] - start.z), unit)} high meets the ground"))
            info.update(type="cut" if cut else "fill", reached=True, height=abs(hit[1] - start.z), slope=batter,
                        source="retaining wall", daylight=hit[0])
            info["wall"]["top"] = (hit[0], hit[1])
            return out, info
        out.append(top)
        info["wall"]["top"] = (top.d, top.z)
        start = top
    chosen = None
    for k, (limit, ratio) in enumerate(bands):
        path = _batter_path(start.d, start.z, ratio, direction, max_width,
                            bench_h if use_benches else 0.0, bench_w, bench_fall)
        hit = _first_hit([(p[0], p[1]) for p in path], gd, gz, cut) if math.isfinite(g0) else None
        height = abs(hit[1] - start.z) if hit else None
        chosen = (limit, ratio, path, hit, height)
        if hit is not None and (limit is None or height <= float(limit) + 1e-9):
            break
    limit, ratio, path, hit, height = chosen
    kind = "cut" if cut else "fill"
    band_text = (f"1:{float(ratio):g} for {'heights up to ' + _fmt(float(limit), unit) if limit is not None else 'any height'}"
                 f" ({source})")
    if hit is not None:
        d_hit, z_hit, seg = hit
        for p in path[1:seg + 1]:
            out.append(Point(p[0], p[1], p[2], label, f"{label}: bench on the {kind} batter every {_fmt(bench_h, unit)}"))
        out.append(Point(d_hit, z_hit, "DAYLIGHT", label,
                         f"{label}: {kind} slope {band_text}; height {_fmt(height, unit)}; meets the ground"))
        info.update(type=kind, reached=True, height=height, slope=float(ratio), source=band_text, daylight=d_hit)
    else:
        for p in path[1:-1]:
            out.append(Point(p[0], p[1], p[2], label, f"{label}: bench"))
        end = path[-1]
        out.append(Point(end[0], end[1], "DAYLIGHT", label,
                         f"{label}: {kind} slope {band_text}; ground not reached within {_fmt(max_width, unit)} "
                         "(maximum width) - check the ground or widen the limit"))
        info.update(type=kind, reached=False, height=abs(end[1] - start.z), slope=float(ratio), source=band_text,
                    daylight=end[0])
    if not cut and hit is not None and float(item.get("toe_drain_depth") or 0.0) > 0:
        depth = float(item["toe_drain_depth"])
        toe = out[-1]
        toe.code = "TOE"
        out.append(Point(toe.d + depth * 2.0, toe.z - depth, "TOE_DRAIN", label,
                         f"{label}: toe drain {_fmt(depth, unit)} deep, 1:2 sides"))
        gz_end = _ground_at(gd, gz, toe.d + depth * 4.0)
        out.append(Point(toe.d + depth * 4.0, gz_end if math.isfinite(gz_end) else toe.z, "DAYLIGHT", label,
                         f"{label}: toe drain back to the ground"))
        info["daylight"] = out[-1].d
    if cut and hit is not None and item.get("cutoff_drain"):
        offset = float(item.get("cutoff_offset") or 0.0)
        source_off = "user offset"
        if offset <= 0 and standard is not None:
            offset = standard.cutoff_offset(height) or 5.0
            source_off = f"{standard.name} {(standard.data.get('cutoff_offset') or {}).get('ref', '')}"
        depth = float(item.get("cutoff_depth") or 0.6)
        top = out[-1]
        top.code = "TOP_CUT"
        a = top.d + offset
        za = _ground_at(gd, gz, a)
        if math.isfinite(za):
            out.append(Point(a, za, "CUTOFF_F", label, f"{label}: cut-off ditch {_fmt(offset, unit)} beyond the top of cut ({source_off})"))
            out.append(Point(a + 2 * depth, za - depth, "CUTOFF_I", label, f"{label}: cut-off ditch {_fmt(depth, unit)} deep"))
            zb = _ground_at(gd, gz, a + 4 * depth)
            out.append(Point(a + 4 * depth, zb if math.isfinite(zb) else za, "DAYLIGHT", label, f"{label}: cut-off ditch back"))
            info["daylight"] = out[-1].d
    return out, info


# ----------------------------------------------------------------------------------------------------------------
# Courses, kerbs and linings as closed shapes
# ----------------------------------------------------------------------------------------------------------------
def _line_at(line, d):
    ds = [p[0] for p in line]
    zs = [p[1] for p in line]
    return float(np.interp(d, ds, zs))


def _ray_hit_polyline(origin, fall, polyline, limit=20.0):
    """Where a ray from ``origin`` (d, z) outward at ``fall`` % meets ``polyline`` [(d, z)] (first crossing)."""
    d0, z0 = origin
    best = None
    for (a_d, a_z), (b_d, b_z) in zip(polyline, polyline[1:]):
        if b_d <= d0 - 1e-9 or abs(b_d - a_d) < 1e-12:
            continue
        # ray: z = z0 - fall/100 (d - d0); segment: z = a_z + s (d - a_d)
        s = (b_z - a_z) / (b_d - a_d)
        r = -fall / 100.0
        if abs(s - r) < 1e-12:
            continue
        d = (a_z - s * a_d - z0 + r * d0) / (r - s)
        if a_d - 1e-9 <= d <= b_d + 1e-9 and d >= d0 - 1e-9 and d - d0 <= limit:
            best = (d, z0 + r * (d - d0))
            break
    return best


def course_shapes(spans, buildups, outer, options):
    """Closed course polygons under each element with a build-up, plus the datum line.

    ``spans``: [(item, label, top points)] in outward order; ``outer``: [(d, z)] of the surface beyond the last
    span (hinge first) for courses that daylight. Returns (shapes, datum, depth at the edge)."""
    shapes, datum = [], []
    last_paved = max((i for i, (item, _l, _p) in enumerate(spans) if item.get("buildup") and item["buildup"] in buildups),
                     default=None)
    for index, (item, label, pts) in enumerate(spans):
        top = [(p.d, p.z) for p in pts]
        name = item.get("buildup") or ""
        courses = (buildups.get(name) or {}).get("courses", []) if name else []
        if item.get("_topsoil"):
            datum.extend(top if not datum else top[1:])
            continue
        if not courses:
            datum.extend(top if not datum else top[1:])
            continue
        depth = 0.0
        daylighting = (index == last_paved and item.get("daylight_courses", True) and outer and len(outer) > 1
                       and options.get("course_daylight", True))
        fall = (top[0][1] - top[-1][1]) / max(top[-1][0] - top[0][0], 1e-9) * 100.0
        upper, tail, last_hit = list(top), [], top[-1]
        for course in courses:
            t = float(course.get("thickness") or 0.0)
            if t <= 0:
                continue
            depth += t
            lower = [(d, z - depth) for d, z in top]
            hit = _ray_hit_polyline(lower[-1], fall, outer) if daylighting else None
            if hit is not None and hit[0] > last_hit[0] + 1e-9:
                between = [p for p in outer if last_hit[0] + 1e-9 < p[0] < hit[0] - 1e-9]
                ring = upper + tail + between + [hit] + list(reversed(lower))
                tail, last_hit = [hit], hit
            else:
                daylighting = False
                ring = upper + list(reversed(lower))
                tail = []
            shapes.append({"name": f"{course.get('name', 'Course')}", "buildup": name, "element": label,
                           "type": item["type"], "material": course.get("material", ""), "unit": course.get("unit", ""),
                           "thickness": t, "ring": ring, "area": _shoelace(ring),
                           "top_width": abs(top[-1][0] - top[0][0])})
            upper = lower
        bottom = [(d, z - depth) for d, z in top] + tail
        datum.extend(bottom)
    return shapes, datum


def _shoelace(ring):
    if len(ring) < 3:
        return 0.0
    x = np.array([p[0] for p in ring])
    y = np.array([p[1] for p in ring])
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


# ----------------------------------------------------------------------------------------------------------------
# One full section
# ----------------------------------------------------------------------------------------------------------------
def _median_profile(median, z_left_edge, z_right_edge, half, unit):
    """Median points from the left edge to the right edge (signed offsets), relative levels."""
    kind = median.get("kind", "raised")
    label = median.get("name") or "Median"
    if kind == "flush":
        return [Point(-half, z_left_edge, "MED_E", label, f"{label}: flush median edge"),
                Point(half, z_right_edge, "MED_E", label, f"{label}: flush median, width {_fmt(2 * half, unit)}")]
    if kind == "depressed":
        s = float(median.get("slope") or 16.7) / 100.0
        zc = min(z_left_edge, z_right_edge) - half * s
        return [Point(-half, z_left_edge, "MED_E", label, f"{label}: depressed median edge"),
                Point(0.0, zc, "MED_C", label, f"{label}: depressed median, side slope {s * 100:g} %"),
                Point(half, z_right_edge, "MED_E", label, f"{label}: depressed median edge")]
    h = float(median.get("kerb_height") or 0.15)
    top_fall = float(median.get("top_fall") or 0.0) / 100.0
    kw = min(0.15, half / 3)
    zt_l, zt_r = z_left_edge + h, z_right_edge + h
    zc = max(zt_l + (half - kw) * top_fall, zt_r + (half - kw) * top_fall)
    return [Point(-half, z_left_edge, "MED_FL", label, f"{label}: median kerb flowline"),
            Point(-half + 0.025, zt_l, "MED_KT", label, f"{label}: raised median kerb {_fmt(h, unit)}"),
            Point(0.0, zc, "MED_C", label, f"{label}: raised median top, {top_fall * 100:g} % fall to the kerbs"),
            Point(half - 0.025, zt_r, "MED_KT", label, f"{label}: raised median kerb {_fmt(h, unit)}"),
            Point(half, z_right_edge, "MED_FL", label, f"{label}: median kerb flowline")]


def build_section(assembly, grade, states, ground, standard=None, buildups=None, options=None):
    """The section at one station.

    ``assembly``: normalised (see :func:`normalise_assembly`); ``grade``: profile level at the baseline;
    ``states``: {"left": SideState, "right": SideState}; ``ground``: (offsets, levels) across the section
    (signed offsets, increasing) or None. Returns a dict with ``top`` [Point] left to right (signed offsets,
    absolute levels), ``datum`` [(offset, level)], ``shapes`` (rings relative to grade), ``sides`` info,
    ``checks`` and ``warnings``."""
    options = dict(options or {})
    buildups = buildups or {}
    median = assembly.get("median")
    half = float(median["width"]) / 2.0 if median else 0.0
    sides = {}
    for side in SIDES:
        items = assembly.get(side) or []
        pts, info = place_side(items, states[side], half, 0.0, options)
        sides[side] = (pts, info)
    # rotation about an edge of the travelled way: keep the axis at its normal-crown level
    shift = 0.0
    axis = options.get("rotation", "centreline")
    if axis in ("inside_edge", "outside_edge") and not median:
        falls = {s: states[s].lane_fall for s in SIDES}
        if any(states[s].super_active for s in SIDES) and all(falls[s] is not None for s in SIDES):
            inside = "right" if falls["right"] >= falls["left"] else "left"
            target = inside if axis == "inside_edge" else ("left" if inside == "right" else "right")
            pts = sides[target][0]
            etw = next((p for p in pts if p.code == "ETW"), None)
            if etw is not None:
                nc = options.get("normal_crown", 2.5)
                shift = -etw.d * nc / 100.0 - etw.z
    # absolute levels
    result_sides = {}
    shapes_all, datum_sides, warnings = [], {}, []
    for side in SIDES:
        sign = -1.0 if side == "left" else 1.0
        pts, info = sides[side]
        for p in pts:
            p.z += grade + shift
        gd = gz = None
        if ground is not None:
            g_off, g_z = ground
            mask = (g_off * sign) >= -1e-9
            gd = (g_off[mask] * sign)
            gz = g_z[mask]
            order = np.argsort(gd)
            gd, gz = gd[order], gz[order]
        spans = info["spans"]
        hinge = pts[-1]
        # datum level at the hinge: bottom of the edge element's courses (for drains measured from the datum)
        datum_hinge = None
        for item, _label, top in reversed(spans):
            courses = (buildups.get(item.get("buildup") or "") or {}).get("courses", [])
            if courses:
                datum_hinge = top[-1].z - sum(float(c.get("thickness") or 0) for c in courses)
                break
        outer, dinfo = daylight_side(Point(hinge.d, hinge.z, hinge.code, hinge.element, hinge.why), datum_hinge,
                                     info["drain"], info["daylight"], (gd, gz) if gd is not None else None,
                                     standard, options) if (info["daylight"] or info["drain"]) else ([], {"type": "none"})
        for p in outer:
            if p.kind == "drain" and info["drain"]:
                p.eid = info["drain"][0].get("id", "")
            elif info["daylight"]:
                p.eid = info["daylight"][0].get("id", "")
        if info["daylight"] and not dinfo.get("reached", True):
            warnings.append(f"{side} side: daylight did not reach the ground within the maximum width.")
        all_pts = pts + outer
        outer_line = [(hinge.d, hinge.z)] + [(p.d, p.z) for p in outer]
        rel_spans = [(item, label, [Point(p.d, p.z - grade, p.code) for p in top]) for item, label, top in spans]
        rel_outer = [(d, z - grade) for d, z in outer_line]
        shapes, datum = course_shapes(rel_spans, buildups, rel_outer, options)
        # kerbs and channels
        for item, label, base, kt, kb, channel, ch_end, start in info["kerbs"]:
            depth = float(item.get("unit_depth") or 0.3)
            zb = kt.z - depth
            ring = [(base.d, base.z), (kt.d, kt.z), (kb.d, kb.z), (kb.d, zb), (base.d, zb)]
            shapes.append({"name": "Kerb", "buildup": "", "element": label, "type": "kerb", "material": "Concrete",
                           "unit": "m", "ring": [(d, z - grade) for d, z in ring], "area": _shoelace(ring),
                           "kind": item.get("kind")})
            if channel > 0 and ch_end is not None:
                t = float(item.get("channel_thickness") or 0.15)
                ring = [(start.d, start.z), (ch_end.d, ch_end.z), (ch_end.d, ch_end.z - t), (start.d, start.z - t)]
                shapes.append({"name": "Channel", "buildup": "", "element": label, "type": "kerb", "material": "Concrete",
                               "unit": "m", "ring": [(d, z - grade) for d, z in ring], "area": _shoelace(ring)})
        dr = dinfo.get("drain")
        if dr and dr.get("lining") not in (None, "none", "grass") and dr.get("thickness", 0) > 0:
            t = dr["thickness"]
            line = [(hinge.d, hinge.z)] + [(p.d, p.z) for p in outer if p.d <= dr["back"] + 1e-9]
            nxt = next((p for p in outer if p.d > dr["back"] + 1e-9), None)
            if nxt is not None:
                # line the back slope up to the edge level
                frac = min(max((hinge.z - line[-1][1]) / max(nxt.z - line[-1][1], 1e-9), 0.0), 1.0)
                line.append((line[-1][0] + frac * (nxt.d - line[-1][0]), line[-1][1] + frac * (nxt.z - line[-1][1])))
            ring = line + [(d, z - t) for d, z in reversed(line)]
            shapes.append({"name": f"Drain lining ({dr['lining']})", "buildup": "", "element": dr["label"],
                           "type": "side_drain", "material": dr["lining"], "unit": "m3",
                           "ring": [(d, z - grade) for d, z in ring], "area": _shoelace(ring)})
        wall = dinfo.get("wall")
        if wall and wall.get("top"):
            (bd, bz), (td, tz) = wall["base"], wall["top"]
            t = wall["thickness"]
            ring = [(bd, bz), (td, tz), (td + t, tz), (bd + t, bz)] if wall["cut"] else \
                [(td, tz), (bd, bz), (bd - t, bz), (td - t, tz)]
            shapes.append({"name": "Retaining wall", "buildup": "", "element": dinfo.get("label", "Daylight"),
                           "type": "wall", "material": "Concrete", "unit": "m3",
                           "ring": [(d, z - grade) for d, z in ring], "area": _shoelace(ring),
                           "height": abs(tz - bz)})
        datum_abs = [(d, z + grade) for d, z in datum]
        if datum_abs and abs(datum_abs[-1][0] - hinge.d) < 1e-6 and datum_abs[-1][1] < hinge.z - 1e-9 and outer:
            datum_abs.append((hinge.d, hinge.z))              # box edge: earthworks resume at the finished edge
        if datum_abs:
            last = datum_abs[-1][0]
            datum_abs += [(p.d, p.z) for p in outer if p.d > last + 1e-6]
        else:
            datum_abs = [(p.d, p.z) for p in all_pts]
        # barriers: on the finished surface at an offset from the point before them
        bars = []
        for item, label, anchor in info["barriers"]:
            d = all_pts[anchor].d + float(item.get("offset") or 0.0)
            line = [(p.d, p.z) for p in all_pts]
            z = _line_at(line, d)
            bars.append(Point(d, z, "BARRIER", label, f"{label}: {item.get('kind', '').replace('_', ' ')} barrier "
                                                       f"{_fmt(float(item.get('offset') or 0), options.get('unit', 'm'))} "
                                                       "beyond the previous point"))
            if item.get("kind") == "concrete":
                w, h = float(item.get("base_width") or 0.6), float(item.get("height") or 0.8)
                ring = [(d - w / 2, z), (d + w / 2, z), (d + w / 6, z + h), (d - w / 6, z + h)]
                shapes.append({"name": "Concrete barrier", "buildup": "", "element": label, "type": "barrier",
                               "material": "Concrete", "unit": "m", "ring": [(a, b - grade) for a, b in ring],
                               "area": _shoelace(ring)})
        for s in shapes:
            s["side"] = side
            s["ring"] = [(sign * d, z) for d, z in s["ring"]]
        shapes_all.extend(shapes)
        datum_sides[side] = [(sign * d, z) for d, z in datum_abs]
        result_sides[side] = {"points": [(Point(sign * p.d, p.z, p.code, p.element, p.why, p.kind, p.eid)) for p in all_pts],
                              "daylight": dinfo, "barriers": [Point(sign * b.d, b.z, b.code, b.element, b.why, "barrier")
                                                              for b in bars]}
    # median
    left_pts, right_pts = result_sides["left"]["points"], result_sides["right"]["points"]
    centre = []
    if median:
        rel = _median_profile(median, left_pts[0].z - grade, right_pts[0].z - grade, half, options.get("unit", "m"))
        centre = [Point(p.d, p.z + grade, p.code, p.element, p.why, "top", median.get("id", "median")) for p in rel]
        mb = (buildups.get(median.get("buildup") or "") or {}).get("courses", [])
        if median.get("kind") == "raised":
            ring = [(p.d, p.z - grade) for p in rel]
            shapes_all.append({"name": "Median fill", "buildup": "", "element": median.get("name") or "Median",
                               "type": "median", "material": "Fill", "unit": "m3", "side": "both",
                               "ring": ring, "area": _shoelace(ring)})
        if mb and median.get("kind") == "flush":
            line = [(p.d, p.z - grade) for p in rel]
            depth = 0.0
            for course in mb:
                t = float(course.get("thickness") or 0.0)
                ring = [(d, z - depth) for d, z in line] + [(d, z - depth - t) for d, z in reversed(line)]
                depth += t
                shapes_all.append({"name": course.get("name", "Course"), "buildup": median.get("buildup"),
                                   "element": median.get("name") or "Median", "type": "median",
                                   "material": course.get("material", ""), "unit": course.get("unit", ""),
                                   "thickness": t, "ring": ring, "area": _shoelace(ring), "side": "both",
                                   "top_width": 2 * half})
        left_pts[0] = Point(left_pts[0].d, left_pts[0].z, "MED_E" if median.get("kind") != "raised" else "MED_FL",
                            left_pts[0].element, left_pts[0].why)
    else:
        centre = [Point(0.0, left_pts[0].z, "CL", "Baseline",
                        "Crown on the baseline at the design profile level" + (f" (rotation shift {shift:+.3f})" if shift else ""))]
    top = list(reversed(left_pts[1:])) + centre + right_pts[1:]
    if median:
        datum = list(reversed(datum_sides["left"])) + datum_sides["right"]
    else:
        datum = list(reversed(datum_sides["left"])) + datum_sides["right"][1:]
    datum = _monotone(datum)
    top = _monotone_points(top)
    return {"top": top, "datum": datum, "shapes": shapes_all, "sides": result_sides, "warnings": warnings,
            "grade": grade, "shift": shift}


def _monotone(line):
    """Offsets never decrease; a vertical step keeps its two levels (in order), repeated points are dropped."""
    out = []
    for d, z in line:
        if out and d < out[-1][0] - 1e-9:
            continue
        if out and abs(d - out[-1][0]) <= 1e-9:
            if abs(z - out[-1][1]) <= 1e-9:
                continue
            if len(out) > 1 and abs(out[-2][0] - d) <= 1e-9:
                out[-1] = (d, z)                       # keep only the two ends of a vertical
                continue
            d = out[-1][0]
        out.append((d, z))
    return out


def _monotone_points(points):
    """Drop points that fold back on themselves (a vertical kerb face keeps a small batter)."""
    out = []
    for p in points:
        if out and p.d < out[-1].d - 1e-9:
            continue
        if out and abs(p.d - out[-1].d) < 1e-9:
            p = Point(out[-1].d + 1e-4, p.z, p.code, p.element, p.why, p.kind, p.eid)
        out.append(p)
    return out


def check_assembly(assembly, standard, speed=None, context=None):
    """Advisory checks of every element against the standard: [{level, side, element, message, ref}]."""
    if standard is None:
        return []
    out = []
    for side in ("median",) + SIDES:
        items = [assembly["median"]] if side == "median" and assembly.get("median") else (
            assembly.get(side, []) if side != "median" else [])
        for index, item in enumerate(items):
            kind = item["type"]
            label = item.get("name") or f"{LABELS.get(kind, kind)} {sum(1 for e in items[:index + 1] if e['type'] == kind)}"
            messages = []
            if kind == "lane":
                surface = item.get("surface", "paved")
                messages += standard.check("lane", "width", float(item["width"]), label=f"{label} width")
                messages += standard.check("lane", "crossfall", float(item["crossfall"]),
                                           kind=surface if surface != "paved" else None, label=f"{label} crossfall")
                if item.get("kind") == "auxiliary":
                    messages += standard.check("auxiliary_lane", "width", float(item["width"]), label=f"{label} width")
            elif kind == "shoulder":
                messages += standard.check("shoulder", "width", float(item["width"]), label=f"{label} width")
                messages += standard.check("shoulder", "crossfall", float(item["crossfall"]),
                                           kind=None if item.get("paved", True) else "gravel", label=f"{label} crossfall")
            elif kind == "kerb":
                messages += standard.check("kerb", "height", float(item["height"]), kind=item.get("kind"),
                                           label=f"{label} height")
                if speed:
                    messages += standard.check("kerb", "speed", float(speed), kind=item.get("kind"),
                                               label=f"Design speed with a {item.get('kind', '').replace('_', ' ')} kerb")
            elif kind in ("sidewalk", "cycle_track"):
                messages += standard.check(kind, "width", float(item["width"]), label=f"{label} width")
                messages += standard.check(kind, "crossfall", float(item["crossfall"]), label=f"{label} crossfall")
            elif kind == "median":
                messages += standard.check("median", "width", float(item["width"]), kind=item.get("kind"),
                                           label=f"{label} width")
                if item.get("kind") in ("depressed", "raised"):
                    value = float(item.get("slope") if item.get("kind") == "depressed" else item.get("top_fall") or 0)
                    messages += standard.check("median", "slope", value, kind=item.get("kind"), label=f"{label} slope")
            elif kind == "side_drain":
                messages += standard.check("side_drain", "depth", float(item["depth"]), label=f"{label} depth")
            elif kind == "daylight":
                for limit, ratio in item.get("fill_bands") or []:
                    messages += standard.check("fill", "slope", float(ratio), label=f"{label} fill slope 1:{float(ratio):g}")
                for limit, ratio in item.get("cut_bands") or []:
                    messages += standard.check("cut", "backslope", float(ratio), label=f"{label} cut slope 1:{float(ratio):g}")
            elif kind == "barrier":
                messages += standard.check("barrier", "setback", float(item.get("offset") or 0), label=f"{label} set-back")
            for m in messages:
                m["side"] = side
                m["element_id"] = item.get("id")
            out += messages
    return out


__all__ = ["assign_ids", "element_spans", "SCHEMA", "LABELS", "SIDES", "SideState", "Point", "element", "defaults", "normalise_assembly", "build_section",
           "check_assembly", "place_side", "daylight_side", "course_shapes"]


def element_spans(section):
    """{(side, element id): (inner Point, outer Point, label)} of every element in a built section."""
    spans = {}
    for side in SIDES:
        pts = section["sides"][side]["points"]
        for k, p in enumerate(pts):
            if not p.eid or k == 0:
                continue
            key = (side, p.eid)
            if key not in spans:
                spans[key] = [pts[k - 1], p, p.element]
            else:
                spans[key][1] = p
    centre = [p for p in section["top"] if p.eid and p.eid.startswith("median")]
    if centre:
        spans[("median", centre[0].eid)] = [centre[0], centre[-1], centre[0].element]
    return {k: tuple(v) for k, v in spans.items()}
