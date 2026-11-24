"""Concept structures for a synthetic corridor: box and pipe culverts from a schedule, and bridges.

Nothing here sizes a structure. Culverts are drawn from the cells, spans and rises the user gives
(a schedule from their own drainage design); bridges carry the road over the stretches where the
corridor's side slopes do not reach the ground, with piers at a chosen span.
"""

from __future__ import annotations

import csv
import io
import math
import re

import numpy as np

from .quantity_units import LINEAR_UNITS
from .stationing import parse_station as source_station

# ------------------------------------------------------------------ schedule

FIELDS = {          # field -> accepted headings (compared without case, spaces, units or punctuation)
    "id": ("id", "site", "name", "structure", "ref", "reference", "structureid"),
    "station": ("chainage", "station", "ch", "sta", "location", "chainagem", "stationm", "startchainage", "fromchainage"),
    "end": ("endchainage", "tochainage", "chainageto", "endstation", "tostation", "to", "end"),
    "type": ("type", "structuretype", "kind", "culverttype"),
    "cells": ("cells", "noofcells", "numberofcells", "ncells", "nocells", "barrels", "noofbarrels", "pipes", "noofpipes"),
    "span": ("span", "cellb", "cellbm", "cellwidth", "width", "spanm", "b", "clearspan"),
    "rise": ("rise", "celld", "celldm", "cellheight", "height", "risem", "d", "clearheight"),
    "diameter": ("diameter", "dia", "diameterm", "pipediameter", "diametermm"),
    "skew": ("skew", "skewdeg", "skewangle", "skewangledeg"),
    "spans": ("spans", "noofspans", "numberofspans", "nspans"),
    "piers": ("piers", "pierchainages", "pierstations", "pierlocations"),
    "invert_in": ("invertin", "inletinvert", "usinvert", "upstreaminvert", "invertus", "inletil"),
    "invert_out": ("invertout", "outletinvert", "dsinvert", "downstreaminvert", "invertds", "outletil"),
}


def _key(heading):
    return re.sub(r"[^a-z0-9]", "", heading.lower().replace("(m)", "").replace("_m", "m"))


def parse_station(text, horizontal_unit="meter"):
    return source_station(str(text), horizontal_unit)


def _heading(heading):
    if _key(heading) in FIELDS["invert_in"]:
        return heading, None
    match = re.search(r"(?:[_ (]?)(mm|cm|ftus|ft|in|yd|m)\)?$", heading.strip(), re.I)
    return (heading[:match.start()], match[1].lower()) if match else (heading, None)


def _dimension(value, heading, target):
    value = _number(value)
    if value is None:
        return None
    unit = _heading(heading)[1]
    factors = {"mm": .001, "cm": .01, "m": 1., "ft": .3048, "ftus": 1200 / 3937, "in": .0254, "yd": .9144}
    converted = value if unit is None else value * factors[unit] / LINEAR_UNITS[target.lower()][0]
    if not math.isfinite(converted):
        raise ValueError(f"Structure dimension '{heading}' is outside the supported numeric range.")
    return converted


def _number(value):
    try:
        value = float(str(value).strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _kind(text, has_diameter, has_rise):
    text = (text or "").lower()
    if "bridge" in text:
        return "bridge"
    if "pipe" in text or "ring" in text or (has_diameter and not has_rise):
        return "pipe"
    return "box"


def read_schedule_csv(path_or_text, horizontal_unit="meter", vertical_unit=None, station_unit=None):
    """(structures, notes): structures from a CSV schedule, notes saying which column became which field."""
    text = path_or_text if "\n" in path_or_text else open(path_or_text, encoding="utf-8-sig").read()
    vertical_unit = vertical_unit or horizontal_unit
    station_unit = station_unit or horizontal_unit
    reader = csv.DictReader(io.StringIO(text))
    headings = reader.fieldnames or []
    mapping = {}
    for field, names in FIELDS.items():
        for heading in headings:
            if _key(_heading(heading)[0]) in names and field not in mapping:
                mapping[field] = heading
    if "station" not in mapping:
        raise ValueError("The structure schedule has no chainage column (accepted headings: chainage, station, ch).")
    used = set(mapping.values())
    notes = ["Structure schedule columns used: " + ", ".join(f"{h} → {f}" for f, h in mapping.items())]
    ignored = [h for h in headings if h not in used]
    if ignored:
        notes.append(f"Ignored columns ({len(ignored)}): " + ", ".join(ignored[:12]) + (" …" if len(ignored) > 12 else ""))
    structures = []
    for line, row in enumerate(reader, 2):
        def get(field, row=row):
            return row.get(mapping[field]) if field in mapping else None
        def station_value(text, field):
            declared = _heading(mapping.get(field, ""))[1]
            names = {"m": "meter", "mm": "millimeter", "cm": "centimeter", "ft": "foot", "ftus": "USSurveyFoot"}
            unit = names.get(declared, station_unit)
            if declared in {"in", "yd"}:
                factor = .0254 if declared == "in" else .9144
                source = parse_station(text, None)
            else:
                factor = LINEAR_UNITS[unit.lower()][0]
                source = parse_station(text, unit)
            converted = source * factor / LINEAR_UNITS[station_unit.lower()][0]
            if not math.isfinite(converted):
                raise ValueError("Station conversion is outside the supported numeric range.")
            return converted
        try:
            station = station_value(get("station"), "station")
        except (ValueError, TypeError):
            notes.append(f"Row {line} skipped: chainage '{get('station')}' is not a number.")
            continue
        def dimension(field, unit):
            return _dimension(get(field), mapping.get(field, ""), unit)
        diameter = dimension("diameter", horizontal_unit)
        rise = dimension("rise", vertical_unit)
        kind = _kind(get("type"), diameter is not None, rise is not None)
        end = None
        if get("end"):
            try:
                end = station_value(get("end"), "end")
            except ValueError:
                end = None
        structures.append({
            "id": (get("id") or "").strip() or f"{kind} {station:.0f}", "kind": kind, "station": station, "end": end,
            "cells": max(int(_number(get("cells")) or 1), 1), "span": dimension("span", horizontal_unit) or diameter,
            "rise": rise or (diameter * LINEAR_UNITS[horizontal_unit.lower()][0] / LINEAR_UNITS[vertical_unit.lower()][0] if diameter else None), "skew": _number(get("skew")) or 0.0,
            "invert_in": dimension("invert_in", vertical_unit), "invert_out": dimension("invert_out", vertical_unit),
            "spans": _number(get("spans")),
            "piers": [station_value(t, "piers") for t in re.split(r"[\s;/|]+", get("piers").strip())] if get("piers") else None})
    return structures, notes


_TYPED = re.compile(r"^\s*(?P<a>[\d+.]+)\s*(?:[-–]\s*(?P<b>[\d+.]+))?\s*=\s*(?P<kind>box|pipe|bridge)\b(?P<rest>.*)$", re.I)


def read_schedule_text(text, horizontal_unit="meter", vertical_unit=None, station_unit=None):
    """'1+250 = box 3 x 2.0 x 2.0; 1+840 = pipe 2 x 0.9; 2+410 = box 1 x 1.5 x 1.5, skew 15'; bridges:
    '1+364-2+293 = bridge, span 25', '... = bridge, spans 7' or '... = bridge, piers 1+500 1+640 1+780'."""
    vertical_unit = vertical_unit or horizontal_unit
    station_unit = station_unit or horizontal_unit
    structures = []
    for part in re.split(r"[;\n]", text or ""):
        if not part.strip():
            continue
        match = _TYPED.match(part)
        if not match:
            raise ValueError(f"Cannot read structure '{part.strip()}'. Use for example '1+250 = box 3 x 2.0 x 2.0', "
                             "'1+840 = pipe 2 x 0.9' or '1+364-2+293 = bridge, span 25'.")
        kind = match["kind"].lower()
        rest = match["rest"]
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", rest.split(",")[0])]
        options = dict((k.lower(), float(v)) for k, v in re.findall(r"\b(skew|spans|span)\s*[:=]?\s*(-?\d+(?:\.\d+)?)", rest, re.I))
        pier_text = re.search(r"\bpiers?\s*[:=]?\s*([\d+.\s/]+)", rest, re.I)
        piers = [parse_station(t, station_unit) for t in re.split(r"[\s/]+", pier_text.group(1).strip())] if pier_text else None
        station = parse_station(match["a"], station_unit)
        item = {"id": f"{kind} {match['a']}", "kind": kind, "station": station,
                "end": parse_station(match["b"], station_unit) if match["b"] else None, "cells": 1, "span": None, "rise": None,
                "skew": options.get("skew", 0.0), "invert_in": None, "invert_out": None}
        if kind == "box":
            if len(numbers) != 3:
                raise ValueError(f"'{part.strip()}': a box needs cells x span x rise, for example 'box 3 x 2.0 x 2.0'.")
            item.update(cells=int(numbers[0]), span=numbers[1], rise=numbers[2])
        elif kind == "pipe":
            if len(numbers) != 2:
                raise ValueError(f"'{part.strip()}': a pipe needs lines x diameter, for example 'pipe 2 x 0.9'.")
            item.update(cells=int(numbers[0]), span=numbers[1], rise=numbers[1] * LINEAR_UNITS[horizontal_unit.lower()][0] / LINEAR_UNITS[vertical_unit.lower()][0])
        else:
            item.update(span=options.get("span"), spans=options.get("spans"), piers=piers)
        structures.append(item)
    return structures


# ------------------------------------------------------------------ meshes

class MeshBuilder:
    """Collects flat-shaded pieces (each face has its own vertices)."""

    def __init__(self):
        self.xyz, self.faces, self.count = [], [], 0

    def add(self, xyz, faces):
        self.xyz.append(np.asarray(xyz))
        self.faces.append(np.asarray(faces) + self.count)
        self.count += len(xyz)

    def quads(self, corners):
        """corners: (k, 4, 3) quads, counter-clockwise seen from outside."""
        corners = np.asarray(corners, float).reshape(-1, 4, 3)
        base = self.count + 4 * np.arange(len(corners))[:, None]
        self.xyz.append(corners.reshape(-1, 3))
        self.faces.append(np.concatenate([base + [0, 1, 2], base + [0, 2, 3]]))
        self.count += 4 * len(corners)

    def prism(self, bottom, top):
        """A solid from a bottom quad and the top quad above it ((4, 3) each, same plan order, counter-clockwise)."""
        b, t = np.asarray(bottom, float), np.asarray(top, float)
        quads = [b[::-1], t]
        for i in range(4):
            j = (i + 1) % 4
            quads.append([b[i], b[j], t[j], t[i]])
        self.quads(quads)

    def box(self, centre, u, v, length, width, z_bottom, z_top):
        """Cuboid along unit plan vectors u, v; z_bottom/z_top are scalars or (start, end) along u."""
        c, u, v = np.asarray(centre, float)[:2], np.asarray(u, float), np.asarray(v, float)
        zb = np.broadcast_to(np.asarray(z_bottom, float), (2,))
        zt = np.broadcast_to(np.asarray(z_top, float), (2,))
        plan = [c - u * length / 2 - v * width / 2, c + u * length / 2 - v * width / 2,
                c + u * length / 2 + v * width / 2, c - u * length / 2 + v * width / 2]
        ends = (0, 1, 1, 0)
        self.prism([[*p, zb[e]] for p, e in zip(plan, ends)], [[*p, zt[e]] for p, e in zip(plan, ends)])

    def tube(self, start, end, radius, thickness, segments=20):
        """A pipe from start to end (3D points) with its wall."""
        start, end = np.asarray(start, float), np.asarray(end, float)
        axis = end - start
        axis /= np.linalg.norm(axis)
        side = np.cross(axis, [0.0, 0.0, 1.0])
        side /= max(np.linalg.norm(side), 1e-12)
        up = np.cross(side, axis)
        angles = np.linspace(0, 2 * np.pi, segments + 1)
        ring = np.cos(angles)[:, None] * side + np.sin(angles)[:, None] * up
        outer, inner = radius + thickness, radius
        quads = []
        for k in range(segments):
            a, b = ring[k], ring[k + 1]
            quads.append([start + a * outer, start + b * outer, end + b * outer, end + a * outer])      # outside
            quads.append([start + b * inner, start + a * inner, end + a * inner, end + b * inner])      # inside
            quads.append([start + b * outer, start + a * outer, start + a * inner, start + b * inner])  # ends
            quads.append([end + a * outer, end + b * outer, end + b * inner, end + a * inner])
        self.quads(quads)

    def result(self):
        if not self.xyz:
            return np.zeros((0, 3)), np.zeros((0, 3), int)
        return np.vstack(self.xyz), np.vstack(self.faces)


def culvert(builder, item, centre, normal, left_length, right_length, ground_at, cover=0.6, record=None, horizontal_unit="meter", vertical_unit="meter"):
    """Add a box or pipe culvert at plan point ``centre`` crossing the road along ``normal`` rotated by the skew.

    ``left_length`` / ``right_length``: barrel length each side of the centreline (to the fill toes).
    ``ground_at(points)`` samples the ground; ``centre[2]`` is the road level, and the barrel keeps ``cover``
    under it. Returns a short description for the log."""
    h = LINEAR_UNITS[horizontal_unit.lower()][0]
    v = LINEAR_UNITS[vertical_unit.lower()][0]
    if h != 1 or v != 1:
        scale = np.array([h, h, v])
        metric = dict(item, span=item["span"] * h if item["span"] else None,
                      rise=item["rise"] * v if item["rise"] else None,
                      invert_in=item.get("invert_in") * v if item.get("invert_in") is not None else None,
                      invert_out=item.get("invert_out") * v if item.get("invert_out") is not None else None)
        parts, channels = MeshBuilder(), []
        text = culvert(parts, metric, np.asarray(centre) * scale, normal, left_length * h, right_length * h,
                       lambda xy: ground_at(xy / h) * v, cover=cover, record=channels)
        xyz, faces = parts.result()
        builder.add(xyz / scale, faces)
        if record is not None:
            record.extend(dict(ch, left=ch["left"] / h, right=ch["right"] / h,
                               z_left=ch["z_left"] / v, z_right=ch["z_right"] / v, width=ch["width"] / h)
                          for ch in channels)
        return text
    skew = math.radians(item.get("skew") or 0.0)
    cos, sin = math.cos(skew), math.sin(skew)
    u = np.array([normal[0] * cos - normal[1] * sin, normal[0] * sin + normal[1] * cos])      # barrel axis (to the right)
    v = np.array([-u[1], u[0]])
    left_length, right_length = left_length / max(cos, 0.2), right_length / max(cos, 0.2)
    left_end, right_end = centre[:2] - u * left_length, centre[:2] + u * right_length
    span, rise, cells = item["span"], item["rise"], item["cells"]
    if not span or not rise:
        raise ValueError(f"{item['id']}: the schedule gives no span and rise (or diameter).")
    line = left_end + (right_end - left_end) * np.linspace(0, 1, 41)[:, None]
    ground = ground_at(line)
    ends = ground_at(np.array([left_end, right_end]))
    inverts = [item.get("invert_in"), item.get("invert_out")]
    if inverts[0] is None and inverts[1] is None:
        low = np.nanmin(ground) if np.isfinite(ground).any() else centre[2] - rise - 1.0
        z_left = z_right = low
        source = "invert at the lowest ground under it"
    else:
        a, b = (inverts[0] if inverts[0] is not None else inverts[1]), (inverts[1] if inverts[1] is not None else inverts[0])
        high, low = max(a, b), min(a, b)
        left_higher = not (np.isfinite(ends).all() and ends[1] > ends[0])
        z_left, z_right = (high, low) if left_higher else (low, high)       # the higher invert at the higher ground
        source = "inverts from the schedule"
    barrel = (span + 2 * max(0.06, span / 12.0)) if item["kind"] == "pipe" else rise + 2 * (0.25 if rise <= 2.0 else 0.3)
    highest = centre[2] - cover - barrel
    if max(z_left, z_right) > highest:
        drop = max(z_left, z_right) - highest
        z_left, z_right = z_left - drop, z_right - drop
        source += (f"; does not fit under the road by {drop:.1f} m (with {cover:g} m cover), drawn that much lower: "
                   "raise the grade or lower the invert")
    length = left_length + right_length
    mid = (left_end + right_end) / 2
    zs = (z_left, z_right)
    if item["kind"] == "pipe":
        wall = max(0.06, span / 12.0)
        gap = max(0.3, span / 2.0)
        width = cells * (span + 2 * wall) + (cells - 1) * gap
        for k in range(cells):
            offset = -width / 2 + wall + span / 2 + k * (span + 2 * wall + gap)
            a = np.array([*(left_end + v * offset), z_left + span / 2])
            b = np.array([*(right_end + v * offset), z_right + span / 2])
            builder.tube(a, b, span / 2, wall)
        height = span + 2 * wall
        label = f"{cells} x {span:g} m pipe"
    else:
        wall = 0.25 if rise <= 2.0 else 0.3
        width = cells * span + (cells + 1) * wall
        builder.box(mid, u, v, length, width, (z_left - wall, z_right - wall), zs)                       # base slab
        builder.box(mid, u, v, length, width, (z_left + rise, z_right + rise), (z_left + rise + wall, z_right + rise + wall))
        for k in range(cells + 1):
            offset = -width / 2 + wall / 2 + k * (span + wall)
            builder.box(mid + v * offset, u, v, length, wall, zs, (z_left + rise, z_right + rise))
        height = rise + 2 * wall
        label = f"{cells} x {span:g} x {rise:g} m box"
    if record is not None:
        record.append({"left": left_end, "right": right_end, "z_left": z_left, "z_right": z_right, "width": width})
    # headwalls and splayed wingwalls at both ends
    for end, z, sign in ((left_end, z_left, -1.0), (right_end, z_right, 1.0)):
        base = z - (0.3 if item["kind"] == "box" else 0.1)
        top = base + height + 0.5
        soffit = z + (rise if item["kind"] == "box" else span + max(0.06, span / 12.0))
        builder.box(end + u * sign * 0.15, u, v, 0.3, width + 0.6, soffit, top)                     # headwall over the openings
        builder.box(end + u * sign * 1.0, u, v, 2.0, width + 0.6, base, z)                            # apron at bed level
        for side in (-1.0, 1.0):
            flare = (u * sign + v * side) / math.sqrt(2.0)
            wing_length = max(1.5 * height, 1.5)
            start = end + v * side * (width / 2 + 0.3)
            middle = start + flare * wing_length / 2
            builder.box(middle, flare, np.array([-flare[1], flare[0]]), wing_length, 0.3, base, (top, base + 0.6))
    return f"{item['id']}: {label}, {length:.0f} m long, {source}"


def bridge(builder, centre, stations, normal, rows, deck_width, span, ground_at, depth=None, spans=None, piers=None,
           horizontal_unit="meter", vertical_unit="meter", station_unit="meter"):
    """Deck, parapets, piers and abutments over the centreline rows ``rows`` (a contiguous index range).

    The deck top is the road (drawn by the corridor); this adds the deck body below it, parapets on its
    edges, piers at about ``span`` spacing and an abutment wall at each end. Returns (description, pier count)."""
    h = LINEAR_UNITS[horizontal_unit.lower()][0]
    v = LINEAR_UNITS[vertical_unit.lower()][0]
    st = LINEAR_UNITS[station_unit.lower()][0]
    if h != 1 or v != 1 or st != 1:
        scale = np.array([h, h, v])
        parts = MeshBuilder()
        result = bridge(parts, np.asarray(centre) * scale, np.asarray(stations) * st, normal, rows,
                        deck_width * h, span * h, lambda xy: ground_at(xy / h) * v,
                        depth=None if depth is None else depth * v, spans=spans,
                        piers=[p * st for p in piers] if piers else None)
        xyz, faces = parts.result()
        builder.add(xyz / scale, faces)
        return result
    rows = np.asarray(rows)
    start, end = stations[rows[0]], stations[rows[-1]]
    length = end - start
    if piers:                                   # pier chainages given: they decide the spans
        pier_stations = sorted(s for s in piers if start < s < end)
    else:
        count = int(spans) if spans else max(int(math.ceil(length / span)), 1)
        pier_stations = [start + k * length / count for k in range(1, max(count, 1))]
    spans = len(pier_stations) + 1
    longest = float(np.max(np.diff([start, *pier_stations, end])))
    depth = depth if depth is not None else float(np.clip(longest / 15.0, 0.8, 2.0))
    half = deck_width / 2
    for i in rows[:-1]:
        j = i + 1
        a, b = centre[i], centre[j]
        na, nb = normal[i], normal[j]
        for side in (-1.0, 1.0):                                                # parapets
            pa, pb = a[:2] + na * side * half, b[:2] + nb * side * half
            qa, qb = a[:2] + na * side * (half - 0.3), b[:2] + nb * side * (half - 0.3)
            za, zb = a[2] - 0.1, b[2] - 0.1
            builder.prism([[*pa, za], [*pb, zb], [*qb, zb], [*qa, za]][:: int(side)],
                          [[*pa, za + 1.0], [*pb, zb + 1.0], [*qb, zb + 1.0], [*qa, za + 1.0]][:: int(side)])
        la, lb = a[:2] - na * half, b[:2] - nb * half                           # deck body below the road
        ra, rb = a[:2] + na * half, b[:2] + nb * half
        builder.prism([[*la, a[2] - depth], [*ra, a[2] - depth], [*rb, b[2] - depth], [*lb, b[2] - depth]],
                      [[*la, a[2] - 0.15], [*ra, a[2] - 0.15], [*rb, b[2] - 0.15], [*lb, b[2] - 0.15]])
    placed = []
    for s in pier_stations:
        i = int(np.clip(np.searchsorted(stations, s), 1, len(stations) - 1))
        t = (s - stations[i - 1]) / max(stations[i] - stations[i - 1], 1e-9)
        p = centre[i - 1] + (centre[i] - centre[i - 1]) * t
        n = normal[i - 1]
        g = ground_at(np.array([p[:2]]))[0]
        if not np.isfinite(g) or g > p[2] - depth - 0.5:
            continue
        along = np.array([n[1], -n[0]])
        builder.box(p, along, n, 1.5, deck_width - 2.0, g - 1.0, p[2] - depth)
        builder.box(p, along, n, 2.0, deck_width, p[2] - depth - 1.0, p[2] - depth)          # pier head
        placed.append(s)
    for i in (rows[0], rows[-1]):
        p, n = centre[i], normal[i]
        g = ground_at(np.array([p[:2]]))[0]
        g = p[2] - depth - 2.0 if not np.isfinite(g) else min(g, p[2] - depth - 0.5)
        along = np.array([n[1], -n[0]])
        builder.box(p, along, n, 2.0, deck_width + 2.0, g - 1.0, p[2] - depth)
    return (f"bridge {start:.0f}–{end:.0f}: {length:.0f} m, {spans} span{'s' if spans > 1 else ''} (longest "
            f"{longest:.0f} m), {len(placed)} pier{'s' if len(placed) != 1 else ''}"), len(placed)


def carve_channels(xyz, faces, channels, apron=30.0, bank=1.5, bed_rise=0.05, horizontal_unit="meter", vertical_unit="meter"):
    """Lower the ground along each culvert: a bed at the barrel inverts between the ends, rising ``bed_rise``
    per metre for ``apron`` beyond them, as wide as the structure, with banks at ``bank`` H:1V. The ground is
    only ever lowered. Returns (xyz, faces, number of vertices lowered); triangles near the channels are split
    first so the cut has a shape."""
    h = LINEAR_UNITS[horizontal_unit.lower()][0]
    v = LINEAR_UNITS[vertical_unit.lower()][0]
    if h != 1 or v != 1:
        scale = np.array([h, h, v])
        metric = [dict(ch, left=np.asarray(ch["left"]) * h, right=np.asarray(ch["right"]) * h,
                       z_left=ch["z_left"] * v, z_right=ch["z_right"] * v, width=ch["width"] * h) for ch in channels]
        xyz, faces, count = carve_channels(np.asarray(xyz) * scale, faces, metric, apron, bank, bed_rise)
        return xyz / scale, faces, count
    xyz, faces = np.asarray(xyz, float), np.asarray(faces, np.int64)
    lowered = 0
    for item in channels:
        a, b = np.asarray(item["left"], float), np.asarray(item["right"], float)
        length = float(np.linalg.norm(b - a))
        if length <= 0:
            continue
        u = (b - a) / length
        v = np.array([-u[1], u[0]])
        reach = item["width"] / 2 + 6.0
        corners = [a - u * apron - v * reach, b + u * apron - v * reach, b + u * apron + v * reach, a - u * apron + v * reach]
        footprint = (np.array([[*q, 0.0] for q in corners]), np.array([[0, 1, 2], [0, 2, 3]]))
        xyz, faces = refine(xyz, faces, footprint, max(item["width"] / 4.0, 1.0))
        rel = xyz[:, :2] - a
        along, across = rel @ u, rel @ v
        inside = (along >= -apron) & (along <= length + apron) & (np.abs(across) <= reach + 30.0)
        t = np.clip(along / length, 0.0, 1.0)
        bed = item["z_left"] + (item["z_right"] - item["z_left"]) * t
        beyond = np.where(along < 0, -along, np.where(along > length, along - length, 0.0))
        surface = bed + bed_rise * beyond + np.maximum(np.abs(across) - item["width"] / 2, 0.0) / bank
        cut = inside & (surface < xyz[:, 2])
        xyz = xyz.copy()
        xyz[cut, 2] = surface[cut]
        lowered += int(cut.sum())
    return xyz, faces, lowered


def _near_cells(tri, longest, footprint):
    """Cheap pre-test for :func:`refine`: True for triangles that may touch ``footprint``, from a coarse grid
    of the cells the footprint's triangles cover (grown by one cell, so any triangle no longer than a cell
    that touches the footprint has a corner in a marked cell)."""
    cell = max(float(np.percentile(longest, 99)), 1e-6)
    fxyz, ffaces = footprint
    corners = np.asarray(fxyz, float)[np.asarray(ffaces, np.int64)][:, :, :2]
    origin = corners.reshape(-1, 2).min(axis=0) - cell
    lo = np.floor((corners.min(axis=1) - origin) / cell).astype(np.int64) - 1
    hi = np.floor((corners.max(axis=1) - origin) / cell).astype(np.int64) + 1
    span = hi - lo + 1
    if int(np.prod(span, axis=1).sum()) > 40_000_000:              # footprint triangles far larger than these: skip
        return np.ones(len(tri), bool)
    width = np.int64(1) << 31
    count = span[:, 0] * span[:, 1]
    owner = np.repeat(np.arange(len(span)), count)
    step = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    cx = lo[owner, 0] + step % span[owner, 0]
    cy = lo[owner, 1] + step // span[owner, 0]
    marked = np.unique(cy * width + cx)
    vertex = np.floor((tri - origin) / cell).astype(np.int64)
    keys = vertex[:, :, 1] * width + vertex[:, :, 0]
    position = np.clip(np.searchsorted(marked, keys), 0, len(marked) - 1)
    return (marked[position] == keys).any(axis=1) | (longest > cell)


def refine(xyz, faces, footprint, target, rounds=5, boundary=False):
    """Split triangles touching ``footprint`` (a TIN) into four at their edge midpoints until their edges are at most
    ``target`` long; the midpoints lie on the original planes, so the surface keeps its shape. With ``boundary``,
    only triangles that cross the footprint's edge are split."""
    from .tin_overlay import TinIndex, sample_tin

    low, high = footprint[0][:, :2].min(axis=0), footprint[0][:, :2].max(axis=0)
    index = TinIndex(*footprint)
    for _round in range(rounds):
        tri = xyz[faces]
        longest = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
        near = ((tri[:, :, :2].max(axis=1) >= low) & (tri[:, :, :2].min(axis=1) <= high)).all(axis=1) & (longest > target)
        if near.any():
            near[near] = _near_cells(tri[near, :, :2], longest[near], footprint)
        touch = np.zeros(len(faces), bool)
        if near.any():
            sub = tri[near]
            probes = np.concatenate([sub, (sub + np.roll(sub, 1, axis=1)) / 2, sub.mean(axis=1, keepdims=True)], axis=1)
            hits = np.isfinite(sample_tin(index, probes.reshape(-1, 3)[:, :2])).reshape(len(sub), 7)
            # boundary: only triangles crossing the footprint's edge, so a cut follows the edge closely
            touch[near] = (hits.any(axis=1) & ~hits.all(axis=1)) if boundary else hits.any(axis=1)
        chosen = touch
        if not chosen.any():
            break
        picked = faces[chosen]
        edges = np.sort(np.concatenate([picked[:, [0, 1]], picked[:, [1, 2]], picked[:, [2, 0]]]), axis=1)
        unique, inverse = np.unique(edges, axis=0, return_inverse=True)
        middle = len(xyz) + inverse.reshape(-1).reshape(3, -1).T
        xyz = np.vstack([xyz, (xyz[unique[:, 0]] + xyz[unique[:, 1]]) / 2])
        p0, p1, p2 = picked.T
        m01, m12, m20 = middle.T
        faces = np.concatenate([faces[~chosen], np.column_stack([p0, m01, m20]), np.column_stack([m01, p1, m12]),
                                np.column_stack([m20, m12, p2]), np.column_stack([m01, m12, m20])])
    return xyz, faces


def low_points(stations, road_z, ground_z, min_fill=1.5, window=50.0):
    """Stations where the ground has a local low under at least ``min_fill`` of fill: candidate crossings."""
    found = []
    finite = np.isfinite(ground_z)
    for i in np.flatnonzero(finite):
        near = finite & (np.abs(stations - stations[i]) <= window)
        if ground_z[i] <= ground_z[near].min() + 1e-9 and road_z[i] - ground_z[i] >= min_fill:
            if not found or stations[i] - found[-1] > window:
                found.append(float(stations[i]))
    return found
