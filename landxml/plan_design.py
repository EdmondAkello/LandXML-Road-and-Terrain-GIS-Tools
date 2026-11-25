"""Interactive horizontal-alignment design: the editing model behind the plan editor (pure Python, no Qt).

A design is a list of intersection points (PIs) in map order with a curve (radius and clothoid spirals in and out)
at every interior PI. Every edit refits the alignment with :func:`writer.alignment_from_pis`; a change that cannot be
fitted (curves that overlap, spirals longer than the deflection allows) is refused with the reason, so the design
always stays buildable. Edits can be undone. ``checks`` compares the fit with the design criteria (advisory).
"""

from __future__ import annotations

import copy
import math

from .writer import Curve, Line, PICurve, Spiral, alignment_from_pis

MIN_GAP = 1.0


class PlanDesign:
    def __init__(self, pis, curves=None, name="Design alignment", start_station=0.0):
        self.name = name
        self.start_station = float(start_station)
        self.pis = [[float(x), float(y)] for x, y in pis]
        n = max(len(self.pis) - 2, 0)
        self.curves = [dict(c) if c else None for c in (curves or [None] * n)]
        if len(self.curves) != n:
            raise ValueError("Give a curve (or None) for each interior PI.")
        self.default_radius = 200.0
        self.levels = None                  # optional (x, y, z) per PI, from a feature line: the design profile
        self.alignment = None
        self.error = ""
        self._undo, self._redo = [], []
        self.refit()

    # ------------------------------------------------------------------ build
    @classmethod
    def from_polyline(cls, points, radius=200.0, name="Design alignment"):
        """Every vertex a PI; interior PIs get ``radius`` (shrunk where it does not fit)."""
        pts = [p for k, p in enumerate(points) if k == 0 or math.dist(p, points[k - 1]) > 1e-6]
        design = cls(pts, [None] * max(len(pts) - 2, 0), name)
        design.default_radius = radius
        for i in range(1, len(pts) - 1):
            design.curves[i - 1] = {"radius": radius, "spiral_in": 0.0, "spiral_out": 0.0}
        design.fit_radii()
        return design

    @classmethod
    def from_segments(cls, segments, name="Design alignment", start_station=0.0):
        """PIs from the elements of a LandXML alignment (``geometry.read_alignments`` segments): tangent lines are
        extended to meet; each group of spiral, curve, spiral between two tangents becomes a PI with its radius and
        spiral lengths. The ends are the alignment's ends."""
        lines = []
        groups = [[]]
        for seg in segments:
            if seg["geometry_type"] == "line":
                lines.append((seg["points"][0], seg["points"][-1]))
                groups.append([])
            else:
                groups[-1].append(seg)
        first = segments[0]["points"][0]
        last = segments[-1]["points"][-1]
        pis = [list(first)]
        curves = []
        for k in range(len(lines) - 1):
            a, b = lines[k], lines[k + 1]
            pi = _intersection(a[0], a[1], b[0], b[1])
            group = groups[k + 1]
            if pi is None or not group:
                continue
            radius = next((g["radius"] for g in group if g["geometry_type"] == "curve" and g.get("radius")), None)
            if radius is None:
                radius = min((g.get("radius_end") or g.get("radius_start") or 1e9) for g in group)
            spirals = [g for g in group if g["geometry_type"] == "spiral"]
            sp_in = next((g["spiral_length"] for g in spirals if g.get("radius_start") is None), 0.0)
            sp_out = next((g["spiral_length"] for g in spirals if g.get("radius_end") is None), 0.0)
            pis.append(list(pi))
            curves.append({"radius": float(radius), "spiral_in": float(sp_in or 0.0), "spiral_out": float(sp_out or 0.0)})
        pis.append(list(last))
        return cls(pis, curves, name, start_station)

    # ------------------------------------------------------------------ state
    def state(self):
        return copy.deepcopy((self.pis, self.curves))

    def mark(self):
        return len(self._undo)

    def _push(self):
        self._undo.append(self.state())
        self._redo = []

    def _restore(self, state):
        self.pis, self.curves = copy.deepcopy(state)
        self.refit()

    def undo(self):
        if self._undo:
            self._redo.append(self.state())
            self._restore(self._undo.pop())
            return True
        return False

    def redo(self):
        if self._redo:
            self._undo.append(self.state())
            self._restore(self._redo.pop())
            return True
        return False

    def squash(self, mark):
        """Keep one undo step for everything done since ``mark`` (a drag)."""
        if len(self._undo) > mark + 1:
            del self._undo[mark + 1:]

    def refit(self):
        try:
            curves = [PICurve(c["radius"], c.get("spiral_in", 0.0), c.get("spiral_out", 0.0)) if c else None
                      for c in self.curves]
            self.alignment = alignment_from_pis(self.name, self.pis, curves, self.start_station) if len(self.pis) >= 2 else None
            self.error = ""
        except ValueError as error:
            self.alignment = None
            self.error = str(error)
        return self.alignment

    def _try(self, change):
        """Apply ``change`` as one undo step; refuse it (and keep the old design) when it cannot be fitted."""
        before = self.state()
        change()
        if len(self.pis) >= 2 and self.refit() is None:
            message = self.error
            self.pis, self.curves = before
            self.refit()
            raise ValueError(message)
        self._undo.append(before)
        self._redo = []

    # ------------------------------------------------------------------ edits
    def move(self, index, x, y):
        def change():
            self.pis[index] = [float(x), float(y)]
        self._try(change)

    def add(self, x, y, index=None, radius=None):
        """Insert a PI at ``index`` (None: at the end; the nearest leg is used by :meth:`insert_near`)."""
        curve = {"radius": float(radius or self.default_radius), "spiral_in": 0.0, "spiral_out": 0.0}

        def change():
            i = len(self.pis) if index is None else index
            self.pis.insert(i, [float(x), float(y)])
            if len(self.pis) >= 3:
                ci = min(max(i - 1, 0), len(self.pis) - 3)
                if i >= len(self.pis) - 1:          # a new end: the old end becomes interior
                    self.curves.append(curve)
                elif i == 0:
                    self.curves.insert(0, curve)
                else:
                    self.curves.insert(ci, curve)
            for _ in range(80):                     # a new curve that does not fit is made smaller until it does
                if len(self.pis) < 3 or self.refit() is not None or curve["radius"] < 1.0:
                    break
                curve["radius"] = round(curve["radius"] * 0.9, 1)
        self._try(change)
        return len(self.pis) - 1 if index is None else index

    def insert_near(self, x, y, radius=None):
        """Insert a PI into the leg nearest to (x, y)."""
        best = None
        for i in range(len(self.pis) - 1):
            d = _point_segment(x, y, self.pis[i], self.pis[i + 1])
            if best is None or d < best[0]:
                best = (d, i + 1)
        return self.add(x, y, best[1] if best else None, radius)

    def delete(self, index):
        if len(self.pis) <= 2:
            raise ValueError("An alignment needs two points.")

        def change():
            self.pis.pop(index)
            if self.curves:
                self.curves.pop(min(max(index - 1, 0), len(self.curves) - 1))
        self._try(change)

    def set_curve(self, index, radius=None, spiral_in=None, spiral_out=None):
        """Set the curve at interior PI ``index`` (1 .. n-2)."""
        def change():
            c = self.curves[index - 1] or {"radius": self.default_radius, "spiral_in": 0.0, "spiral_out": 0.0}
            if radius is not None:
                c["radius"] = float(radius)
            if spiral_in is not None:
                c["spiral_in"] = float(spiral_in)
            if spiral_out is not None:
                c["spiral_out"] = float(spiral_out)
            self.curves[index - 1] = c
        self._try(change)

    def fit_radii(self, factor=0.95):
        """Shrink any radius that does not fit between its neighbours (used for designs made from lines)."""
        for _ in range(60):
            if self.refit() is not None:
                return True
            for c in self.curves:
                if c:
                    c["radius"] *= factor
        return self.refit() is not None

    # ------------------------------------------------------------------ reading
    def elements(self):
        """[{kind, start, end, length, radius, pi}] of the fitted alignment (stations)."""
        if self.alignment is None:
            return []
        out = []
        for item in self.alignment.layout():
            e = item["element"]
            kind = "line" if isinstance(e, Line) else "curve" if isinstance(e, Curve) else "spiral"
            radius = e.radius if isinstance(e, Curve) else (e.radius_end or e.radius_start if isinstance(e, Spiral) else None)
            out.append({"kind": kind, "start": item["station"], "end": item["station"] + e.length, "length": e.length,
                        "radius": radius, "turn": getattr(e, "turn", 0)})
        return out

    def deflection(self, index):
        (x0, y0), (x1, y1), (x2, y2) = self.pis[index - 1], self.pis[index], self.pis[index + 1]
        a = math.atan2(y1 - y0, x1 - x0)
        b = math.atan2(y2 - y1, x2 - x1)
        return math.degrees(math.atan2(math.sin(b - a), math.cos(b - a)))

    def checks(self, criteria, speed, emax=6.0):
        """Advisory checks of the fitted alignment: [(level, message, reference)]."""
        if criteria is None or self.alignment is None:
            return []
        out = []
        rmin = criteria.min_radius(speed, emax)
        trans = criteria.transition_required_radius(speed) if hasattr(criteria, "transition_required_radius") else None
        for i, c in enumerate(self.curves, start=1):
            if not c:
                continue
            r = c["radius"]
            if rmin and r < rmin - 1e-6:
                out.append(("departure", f"PI {i + 1}: radius {r:g} below the minimum {rmin:.0f} for {speed:g} at emax "
                                         f"{emax:g} %", criteria.ref("min_radius")))
            if trans and r < trans and not (c.get("spiral_in") and c.get("spiral_out")):
                out.append(("departure", f"PI {i + 1}: radius {r:g} below {trans:.0f} - transition curves are required",
                            criteria.ref("transition")))
            a_min = criteria.spiral_min_parameter(speed, r)
            for key in ("spiral_in", "spiral_out"):
                length = c.get(key) or 0.0
                if length > 0 and a_min and math.sqrt(r * length) < a_min - 1e-6:
                    out.append(("departure", f"PI {i + 1}: {key.replace('_', ' ')} {length:g} gives A = "
                                             f"{math.sqrt(r * length):.0f}, below {a_min:.0f}", criteria.ref("transition")))
        factor_min = criteria.get("tangent_min_factor")
        factor_max = criteria.get("tangent_max_factor")
        lines = [e for e in self.elements() if e["kind"] == "line"]
        end = self.alignment.start_station + self.alignment.length
        for e in lines:
            interior = e["start"] > self.alignment.start_station + 1e-6 and e["end"] < end - 1e-6
            if factor_min and interior and e["length"] < factor_min * speed - 1e-6:
                out.append(("departure", f"Tangent {e['start']:.1f}-{e['end']:.1f} is {e['length']:.0f} long, below "
                                         f"{factor_min * speed:.0f} ({factor_min:g} V) between curves", criteria.ref("tangents")))
            if factor_max:
                limit = criteria.get("tangent_max_cap_high_speed", 4000) if speed >= 100 else factor_max * speed
                if e["length"] > limit:
                    out.append(("departure", f"Tangent {e['start']:.1f}-{e['end']:.1f} is {e['length']:.0f} long, more "
                                             f"than {limit:.0f}", criteria.ref("tangents")))
        return out


def simplify(points, tolerance=0.1):
    """Douglas-Peucker in plan (keeps the ends); points may carry a level."""
    pts = [tuple(p) for p in points]
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        best, index = 0.0, None
        for k in range(a + 1, b):
            d = _point_segment(pts[k][0], pts[k][1], pts[a][:2], pts[b][:2])
            if d > best:
                best, index = d, k
        if index is not None and best > tolerance:
            keep[index] = True
            stack += [(a, index), (index, b)]
    return [p for p, k in zip(pts, keep) if k]


def profile_from_points(alignment, xyz, curve_length=0.0, name=None, min_gap=1.0):
    """A design profile through the levels of 3-D points (a feature line or a 3-D road line): a PVI at the station
    of each point on the fitted alignment, vertical curves of ``curve_length`` where they fit."""
    from .writer import VerticalProfile

    pts = alignment.points(0.5)
    out = []
    for x, y, z in xyz:
        best = min(pts, key=lambda q: (q[0] - x) ** 2 + (q[1] - y) ** 2)
        out.append([best[2], float(z), 0.0])
    out[0][0] = alignment.start_station
    out[-1][0] = alignment.start_station + alignment.length
    pvis = []
    for s, z, _l in sorted(out):
        if pvis and s <= pvis[-1][0] + min_gap:
            continue
        pvis.append([s, z, 0.0])
    if pvis[-1][0] < alignment.start_station + alignment.length - 1e-6:
        pvis[-1][0] = alignment.start_station + alignment.length
    for i in range(1, len(pvis) - 1):
        room = min(pvis[i][0] - pvis[i - 1][0] - pvis[i - 1][2] / 2, pvis[i + 1][0] - pvis[i][0]) * 0.95
        pvis[i][2] = max(min(curve_length, room), 0.0)
    return VerticalProfile(name or f"{alignment.name} FRL", [tuple(v) for v in pvis])


def _intersection(p1, p2, p3, p4):
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
    return x1 + t * (x2 - x1), y1 + t * (y2 - y1)


def _point_segment(x, y, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
    return math.hypot(ax + t * dx - x, ay + t * dy - y)


__all__ = ["PlanDesign", "MIN_GAP", "simplify", "profile_from_points"]
