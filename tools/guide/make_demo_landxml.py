"""Write a synthetic Civil 3D-style LandXML road for the user guide figures.

Everything is invented (coordinates are written northing first, as Civil 3D does): the names, the local coordinates and an analytic
terrain. The road has tangents, clothoid spirals and circular curves, a
design profile with crests and sags, an existing-ground profile and TIN,
Civil 3D speed stations, superelevation and corridor cross-sections with
pavement layers and daylighted side slopes.

    python tools/guide/make_demo_landxml.py out/demo_road.xml
"""

import math
import sys

X0, Y0 = 10000.0, 20000.0
LANE, SHOULDER = 3.5, 1.5
CROWN, SHOULDER_SLOPE = 0.025, 0.04
FILL_SLOPE, CUT_SLOPE, DITCH = 2.0, 1.5, 1.0


def ground(x, y):
    u, v = x - X0, y - Y0
    return (1500.0 + 18.0 * math.sin(u / 320.0) + 14.0 * math.cos(v / 270.0) + 7.0 * math.sin((u + v) / 140.0)
            - 9.0 * math.exp(-((u - 1450.0) ** 2 + (v - 900.0) ** 2) / 90000.0))


# (kind, length, radius, turn) with turn +1 = right (cw), -1 = left (ccw)
PLAN = [
    ("line", 450, None, 0),
    ("spiral_in", 70, 300, 1), ("curve", 190, 300, 1), ("spiral_out", 70, 300, 1),
    ("line", 110, None, 0),
    ("spiral_in", 60, 220, -1), ("curve", 140, 220, -1), ("spiral_out", 60, 220, -1),
    ("line", 640, None, 0),
    ("curve", 150, 140, 1),
    ("line", 330, None, 0),
    ("spiral_in", 50, 450, -1), ("curve", 230, 450, -1), ("spiral_out", 50, 450, -1),
    ("line", 420, None, 0),
]


def march():
    """Walk the plan; returns element records with coordinates and a dense centreline."""
    x, y, heading, station = X0, Y0, math.radians(60.0), 0.0
    elements, dense = [], [(0.0, x, y, heading)]
    for kind, length, radius, turn in PLAN:
        start = (x, y, heading, station)
        steps = max(int(length / 0.5), 1)
        ds = length / steps
        for i in range(steps):
            s_mid = (i + 0.5) * ds
            if kind == "line":
                k = 0.0
            elif kind == "curve":
                k = 1.0 / radius
            elif kind == "spiral_in":
                k = s_mid / length / radius
            else:
                k = (1.0 - s_mid / length) / radius
            heading_mid = heading - turn * k * ds / 2.0
            x += ds * math.cos(heading_mid)
            y += ds * math.sin(heading_mid)
            heading -= turn * k * ds
            station += ds
            dense.append((station, x, y, heading))
        elements.append({"kind": kind, "length": length, "radius": radius, "turn": turn, "start": start, "end": (x, y, heading, station)})
    return elements, dense


def fmt(*values):
    return " ".join(f"{v:.4f}" for v in values)


def ne(x, y, *rest):
    """Civil 3D stores plan coordinates northing first."""
    return fmt(y, x, *rest)


def coord_geom(elements):
    out = []
    for e in elements:
        sx, sy, sh, ss = e["start"]
        ex, ey, eh, _es = e["end"]
        rot = "cw" if e["turn"] > 0 else "ccw"
        if e["kind"] == "line":
            out.append(f'<Line staStart="{ss:.4f}" length="{e["length"]:.4f}"><Start>{ne(sx, sy)}</Start><End>{ne(ex, ey)}</End></Line>')
        elif e["kind"] == "curve":
            r = e["radius"]
            cx = sx + r * math.cos(sh - e["turn"] * math.pi / 2.0)
            cy = sy + r * math.sin(sh - e["turn"] * math.pi / 2.0)
            out.append(f'<Curve rot="{rot}" staStart="{ss:.4f}" length="{e["length"]:.4f}" radius="{r:.4f}">'
                       f'<Start>{ne(sx, sy)}</Start><Center>{ne(cx, cy)}</Center><End>{ne(ex, ey)}</End></Curve>')
        else:
            # PI: intersection of the start and end tangents.
            det = math.cos(sh) * math.sin(eh) - math.sin(sh) * math.cos(eh)
            t = ((ex - sx) * math.sin(eh) - (ey - sy) * math.cos(eh)) / det
            px, py = sx + t * math.cos(sh), sy + t * math.sin(sh)
            rs, re = ("INF", f"{e['radius']:.4f}") if e["kind"] == "spiral_in" else (f"{e['radius']:.4f}", "INF")
            out.append(f'<Spiral spiType="clothoid" rot="{rot}" staStart="{ss:.4f}" length="{e["length"]:.4f}" radiusStart="{rs}" radiusEnd="{re}">'
                       f'<Start>{ne(sx, sy)}</Start><PI>{ne(px, py)}</PI><End>{ne(ex, ey)}</End></Spiral>')
    return out


def at_station(dense, station):
    lo, hi = 0, len(dense) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if dense[mid][0] <= station:
            lo = mid
        else:
            hi = mid
    s0, x0, y0, h0 = dense[lo]
    s1, x1, y1, h1 = dense[hi]
    t = 0.0 if s1 == s0 else (station - s0) / (s1 - s0)
    return x0 + t * (x1 - x0), y0 + t * (y1 - y0), h0 + t * (h1 - h0)


def ground_at(dense, station, offset=0.0):
    x, y, h = at_station(dense, station)
    # Positive offset is to the right of the direction of travel.
    return ground(x + offset * math.sin(h), y - offset * math.cos(h))


class Profile:
    def __init__(self, pvis):
        self.pvis = pvis  # (station, elevation, curve length)

    def elevation(self, s):
        p = self.pvis
        for i in range(1, len(p) - 1):
            st, z, length = p[i]
            g1 = (z - p[i - 1][1]) / (st - p[i - 1][0])
            g2 = (p[i + 1][1] - z) / (p[i + 1][0] - st)
            if length and st - length / 2 <= s <= st + length / 2:
                x = s - (st - length / 2)
                return z - g1 * length / 2 + g1 * x + (g2 - g1) * x * x / (2 * length)
        for i in range(len(p) - 1):
            if p[i][0] <= s <= p[i + 1][0]:
                g = (p[i + 1][1] - p[i][1]) / (p[i + 1][0] - p[i][0])
                return p[i][1] + g * (s - p[i][0])
        return p[-1][1]


def design_profile(dense, total):
    stations = [0, 400, 820, 1240, 1650, 2050, 2450, 2800, total]
    lengths = [0, 160, 200, 90, 240, 160, 220, 180, 0]
    lifts = [0.5, 3.5, -4.0, 9.5, -2.0, 4.5, -3.5, 2.0, 0.5]
    return Profile([(s, round(ground_at(dense, s) + d, 3), L) for s, L, d in zip(stations, lengths, lifts)])


def superelevation(elements):
    """Full rates on curves (negative = left curve), with run-off over the spirals or 40 m."""
    records, out = [], []
    for i, e in enumerate(elements):
        if e["kind"] != "curve":
            continue
        rate = (6.0 if e["radius"] < 250 else 4.0 if e["radius"] < 400 else 3.0) * (1 if e["turn"] > 0 else -1)
        start, end = e["start"][3], e["end"][3]
        spiral_before = elements[i - 1]["kind"] == "spiral_in"
        spiral_after = i + 1 < len(elements) and elements[i + 1]["kind"] == "spiral_out"
        runoff_in = elements[i - 1]["length"] if spiral_before else 40.0
        runoff_out = elements[i + 1]["length"] if spiral_after else 40.0
        begin_runoff = start - runoff_in if spiral_before else start - 0.67 * runoff_in
        full_start = start if spiral_before else begin_runoff + runoff_in
        end_full = end if spiral_after else end + 0.33 * runoff_out - runoff_out
        end_runoff = end_full + runoff_out
        runout = 20.0
        records.append((begin_runoff, full_start, end_full, end_runoff, rate))
        out.append(
            f'<Superelevation staStart="{begin_runoff - runout:.2f}" staEnd="{end_runoff + runout:.2f}">'
            f"<BeginRunoutSta>{begin_runoff - runout:.2f}</BeginRunoutSta><BeginRunoffSta>{begin_runoff:.2f}</BeginRunoffSta>"
            f"<FullSuperSta>{full_start:.2f}</FullSuperSta><FullSuperelev>{rate:.2f}</FullSuperelev>"
            f"<RunoffSta>{end_full:.2f}</RunoffSta><StartofRunoutSta>{end_runoff:.2f}</StartofRunoutSta>"
            f"<EndofRunoutSta>{end_runoff + runout:.2f}</EndofRunoutSta></Superelevation>"
        )
    return records, out


def cross_slopes(records, station):
    """(left, right) lane slopes in %, positive up from the centreline."""
    for begin, full, end_full, end, rate in records:
        if begin - 20 <= station <= end + 20:
            if full <= station <= end_full:
                f = 1.0
            elif begin <= station < full:
                f = (station - begin) / (full - begin)
            elif end_full < station <= end:
                f = (end - station) / (end - end_full)
            else:
                f = 0.0
            outside = -CROWN * 100 + f * (abs(rate) + CROWN * 100)
            inside = -max(CROWN * 100, f * abs(rate))
            return (outside, inside) if rate > 0 else (inside, outside)
    return -CROWN * 100, -CROWN * 100


def side(dense, station, sign, edge_offset, edge_z):
    """Daylight from the shoulder edge outward (sign -1 left, +1 right). Returns points and slope type."""
    pts = []
    g = ground_at(dense, station, sign * edge_offset)
    if g < edge_z:  # fill
        o, z = edge_offset, edge_z
        while z > ground_at(dense, station, sign * o) and o < edge_offset + 80:
            o += 0.25
            z = edge_z - (o - edge_offset) / FILL_SLOPE
        pts.append((o, ground_at(dense, station, sign * o)))
    else:  # cut with a ditch
        o, z = edge_offset + DITCH, edge_z - 0.6
        pts.append((o, z))
        while z < ground_at(dense, station, sign * o) and o < edge_offset + 80:
            o += 0.25
            z = edge_z - 0.6 + (o - edge_offset - DITCH) / CUT_SLOPE
        pts.append((o, ground_at(dense, station, sign * o)))
    return pts


def sections(dense, profile, records, total, step=20.0):
    out = []
    station = 0.0
    while station <= total + 1e-6:
        zc = profile.elevation(station)
        left, right = cross_slopes(records, station)
        edge_l = zc + LANE * left / 100.0
        edge_r = zc + LANE * right / 100.0
        sh_l = edge_l - SHOULDER * SHOULDER_SLOPE
        sh_r = edge_r - SHOULDER * SHOULDER_SLOPE
        w = LANE + SHOULDER
        lpts = side(dense, station, -1, w, sh_l)
        rpts = side(dense, station, 1, w, sh_r)
        top = [(-o, z) for o, z in reversed(lpts)] + [(-w, sh_l), (-LANE, edge_l), (0.0, zc), (LANE, edge_r), (w, sh_r)] + rpts
        reach = max(abs(top[0][0]), abs(top[-1][0])) + 15.0
        eg = []
        o = -reach
        while o <= reach + 1e-6:
            eg.append((o, ground_at(dense, station, o)))
            o += 2.0
        name = f"{int(station // 1000)}+{station % 1000:06.2f}"
        layers = []
        for layer, top_depth, bottom_depth, half in (("Asphalt", 0.0, 0.05, LANE), ("Base", 0.05, 0.25, LANE + 0.3), ("Subbase", 0.25, 0.45, LANE + 0.6)):
            pl = [(-half, left / 100 * half - top_depth), (0, -top_depth), (half, right / 100 * half - top_depth),
                  (half, right / 100 * half - bottom_depth), (0, -bottom_depth), (-half, left / 100 * half - bottom_depth),
                  (-half, left / 100 * half - top_depth)]
            layers.append(f'<DesignCrossSectSurf name="{layer}" side="both" closedArea="true">'
                          + "".join(f"<CrossSectPnt>{o:.3f} {z:.3f}</CrossSectPnt>" for o, z in pl) + "</DesignCrossSectSurf>")
        datum = (f'<DesignCrossSectSurf name="Datum"><CrossSectPnt>{-LANE - 0.6:.3f} {left / 100 * (LANE + 0.6) - 0.45:.3f}</CrossSectPnt>'
                 f"<CrossSectPnt>0 -0.45</CrossSectPnt><CrossSectPnt>{LANE + 0.6:.3f} {right / 100 * (LANE + 0.6) - 0.45:.3f}</CrossSectPnt></DesignCrossSectSurf>")
        out.append(
            f'<CrossSect sta="{station:.2f}" name="{name}">'
            f'<CrossSectSurf name="EG"><PntList2D>{" ".join(f"{o:.3f} {z:.3f}" for o, z in eg)}</PntList2D></CrossSectSurf>'
            f'<CrossSectSurf name="Demo Corridor TOP"><PntList2D>{" ".join(f"{o:.3f} {z:.3f}" for o, z in top)}</PntList2D></CrossSectSurf>'
            + "".join(layers) + datum + "</CrossSect>"
        )
        station += step
    return out


def tin(dense, spacing=25.0, margin=250.0):
    xs = [p[1] for p in dense]
    ys = [p[2] for p in dense]
    x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    nx, ny = int((x1 - x0) / spacing) + 1, int((y1 - y0) / spacing) + 1
    pnts, faces = [], []
    for j in range(ny):
        for i in range(nx):
            x, y = x0 + i * spacing, y0 + j * spacing
            pnts.append(f'<P id="{j * nx + i + 1}">{ne(x, y, ground(x, y))}</P>')
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i + 1
            faces.append(f"<F>{a} {a + 1} {a + nx + 1}</F><F>{a} {a + nx + 1} {a + nx}</F>")
    return pnts, faces


def main(path):
    elements, dense = march()
    total = dense[-1][0]
    profile = design_profile(dense, total)
    records, superel = superelevation(elements)
    eg_profile = " ".join(f"{s:.2f} {ground_at(dense, s):.3f}" for s in [i * 10.0 for i in range(int(total // 10) + 1)] + [total])
    pvis = []
    for i, (s, z, length) in enumerate(profile.pvis):
        pvis.append(f"<ParaCurve length=\"{length:.1f}\">{s:.3f} {z:.3f}</ParaCurve>" if length else f"<PVI>{s:.3f} {z:.3f}</PVI>")
    pnts, faces = tin(dense)
    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Synthetic demonstration road for the user guide: invented names, coordinates and terrain. -->",
        '<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="2026-10-07" time="12:00:00">',
        '<Units><Metric linearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter" angularUnit="decimal degrees" directionUnit="decimal degrees"/></Units>',
        '<Application name="Autodesk Civil 3D" manufacturer="Autodesk, Inc." version="2026"/>',
        f'<Surfaces><Surface name="EG" desc="Synthetic existing ground"><Definition surfType="TIN"><Pnts>{"".join(pnts)}</Pnts><Faces>{"".join(faces)}</Faces></Definition></Surface></Surfaces>',
        "<Alignments>",
        f'<Alignment name="Demo Road" staStart="0" length="{total:.4f}" desc="Synthetic road">',
        "<CoordGeom>" + "".join(coord_geom(elements)) + "</CoordGeom>",
        '<Profile name="Demo Road">',
        f'<ProfSurf name="Demo Road_EG" state="existing"><PntList2D>{eg_profile}</PntList2D></ProfSurf>',
        '<ProfAlign name="Demo Road FRL">' + "".join(pvis) + "</ProfAlign>",
        "</Profile>",
        "<CrossSects>" + "".join(sections(dense, profile, records, total)) + "</CrossSects>",
        "".join(superel),
        '<Feature name="SpeedStation" code="0" source="Autodesk Civil 3D"><Property label="station" value="0"/><Property label="speed" value="80"/></Feature>',
        '<Feature name="SpeedStation" code="1" source="Autodesk Civil 3D"><Property label="station" value="1900"/><Property label="speed" value="60"/></Feature>',
        "</Alignment>",
        "</Alignments>",
        "</LandXML>",
    ]
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(xml) + "\n")
    print(f"{path}: {total:.1f} m, {len(elements)} elements, {len(pnts)} TIN points")


if __name__ == "__main__":
    main(sys.argv[1])
