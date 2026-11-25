"""Setting-out data from a LandXML alignment and its feature lines (no QGIS).

Follows common setting-out practice: a geometry schedule of the horizontal elements, the key points (BP, TS, SC, CS,
ST, PC, PT, PCC, PRC, EP) and the intersection points, then coordinates at a regular chainage interval (with the key
points included) with whole-circle bearings from grid north, design and ground levels and the cut or fill, and the
offsets and levels of each feature line (road edges, shoulders, daylight) at those chainages. Coordinates are the
file's, in its CRS and unit, easting before northing in the tables; nothing is transformed.

Positions come from the alignment densified at 0.25 (the chord of a 0.25 arc misses it by under 0.1 mm at radius
80), so coordinates are good to the millimetre.
"""

from __future__ import annotations

import csv
import html
import math

import numpy as np

from .corridor_build import AlignmentFrame, alignment_element
from .features import read_line_features
from .geometry import read_alignments
from .longsection import read_alignment_profiles
from .parser import load_document

STEP = 0.25


def dms(bearing):
    """Whole-circle bearing in degrees as 123°45'06"."""
    b = bearing % 360.0
    d = int(b)
    m_full = (b - d) * 60
    m = int(m_full)
    s = round((m_full - m) * 60)
    if s == 60:
        s, m = 0, m + 1
    if m == 60:
        m, d = 0, d + 1
    return f"{d % 360:03d}°{m:02d}'{s:02d}\""


def bearing_of(tangent):
    """Whole-circle bearing (degrees from grid north, clockwise) of a unit tangent (dE, dN)."""
    return math.degrees(math.atan2(tangent[0], tangent[1])) % 360.0


KEY_NAMES = {("line", "spiral"): "TS", ("spiral", "curve"): "SC", ("curve", "spiral"): "CS", ("spiral", "line"): "ST",
             ("line", "curve"): "PC", ("curve", "line"): "PT", ("spiral", "spiral"): "SS"}


def _profile_sampler(profiles, kind, name=None):
    pick = [p for p in profiles if p.kind == kind and (name is None or p.name == name)]
    if not pick:
        return None, None
    profile = pick[0]
    pts = sorted(pt for part in profile.parts for pt in part)
    st = np.array([p[0] for p in pts], float)
    z = np.array([p[1] for p in pts], float)

    def sample(stations):
        stations = np.asarray(stations, float)
        out = np.interp(stations, st, z, left=np.nan, right=np.nan)
        return out
    return sample, profile.name


def exact_bearing(frame, station, side=0):
    """Bearing at ``station`` from the positions just before and after it (side -1: before only, +1: after only), so
    an element's own direction is used at its ends; good to about a second of arc."""
    h = STEP / 2
    a = station - (h if side <= 0 else 0.0)
    b = station + (h if side >= 0 else 0.0)
    a, b = max(a, frame.start), min(b, frame.end)
    if b - a < 1e-9:
        a, b = max(station - h, frame.start), min(station + h, frame.end)
    xy, _t, _n = frame.at([a, b])
    return bearing_of((xy[1] - xy[0]) / max(np.hypot(*(xy[1] - xy[0])), 1e-12))


def _curvature(radius):
    try:
        r = float(radius)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if not math.isfinite(r) or r <= 0 or r > 1e8 else 1.0 / r


def _analytic_bearings(frame, elements):
    """Exact bearings in and out of each element: lines from their end points, then carried through curves (L / R)
    and spirals (L × mean curvature), restarting from the geometry wherever the elements are not tangent."""
    previous_out = None
    for e in elements:
        if e["type"] == "line":
            d = np.array([e["e1"] - e["e0"], e["n1"] - e["n0"]])
            b_in = bearing_of(d / max(np.hypot(*d), 1e-12)) if np.hypot(*d) > 1e-9 else exact_bearing(frame, e["start"], +1)
            e["k0"] = e["k1"] = 0.0
            e["b0"], e["b1"] = b_in, b_in
        else:
            if e["type"] == "curve":
                k0 = k1 = _curvature(e["radius"])
            else:
                k0, k1 = _curvature(e["radius_start"]), _curvature(e["radius_end"])
            sign = 1.0 if e["direction"] == "right" else -1.0
            e["k0"], e["k1"], e["sign"] = k0, k1, sign
            estimate = exact_bearing(frame, e["start"], +1)
            b_in = previous_out if previous_out is not None and abs((previous_out - estimate + 180) % 360 - 180) < 0.1 \
                else estimate
            e["b0"] = b_in % 360.0
            e["b1"] = (b_in + sign * math.degrees(e["length"] * (k0 + k1) / 2)) % 360.0
        previous_out = e["b1"]


def bearing_at(elements, station):
    """The exact bearing at ``station`` from the element it falls in."""
    e = next((e for e in elements if e["start"] - 1e-9 <= station <= e["end"] + 1e-9), elements[-1])
    if e["type"] == "line":
        return e["b0"]
    length = max(e["length"], 1e-12)
    l_ = min(max(station - e["start"], 0.0), length)
    turn = e["k0"] * l_ + (e["k1"] - e["k0"]) * l_ * l_ / (2 * length)
    return (e["b0"] + e["sign"] * math.degrees(turn)) % 360.0


class SettingOut:
    """All the tables for one alignment; see :func:`setting_out`."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def setting_out(path, alignment=None, northing_first=True, interval=20.0, profile=None, ground_profile=None,
                feature_lines=True, decimals=3):
    records, _bad = read_alignments(path, STEP)
    if alignment:
        record = next((r for r in records if r["name"] == alignment), None)
        if record is None:
            raise ValueError(f"Alignment '{alignment}' is not in the file: " + ", ".join(r["name"] for r in records))
    elif len(records) == 1:
        record = records[0]
    else:
        raise ValueError("Choose the alignment: " + ", ".join(r["name"] for r in records))
    frame = AlignmentFrame(record, northing_first)
    document = load_document(path)
    node = alignment_element(document.root, record["name"])
    profiles = read_alignment_profiles(node, 1.0)
    design, design_name = _profile_sampler(profiles, "design", profile)
    ground, ground_name = _profile_sampler(profiles, "surface", ground_profile)

    def point(station):
        xy, t, _n = frame.at([station])
        return xy[0], t[0]

    # ---- elements and key points
    elements = []
    keys = []
    elems = frame.elements()
    for k, (kind, s0, s1, radius, turn, r_start, r_end) in enumerate(elems):
        p0, _t0 = point(s0)
        p1, _t1 = point(s1)
        row = {"no": k + 1, "type": kind, "start": s0, "end": s1, "length": s1 - s0,
               "radius": radius if kind == "curve" else None,
               "radius_start": r_start if kind == "spiral" else None, "radius_end": r_end if kind == "spiral" else None,
               "direction": ("right" if turn > 0 else "left") if kind != "line" else "",
               "e0": p0[0], "n0": p0[1], "e1": p1[0], "n1": p1[1],
               "b0": exact_bearing(frame, s0, +1), "b1": exact_bearing(frame, s1, -1)}
        if kind == "spiral":
            rr = r_end or r_start
            row["A"] = math.sqrt(float(rr) * (s1 - s0)) if rr else None
        elements.append(row)
    _analytic_bearings(frame, elements)
    for k, e in enumerate(elements):
        prev = elements[k - 1]["type"] if k else None
        if k == 0:
            name = "BP"
        else:
            name = KEY_NAMES.get((prev, e["type"]))
            if name is None and prev == e["type"] == "curve":
                same = (elems[k - 1][4] == elems[k][4])
                name = "PCC" if same else "PRC"
            name = name or "TP"
        keys.append((e["start"], name))
    keys.append((elements[-1]["end"] if elements else frame.end, "EP"))

    # ---- intersection points (from the tangents, as the Plan Editor recovers them)
    pis = []
    try:
        from .plan_design import PlanDesign
        segs = []
        for seg in record["segments"]:
            seg = dict(seg)
            if northing_first:
                seg["points"] = [(p[1], p[0]) for p in seg["points"]]
            segs.append(seg)
        plan = PlanDesign.from_segments(segs, record["name"], frame.start)
        for i, (x, y) in enumerate(plan.pis):
            c = plan.curves[i - 1] if 0 < i < len(plan.pis) - 1 else None
            station, offset = frame.station_of(x, y)
            pis.append({"no": i + 1, "e": x, "n": y, "station": station, "offset": offset,
                        "deflection": plan.deflection(i) if c else None,
                        "radius": c["radius"] if c else None, "spiral_in": c["spiral_in"] if c else None,
                        "spiral_out": c["spiral_out"] if c else None})
    except (ValueError, KeyError, IndexError, ImportError):
        pis = []

    # ---- stations: the interval plus the key points
    first = math.ceil(frame.start / interval - 1e-9) * interval if interval > 0 else frame.start
    regular = list(np.arange(first, frame.end + 1e-9, interval)) if interval > 0 else []
    labels = {}
    for s, name in keys:
        labels.setdefault(round(s, 6), name)
    stations = sorted({round(float(s), 6) for s in regular} | set(labels) | {round(frame.start, 6), round(frame.end, 6)})
    xy, tangent, _normal = frame.at(stations)
    zd = design(stations) if design else np.full(len(stations), np.nan)
    zg = ground(stations) if ground else np.full(len(stations), np.nan)
    rows = []
    for i, s in enumerate(stations):
        rows.append({"station": s, "point": labels.get(s, ""), "e": xy[i, 0], "n": xy[i, 1],
                     "bearing": bearing_at(elements, s) if elements else exact_bearing(frame, s), "design": zd[i], "ground": zg[i],
                     "depth": zd[i] - zg[i] if np.isfinite(zd[i]) and np.isfinite(zg[i]) else np.nan})

    # ---- feature lines: points with chainage and offset, and offsets at the stations
    lines = []
    offsets = {}
    if feature_lines:
        found, _w = read_line_features(document.root)
        for f in found:
            if len(f.points) < 2:
                continue
            pts = np.array([(p[1], p[0], p[2] if len(p) > 2 else np.nan) if northing_first
                            else (p[0], p[1], p[2] if len(p) > 2 else np.nan) for p in f.points], float)
            so = np.array([frame.station_of(x, y) for x, y in pts[:, :2]])
            near = np.isfinite(so[:, 0]) & (np.abs(so[:, 1]) < 500.0)
            if not near.any():
                continue
            name = f.name or f.feature_code or "Feature line"
            lines.append({"name": name, "code": f.feature_code or f.description or "", "points": [
                {"no": k + 1, "e": p[0], "n": p[1], "z": p[2], "station": s_[0], "offset": s_[1]}
                for k, (p, s_) in enumerate(zip(pts, so))]})
            order = np.argsort(so[:, 0])
            st, off, z = so[order, 0], so[order, 1], pts[order, 2]
            keep = np.concatenate([[True], np.diff(st) > 1e-6])
            st, off, z = st[keep], off[keep], z[keep]
            if len(st) < 2:
                continue
            inside = [(i, s) for i, s in enumerate(stations) if st[0] - 1e-6 <= s <= st[-1] + 1e-6]
            col = {}
            for i, s in inside:
                col[i] = (float(np.interp(s, st, off)), float(np.interp(s, st, z)))
            offsets[name] = col
    return SettingOut(alignment=record["name"], frame=frame, elements=elements, keys=keys, pis=pis, rows=rows,
                      design_name=design_name, ground_name=ground_name, lines=lines, offsets=offsets,
                      declared_crs=document.declared_crs, unit=document.horizontal_unit or "", decimals=decimals,
                      source=path, interval=interval)


# ---------------------------------------------------------------------------------------------------------------
def _r(v, d=3):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    if isinstance(v, (int, np.integer)):
        return int(v)
    return round(float(v), d)


def sheets(so):
    """[(name, rows, widths)] for :func:`corridor_quantities.write_xlsx`."""
    from .reports import station_label
    d = so.decimals
    hu = so.unit or "meter"
    notes = [["Setting-out data", ""], ["Alignment", so.alignment], ["Source file", so.source],
             ["Coordinate system", so.declared_crs or "not declared in the file (confirm before use)"],
             ["Units", so.unit or "not declared"], ["Design profile", so.design_name or "none"],
             ["Ground profile", so.ground_name or "none"], ["Chainage interval", so.interval],
             ["Bearings", "whole-circle bearings from grid north, clockwise"],
             ["Coordinates", "easting, northing as stored in the file (not transformed)"],
             ["Check", "Verify against the design drawings and the survey control before setting out."]]
    elements = [["No", "Type", "Start ch", "End ch", "Length", "Radius", "R start", "R end", "A", "Turn", "Start E",
                 "Start N", "End E", "End N", "Bearing in", "Bearing out"]]
    for e in so.elements:
        elements.append([e["no"], e["type"], station_label(e["start"], hu, d), station_label(e["end"], hu, d),
                         _r(e["length"], d), _r(e["radius"], d), _r(e["radius_start"], d), _r(e["radius_end"], d),
                         _r(e.get("A"), d), e["direction"], _r(e["e0"], d), _r(e["n0"], d), _r(e["e1"], d), _r(e["n1"], d),
                         dms(e["b0"]), dms(e["b1"])])
    pis = [["PI", "Easting", "Northing", "Chainage (nearest)", "Deflection", "Radius", "Spiral in", "Spiral out"]]
    for p in so.pis:
        pis.append([p["no"], _r(p["e"], d), _r(p["n"], d), station_label(p["station"], hu, d),
                    f"{p['deflection']:+.4f}°" if p["deflection"] is not None else "", _r(p["radius"], d),
                    _r(p["spiral_in"], d), _r(p["spiral_out"], d)])
    stations = [["Chainage", "Point", "Easting", "Northing", "Bearing", "Design level", "Ground level", "Cut(−)/fill(+)"]]
    for r in so.rows:
        stations.append([station_label(r["station"], hu, d), r["point"], _r(r["e"], d), _r(r["n"], d),
                         dms(r["bearing"]), _r(r["design"], d), _r(r["ground"], d), _r(r["depth"], d)])
    names = list(so.offsets)
    wide = [["Chainage", "CL easting", "CL northing", "CL level"] + [f"{n} {x}" for n in names for x in ("offset", "level")]]
    for i, r in enumerate(so.rows):
        row = [station_label(r["station"], hu, d), _r(r["e"], d), _r(r["n"], d), _r(r["design"], d)]
        for n in names:
            o = so.offsets[n].get(i)
            row += [_r(o[0], d), _r(o[1], d)] if o else ["", ""]
        if any(c != "" for c in row[4:]) or not names:
            wide.append(row)
    points = [["Feature line", "Code", "Vertex", "Easting", "Northing", "Level", "Chainage", "Offset"]]
    for line in so.lines:
        for p in line["points"]:
            points.append([line["name"], line["code"], p["no"], _r(p["e"], d), _r(p["n"], d), _r(p["z"], d),
                           station_label(p["station"], hu, d), _r(p["offset"], d)])
    out = [("Notes", notes, [24, 70]), ("Elements", elements, [6, 9, 12, 12, 11, 11, 10, 10, 10, 7, 14, 14, 14, 14, 13, 13]),
           ("Key points and PIs", [["Key point", "Chainage", "Easting", "Northing"]]
            + [[name, station_label(s, hu, d)] + [_r(v, d) for v in so.frame.at([s])[0][0]] for s, name in so.keys]
            + [[]] + pis, [12, 14, 14, 14, 14, 12, 12, 12]),
           ("Stations", stations, [13, 7, 14, 14, 13, 12, 12, 13])]
    if names:
        out.append(("Offsets at stations", wide, [13, 14, 14, 11] + [12] * (2 * len(names))))
    if so.lines:
        out.append(("Feature line points", points, [26, 10, 7, 14, 14, 11, 13, 11]))
    return out


def write_csv(path, so):
    """Points for a total station or data collector: name, easting, northing, level, description (PENZD)."""
    from .reports import station_label
    hu = so.unit or "meter"
    with open(path, "w", newline="", encoding="utf-8") as stream:
        w = csv.writer(stream)
        w.writerow(["Point", "Easting", "Northing", "Level", "Description"])
        for k, r in enumerate(so.rows):
            w.writerow([f"CL{k + 1}", _r(r["e"], so.decimals), _r(r["n"], so.decimals), _r(r["design"], so.decimals),
                        f"CL {station_label(r['station'], hu, so.decimals)} {r['point']}".strip()])
        for line in so.lines:
            for p in line["points"]:
                w.writerow([f"{line['code'] or 'F'}{p['no']}", _r(p["e"], so.decimals), _r(p["n"], so.decimals),
                            _r(p["z"], so.decimals), f"{line['name']} ch {station_label(p['station'], hu, so.decimals)} "
                                                     f"off {p['offset']:+.3f}"])


def report_html(so, title=None):
    from .reports import _cards, _document, _table, station_label
    hu = so.unit or "meter"
    d = so.decimals
    u = "m" if (so.unit or "meter").startswith("met") else "ft"
    counts = {}
    for e in so.elements:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    body = [_cards([("Alignment length", f"{so.frame.end - so.frame.start:,.3f} {u}"),
                    ("Chainage", f"{station_label(so.frame.start, hu)} – {station_label(so.frame.end, hu)}"),
                    ("Elements", ", ".join(f"{v} {k}s" for k, v in counts.items())),
                    ("Setting-out points", f"{len(so.rows):,d}"), ("Feature lines", f"{len(so.lines)}")])]
    body.append("<p class='note'>Coordinates as stored in the file (" + (so.declared_crs or "CRS not declared")
                + "), easting before northing; whole-circle bearings from grid north. Verify against the design "
                "drawings and the survey control before setting out.</p>")
    body.append("<h2>Horizontal geometry</h2>" + _table(
        ["No", "Type", "Start ch", "End ch", "Length", "Radius / A", "Turn", "Start E", "Start N", "Bearing in"],
        [(e["no"], e["type"], station_label(e["start"], hu, d), station_label(e["end"], hu, d), f"{e['length']:.{d}f}",
          (f"R {e['radius']:.{d}f}" if e["radius"] else (f"A {e['A']:.{d}f}" if e.get("A") else "–")),
          e["direction"] or "–", f"{e['e0']:.{d}f}", f"{e['n0']:.{d}f}", dms(e["b0"])) for e in so.elements]))
    body.append("<h2>Key points</h2>" + _table(
        ["Point", "Chainage", "Easting", "Northing"],
        [(name, station_label(s, hu, d), *(f"{v:.{d}f}" for v in so.frame.at([s])[0][0])) for s, name in so.keys]))
    if so.pis:
        body.append("<h2>Intersection points</h2>" + _table(
            ["PI", "Easting", "Northing", "Deflection", "Radius", "Spiral in", "Spiral out"],
            [(p["no"], f"{p['e']:.{d}f}", f"{p['n']:.{d}f}",
              f"{p['deflection']:+.4f}°" if p["deflection"] is not None else "–",
              f"{p['radius']:.{d}f}" if p["radius"] else "–", f"{p['spiral_in']:g}" if p["spiral_in"] else "–",
              f"{p['spiral_out']:g}" if p["spiral_out"] else "–") for p in so.pis]))
    body.append(f"<h2>Centreline setting-out every {so.interval:g} {u}</h2><div class='scroll'>" + _table(
        ["Chainage", "Point", "Easting", "Northing", "Bearing", "Design", "Ground", "Cut/fill"],
        [(station_label(r["station"], hu, d), r["point"], f"{r['e']:.{d}f}", f"{r['n']:.{d}f}", dms(r["bearing"]),
          f"{r['design']:.{d}f}" if np.isfinite(r["design"]) else "–",
          f"{r['ground']:.{d}f}" if np.isfinite(r["ground"]) else "–",
          f"{r['depth']:+.{d}f}" if np.isfinite(r["depth"]) else "–") for r in so.rows]) + "</div>")
    if so.offsets:
        names = list(so.offsets)
        body.append("<h2>Feature lines: offset / level at each chainage</h2><p class='note'>Offsets are right (+) or "
                    "left (−) of the centreline, square to it; shown five lines to a table (all of them are in the "
                    "workbook's <i>Offsets at stations</i> sheet).</p>")
        for g in range(0, len(names), 5):
            group = names[g:g + 5]
            rows = []
            for i, r in enumerate(so.rows):
                cells = [station_label(r["station"], hu, d)]
                for n in group:
                    o = so.offsets[n].get(i)
                    cells.append(f"{o[0]:+.{d}f} / {o[1]:.{d}f}" if o else "–")
                if any(c != "–" for c in cells[1:]):
                    rows.append(cells)
            if rows:
                body.append(f"<h3>{html.escape(', '.join(group))}</h3><div class='scroll'>"
                            + _table(["Chainage"] + group, rows) + "</div>")
    return _document(title or f"Setting-out data – {so.alignment}", "".join(body),
                     meta=[("Alignment", so.alignment), ("Source", so.source.replace("\\", "/").rsplit("/", 1)[-1]),
                           ("CRS", so.declared_crs or "not declared")],
                     kicker="Setting-out data")


__all__ = ["setting_out", "sheets", "write_csv", "report_html", "dms"]
