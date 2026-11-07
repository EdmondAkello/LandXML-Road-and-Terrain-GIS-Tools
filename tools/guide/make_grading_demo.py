"""Write a synthetic Civil 3D-style site-grading LandXML for the user-guide figures.

Everything is invented: local coordinates (northing first, as Civil 3D writes
them), an analytic hillside, two platforms with 1:2 batters, a pond and an
earlier revision. Surfaces written:

* ``Demo EG``: existing ground;
* ``Demo FG``: finished grade over the graded footprint (platform A level, platform B falling 1 % east, pond in A);
* ``Demo Earthworks``: volume surface, FG minus EG over the graded footprint;
* ``Demo Earthworks Rev A``: the same for an earlier revision with platform B 0.5 m higher;
* ``Demo Pond``: the pond basin and its rim;
* ``Platform A Earthworks`` / ``Platform B Earthworks``: the volume surface split at x = 76 m, one per site.

    python tools/guide/make_grading_demo.py out/demo_grading.xml
"""

import math
import sys

X0, Y0 = 50000.0, 80000.0          # local origin (easting, northing)
STEP = 2.0
NX, NY = 81, 51                    # 160 m x 100 m
BATTER = 0.5                       # 1V:2H
A = (12.0, 18.0, 66.0, 82.0)       # platform A extents (x0, y0, x1, y1)
B = (86.0, 22.0, 146.0, 78.0)      # platform B extents
POND = (18.0, 26.0, 32.0, 40.0)    # pond floor, inside A
LEVEL_A = 1003.5
POND_DEPTH = 2.0


def ground(x, y):
    return 1000.0 + 0.06 * x + 0.035 * y + 1.6 * math.sin(x / 23.0) + 1.2 * math.cos(y / 17.0)


def level_b(x, offset=0.0):
    return 1009.0 + offset - 0.01 * (x - B[0])


def _distance(x, y, box):
    x0, y0, x1, y1 = box
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return math.hypot(dx, dy)


def finished(x, y, offset=0.0):
    eg = ground(x, y)
    candidates = []
    for box, level in ((A, LEVEL_A), (B, level_b(min(max(x, B[0]), B[2]), offset))):
        d = _distance(x, y, box)
        reach = abs(eg - level) / BATTER
        if d <= reach + 1e-9:
            z = level + max(-BATTER * d, min(BATTER * d, eg - level)) if d > 0 else level
            candidates.append((d, z))
    if not candidates:
        return eg
    z = min(candidates)[1]
    # pond: 1:1 sides down to a flat floor inside platform A
    d = _distance(x, y, POND)
    if d < POND_DEPTH:
        z = min(z, LEVEL_A - POND_DEPTH + d)
    return z


def _grid(fn):
    pts = []
    for j in range(NY):
        for i in range(NX):
            x, y = i * STEP, j * STEP
            pts.append((x, y, fn(x, y)))
    faces = []
    for j in range(NY - 1):
        for i in range(NX - 1):
            a = j * NX + i
            b, c, d = a + 1, a + NX + 1, a + NX
            faces += [(a, b, c), (a, c, d)] if (i + j) % 2 == 0 else [(a, b, d), (b, c, d)]
    return pts, faces


def _surface(name, pts, faces, keep=None):
    faces = [f for f in faces if keep is None or keep(f)]
    used = sorted({v for f in faces for v in f})
    ids = {v: k + 1 for k, v in enumerate(used)}
    p = "".join(f'<P id="{ids[v]}">{Y0 + pts[v][1]:.3f} {X0 + pts[v][0]:.3f} {pts[v][2]:.4f}</P>' for v in used)
    f = "".join(f"<F>{ids[a]} {ids[b]} {ids[c]}</F>" for a, b, c in faces)
    return (f'    <Surface name="{name}">\n      <Definition surfType="TIN">\n        <Pnts>{p}</Pnts>\n'
            f'        <Faces>{f}</Faces>\n      </Definition>\n    </Surface>\n')


def build():
    eg, faces = _grid(ground)
    fg, _ = _grid(finished)
    rev_a, _ = _grid(lambda x, y: finished(x, y, 0.5))
    depth = [(x, y, z - e) for (x, y, z), (_, _, e) in zip(fg, eg)]
    depth_a = [(x, y, z - e) for (x, y, z), (_, _, e) in zip(rev_a, eg)]
    graded = lambda d: (lambda f: any(abs(d[v][2]) > 1e-9 for v in f))  # noqa: E731
    pond = lambda f: all(_distance(fg[v][0], fg[v][1], POND) <= POND_DEPTH + STEP for v in f)  # noqa: E731
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<!-- Synthetic site-grading model for the user guide (all values invented). -->
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
  <Units><Metric linearUnit="meter" verticalLinearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter"/></Units>
  <Application name="Synthetic grading demo" manufacturer="Autodesk, Inc."/>
  <Surfaces>
""" + _surface("Demo EG", eg, faces) + _surface("Demo FG", fg, faces, graded(depth))
        + _surface("Demo Earthworks", depth, faces, graded(depth))
        + _surface("Demo Earthworks Rev A", depth_a, faces, graded(depth_a))
        + _surface("Demo Pond", fg, faces, pond)
        + _surface("Platform A Earthworks", depth, faces, lambda f: graded(depth)(f) and _centre_x(fg, f) < 76.0)
        + _surface("Platform B Earthworks", depth, faces, lambda f: graded(depth)(f) and _centre_x(fg, f) >= 76.0)
        + "  </Surfaces>\n</LandXML>\n")


def _centre_x(pts, face):
    return sum(pts[v][0] for v in face) / 3.0


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8") as stream:
        stream.write(build())
