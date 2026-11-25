"""Write road geometry as LandXML 1.2 that Civil 3D and this plugin can read (pure Python, no QGIS).

Horizontal alignments are built from design elements (tangents, circular curves and clothoid spirals) laid out from a
start point and bearing; vertical profiles from PVIs with symmetric parabolic curves. Coordinates are worked in map
order (easting, northing) and written northing first by default, as Civil 3D does. Nothing is inferred: the coordinate
system is written only when it is given, and every element is checked to join the next one.

    road = HorizontalAlignment("Access road", start=(10000.0, 20000.0), bearing=60.0, elements=[
        Line(200), Spiral(60, radius_end=300, turn=RIGHT), Curve(150, 300, RIGHT), Spiral(60, radius_start=300, turn=RIGHT),
        Line(250)])
    profile = VerticalProfile("FRL", [(0, 1500.0, 0), (400, 1512.0, 160), (720, 1504.5, 0)])
    write_landxml("road.xml", [(road, [profile])], linear_unit="meter", epsg=32737)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math

RIGHT, LEFT = 1, -1
UNITS = {"meter": ("Metric", "meter", "squareMeter", "cubicMeter"),
         "foot": ("Imperial", "foot", "squareFoot", "cubicYard"),
         "USSurveyFoot": ("Imperial", "USSurveyFoot", "squareFoot", "cubicYard")}
TOLERANCE = 1e-6
APPLICATION = "LandXML Road & Terrain GIS Tools"


@dataclass
class Line:
    """A tangent of ``length``."""
    length: float


@dataclass
class Curve:
    """A circular curve of ``length`` and ``radius``, turning RIGHT (clockwise in plan) or LEFT."""
    length: float
    radius: float
    turn: int = RIGHT


@dataclass
class Spiral:
    """A clothoid of ``length`` whose radius runs from ``radius_start`` to ``radius_end`` (None = straight)."""
    length: float
    radius_start: float | None = None
    radius_end: float | None = None
    turn: int = RIGHT


@dataclass
class HorizontalAlignment:
    """A centreline laid out from ``start`` (easting, northing) on ``bearing`` (degrees clockwise from north)."""
    name: str
    start: tuple
    bearing: float
    elements: list
    start_station: float = 0.0
    desc: str = ""
    _laid: list = field(default=None, init=False, repr=False)

    def layout(self):
        """[{kind, element, start, end, heading_start, heading_end, station, center, pi}] in map order, checked."""
        if self._laid is None:
            self._laid = _layout(self)
        return self._laid

    @property
    def length(self):
        return sum(item["element"].length for item in self.layout())

    def points(self, step=1.0):
        """[(easting, northing, station)] every ``step`` or closer, including every element's ends."""
        out = []
        for item in self.layout():
            e = item["element"]
            count = max(int(math.ceil(e.length / step)), 1)
            for k in range(0 if not out else 1, count + 1):
                s = e.length * k / count
                x, y, _h = _position(item, s)
                out.append((x, y, item["station"] + s))
        return out


@dataclass
class VerticalProfile:
    """A finished-level profile from PVIs (station, elevation, parabolic curve length; 0 = no curve)."""
    name: str
    pvis: list
    desc: str = ""

    def __post_init__(self):
        pvis = [(float(s), float(z), float(length or 0.0)) for s, z, length in self.pvis]
        if len(pvis) < 2:
            raise ValueError(f"Profile '{self.name}' needs at least two PVIs.")
        for (s0, _z0, _l0), (s1, _z1, _l1) in zip(pvis, pvis[1:]):
            if s1 <= s0:
                raise ValueError(f"Profile '{self.name}': PVI stations must increase ({s0:g} then {s1:g}).")
        if pvis[0][2] or pvis[-1][2]:
            raise ValueError(f"Profile '{self.name}': the first and last PVIs cannot have a vertical curve.")
        for (s0, _z0, l0), (s1, _z1, l1) in zip(pvis, pvis[1:]):
            if s0 + l0 / 2 > s1 - l1 / 2 + TOLERANCE:
                raise ValueError(f"Profile '{self.name}': the vertical curves at {s0:g} and {s1:g} overlap.")
        self.pvis = pvis

    def elevation(self, station):
        """Level at ``station`` (tangent grades, parabolas over the curves); outside the PVIs, the end grades run on."""
        p = self.pvis
        for i in range(1, len(p) - 1):
            s, z, length = p[i]
            if length and s - length / 2 <= station <= s + length / 2:
                g1 = (z - p[i - 1][1]) / (s - p[i - 1][0])
                g2 = (p[i + 1][1] - z) / (p[i + 1][0] - s)
                x = station - (s - length / 2)
                return z - g1 * length / 2 + g1 * x + (g2 - g1) * x * x / (2 * length)
        i = max(min(next((k for k in range(len(p) - 1) if station <= p[k + 1][0]), len(p) - 2), len(p) - 2), 0)
        s0, z0 = p[i][:2]
        s1, z1 = p[i + 1][:2]
        return z0 + (z1 - z0) * (station - s0) / (s1 - s0)


@dataclass
class GroundProfile:
    """A surface (existing-ground) profile along an alignment: (station, level) points, written as <ProfSurf>
    the way Civil 3D writes them, so the tools read it as ground and not as a design."""
    name: str
    points: list
    desc: str = ""
    state: str = "existing"

    def __post_init__(self):
        self.points = [(float(s), float(z)) for s, z in self.points]
        if len(self.points) < 2:
            raise ValueError(f"Ground profile '{self.name}' needs at least two points.")
        if any(b[0] <= a[0] for a, b in zip(self.points, self.points[1:])):
            raise ValueError(f"Ground profile '{self.name}': stations must increase.")


@dataclass
class PICurve:
    """The curve at an intersection point: ``radius`` with clothoid spirals of ``spiral_in`` and ``spiral_out`` (0 = none)."""
    radius: float
    spiral_in: float = 0.0
    spiral_out: float = 0.0


@dataclass
class Surface:
    """A TIN: ``points`` (n, 3) in map order (easting, northing, level) and ``faces`` (m, 3) of 0-based point indices."""
    name: str
    points: list
    faces: list
    desc: str = ""


@dataclass
class FeatureLine:
    """A 3D polyline written as a Civil 3D feature line (PlanFeature with Feature code="FeatureLine")."""
    name: str
    points: list
    desc: str = ""


def spiral_shift(radius, length):
    """(p, k, theta) of a clothoid from a tangent into ``radius`` over ``length``: the shift of the circle from the
    tangent, the distance along the tangent to the shifted circle's start, and the spiral's turn (radians)."""
    if length <= 0:
        return 0.0, 0.0, 0.0
    item = {"element": Spiral(length, None, radius, LEFT), "start": (0.0, 0.0), "heading_start": 0.0}
    x, y, _h = _position(item, length)
    theta = length / (2 * radius)
    return y - radius * (1 - math.cos(theta)), x - radius * math.sin(theta), theta


def alignment_from_pis(name, pis, curves, start_station=0.0, desc=""):
    """A HorizontalAlignment through intersection points ``pis`` [(easting, northing), ...] with a PICurve (or None for
    a straight run through) at every interior PI. Tangent lengths follow the clothoid shift formulas, unequal spirals
    included; a curve that does not fit between its neighbours, or spirals longer than the deflection allows, is
    refused with the PI named."""
    pis = [(float(x), float(y)) for x, y in pis]
    if len(pis) < 2:
        raise ValueError("An alignment needs at least two points.")
    if len(curves) != len(pis) - 2:
        raise ValueError(f"Give a curve (or None) for each of the {len(pis) - 2} interior PIs.")
    bearings, lengths = [], []
    for (x0, y0), (x1, y1) in zip(pis, pis[1:]):
        lengths.append(math.hypot(x1 - x0, y1 - y0))
        if lengths[-1] <= TOLERANCE:
            raise ValueError("Two consecutive PIs are at the same place.")
        bearings.append(math.atan2(y1 - y0, x1 - x0))
    plan = []                                         # per interior PI: (t_in, t_out, elements)
    for i, curve in enumerate(curves, start=1):
        deflection = math.atan2(math.sin(bearings[i] - bearings[i - 1]), math.cos(bearings[i] - bearings[i - 1]))
        if abs(deflection) < 1e-9:                    # no turn: the PI is just a point on a straight
            plan.append((0.0, 0.0, []))
            continue
        if curve is None:
            raise ValueError(f"PI {i + 1} turns {math.degrees(abs(deflection)):.3f}°: give it a curve.")
        turn = LEFT if deflection > 0 else RIGHT
        delta = abs(deflection)
        r = float(curve.radius)
        p1, k1, th1 = spiral_shift(r, curve.spiral_in)
        p2, k2, th2 = spiral_shift(r, curve.spiral_out)
        arc = r * (delta - th1 - th2)
        if arc < -TOLERANCE:
            raise ValueError(f"PI {i + 1}: the spirals turn more than its {math.degrees(delta):.2f}° deflection; shorten them.")
        t_in = k1 + (r + p1) * math.tan(delta / 2) + (p2 - p1) / math.sin(delta)
        t_out = k2 + (r + p2) * math.tan(delta / 2) - (p2 - p1) / math.sin(delta)
        elements = []
        if curve.spiral_in > 0:
            elements.append(Spiral(curve.spiral_in, None, r, turn))
        if arc > TOLERANCE:
            elements.append(Curve(arc, r, turn))
        if curve.spiral_out > 0:
            elements.append(Spiral(curve.spiral_out, r, None, turn))
        plan.append((t_in, t_out, elements))
    elements = []
    for leg in range(len(pis) - 1):
        used_start = plan[leg - 1][1] if leg > 0 else 0.0
        used_end = plan[leg][0] if leg < len(plan) else 0.0
        tangent = lengths[leg] - used_start - used_end
        if tangent < -1e-6:
            raise ValueError(f"The curves at PIs {leg + 1} and {leg + 2} overlap by {-tangent:.2f}: reduce the radius or "
                             "the spirals, or move the PIs apart.")
        if tangent > 1e-6:
            if elements and isinstance(elements[-1], Line):      # a straight-through PI: one tangent, not two
                elements[-1] = Line(elements[-1].length + tangent)
            else:
                elements.append(Line(tangent))
        if leg < len(plan):
            elements += plan[leg][2]
    bearing = 90.0 - math.degrees(bearings[0])
    alignment = HorizontalAlignment(name, pis[0], bearing, elements, start_station, desc)
    end = alignment.layout()[-1]["end"]
    if math.hypot(end[0] - pis[-1][0], end[1] - pis[-1][1]) > 1e-3 * max(sum(lengths), 1.0):
        raise ValueError("The fitted alignment does not reach the last PI; check the curves.")   # a safeguard, not expected
    return alignment


# ------------------------------------------------------------------ geometry

def _curvature(radius):
    return 0.0 if radius is None or math.isinf(radius) else 1.0 / radius


def _position(item, s):
    """(x, y, heading) at distance ``s`` into a laid-out element."""
    e = item["element"]
    x0, y0 = item["start"]
    h0 = item["heading_start"]
    if isinstance(e, Line):
        return x0 + s * math.cos(h0), y0 + s * math.sin(h0), h0
    if isinstance(e, Curve):
        cx, cy = item["center"]
        a0 = math.atan2(y0 - cy, x0 - cx)
        a = a0 - e.turn * s / e.radius
        return cx + e.radius * math.cos(a), cy + e.radius * math.sin(a), h0 - e.turn * s / e.radius
    k0, k1 = _curvature(e.radius_start), _curvature(e.radius_end)

    def heading(t):
        return h0 - e.turn * (k0 * t + (k1 - k0) * t * t / (2 * e.length))

    n = max(int(math.ceil(s / 0.25)), 2)
    n += n % 2                                     # Simpson needs an even number of intervals
    if s <= 0:
        return x0, y0, h0
    dt = s / n
    sx = sy = 0.0
    for k in range(n + 1):
        w = 1 if k in (0, n) else (4 if k % 2 else 2)
        h = heading(k * dt)
        sx += w * math.cos(h)
        sy += w * math.sin(h)
    return x0 + sx * dt / 3, y0 + sy * dt / 3, heading(s)


def _layout(alignment):
    if not alignment.elements:
        raise ValueError(f"Alignment '{alignment.name}' has no elements.")
    x, y = (float(v) for v in alignment.start)
    heading = math.radians(90.0 - alignment.bearing)       # bearing clockwise from north -> maths angle
    station = float(alignment.start_station)
    laid, previous_radius = [], None                        # curvature continuity: radius at the end of the last element
    for index, e in enumerate(alignment.elements):
        if e.length <= 0:
            raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: the length must be positive.")
        if isinstance(e, Curve):
            if e.radius <= 0 or e.turn not in (RIGHT, LEFT):
                raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: a curve needs a positive radius and a turn.")
            start_radius = end_radius = e.radius
        elif isinstance(e, Spiral):
            if e.turn not in (RIGHT, LEFT) or (e.radius_start is None and e.radius_end is None):
                raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: a spiral needs a turn and at least one radius.")
            start_radius, end_radius = e.radius_start, e.radius_end
        elif isinstance(e, Line):
            start_radius = end_radius = None
        else:
            raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: unknown element {e!r}.")
        if isinstance(e, Spiral) and index and not _same_radius(previous_radius, start_radius):
            raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: the spiral starts at radius "
                             f"{_label(start_radius)} but the element before it ends at {_label(previous_radius)}.")
        if index and isinstance(alignment.elements[index - 1], Spiral) and not _same_radius(previous_radius, start_radius):
            raise ValueError(f"Alignment '{alignment.name}', element {index + 1}: the spiral before it ends at radius "
                             f"{_label(previous_radius)} but this element starts at {_label(start_radius)}.")
        item = {"kind": type(e).__name__.lower(), "element": e, "start": (x, y), "heading_start": heading, "station": station}
        if isinstance(e, Curve):
            item["center"] = (x + e.radius * math.cos(heading - e.turn * math.pi / 2),
                              y + e.radius * math.sin(heading - e.turn * math.pi / 2))
        ex, ey, eh = _position(item, e.length)
        item.update(end=(ex, ey), heading_end=eh)
        if isinstance(e, Spiral):
            item["pi"] = _tangent_intersection((x, y), heading, (ex, ey), eh)
        laid.append(item)
        x, y, heading, station = ex, ey, eh, station + e.length
        previous_radius = end_radius
    return laid


def _same_radius(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOLERANCE * max(a, b, 1.0)


def _label(radius):
    return "infinite" if radius is None else f"{radius:g}"


def _tangent_intersection(p, hp, q, hq):
    det = math.cos(hp) * math.sin(hq) - math.sin(hp) * math.cos(hq)
    if abs(det) < 1e-12:                                    # no deflection: the PI is half way
        return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    t = ((q[0] - p[0]) * math.sin(hq) - (q[1] - p[1]) * math.cos(hq)) / det
    return p[0] + t * math.cos(hp), p[1] + t * math.sin(hp)


# ------------------------------------------------------------------ XML

def quoteattr(text):
    """An XML attribute value in double quotes (written without importing an XML library)."""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return '"' + text.replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;") + '"'


def _fmt(value):
    return f"{value:.6f}".rstrip("0").rstrip(".") if math.isfinite(value) else "INF"


def _pt(point, northing_first):
    x, y = point
    return f"{_fmt(y)} {_fmt(x)}" if northing_first else f"{_fmt(x)} {_fmt(y)}"


def coord_geom(alignment, northing_first=True):
    """The <CoordGeom> elements of an alignment."""
    out = []
    for item in alignment.layout():
        e = item["element"]
        start, end, sta = _pt(item["start"], northing_first), _pt(item["end"], northing_first), _fmt(item["station"])
        if isinstance(e, Line):
            out.append(f'<Line staStart="{sta}" length="{_fmt(e.length)}"><Start>{start}</Start><End>{end}</End></Line>')
            continue
        rot = "cw" if e.turn == RIGHT else "ccw"
        if isinstance(e, Curve):
            out.append(f'<Curve rot="{rot}" staStart="{sta}" length="{_fmt(e.length)}" radius="{_fmt(e.radius)}">'
                       f'<Start>{start}</Start><Center>{_pt(item["center"], northing_first)}</Center><End>{end}</End></Curve>')
        else:
            rs = "INF" if e.radius_start is None else _fmt(e.radius_start)
            re_ = "INF" if e.radius_end is None else _fmt(e.radius_end)
            out.append(f'<Spiral spiType="clothoid" rot="{rot}" staStart="{sta}" length="{_fmt(e.length)}" '
                       f'radiusStart="{rs}" radiusEnd="{re_}"><Start>{start}</Start><PI>{_pt(item["pi"], northing_first)}</PI>'
                       f'<End>{end}</End></Spiral>')
    return "".join(out)


def prof_surf(profile):
    """The <ProfSurf> of a ground profile."""
    points = " ".join(f"{_fmt(s)} {_fmt(z)}" for s, z in profile.points)
    desc = f" desc={quoteattr(profile.desc)}" if profile.desc else ""
    return (f"<ProfSurf name={quoteattr(profile.name)}{desc} state={quoteattr(profile.state)}>"
            f"<PntList2D>{points}</PntList2D></ProfSurf>")


def prof_align(profile):
    """The <ProfAlign> of a profile: PVIs, with ParaCurve where a vertical curve is set (a GroundProfile is written
    as <ProfSurf>)."""
    if isinstance(profile, GroundProfile):
        return prof_surf(profile)
    parts = []
    for s, z, length in profile.pvis:
        if length:
            parts.append(f'<ParaCurve length="{_fmt(length)}">{_fmt(s)} {_fmt(z)}</ParaCurve>')
        else:
            parts.append(f"<PVI>{_fmt(s)} {_fmt(z)}</PVI>")
    desc = f" desc={quoteattr(profile.desc)}" if profile.desc else ""
    return f"<ProfAlign name={quoteattr(profile.name)}{desc}>{''.join(parts)}</ProfAlign>"


def surface_xml(surface, northing_first=True):
    """A <Surface> TIN; point ids start at 1 as LandXML requires."""
    points = "".join(f'<P id="{k}">{_pt((x, y), northing_first)} {_fmt(z)}</P>' for k, (x, y, z) in enumerate(surface.points, 1))
    faces = "".join(f"<F>{a + 1} {b + 1} {c + 1}</F>" for a, b, c in surface.faces)
    desc = f" desc={quoteattr(surface.desc)}" if surface.desc else ""
    return (f'<Surface name={quoteattr(surface.name)}{desc}><Definition surfType="TIN"><Pnts>{points}</Pnts>'
            f"<Faces>{faces}</Faces></Definition></Surface>")


def feature_line_xml(line, northing_first=True):
    """A PlanFeature of 3D <Line> segments with Feature code="FeatureLine" (as Civil 3D exports and imports them)."""
    def p3(point):
        return f"{_pt(point[:2], northing_first)} {_fmt(point[2])}"
    segments = "".join(f"<Line><Start>{p3(a)}</Start><End>{p3(b)}</End></Line>" for a, b in zip(line.points, line.points[1:]))
    desc = f" desc={quoteattr(line.desc)}" if line.desc else ""
    return (f"<PlanFeature name={quoteattr(line.name)}{desc}><CoordGeom>{segments}</CoordGeom>"
            '<Feature code="FeatureLine"/></PlanFeature>')


def landxml_text(alignments, linear_unit="meter", epsg=None, northing_first=True, project="", group="Alignments",
                 surfaces=(), feature_lines=(), vertical_unit=None,
                 application=APPLICATION):
    """The LandXML document for [(HorizontalAlignment, [VerticalProfile, ...]), ...], TIN ``surfaces`` and
    ``feature_lines`` as text."""
    if linear_unit not in UNITS:
        raise ValueError(f"Linear unit must be one of {', '.join(UNITS)}.")
    system, linear, area, volume = UNITS[linear_unit]
    elevation = ""
    if vertical_unit is not None:                      # only when the caller knows it; never assumed
        if vertical_unit not in UNITS or UNITS[vertical_unit][0] != system:
            raise ValueError(f"Elevation unit must be one of the {system} units: "
                             f"{', '.join(k for k, v in UNITS.items() if v[0] == system)}.")
        elevation = f' elevationUnit="{UNITS[vertical_unit][1]}"'
    now = datetime.now()
    head = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}">'
            f'<Units><{system} linearUnit="{linear}"{elevation} areaUnit="{area}" volumeUnit="{volume}" '
            f'angularUnit="decimal degrees" '
            f'directionUnit="decimal degrees"/></Units>')
    if epsg is not None:
        head += f'<CoordinateSystem epsgCode="{int(epsg)}"/>'
    if application:
        head += f'<Application name={quoteattr(application)} manufacturer="QGIS plugin"/>'
    if project:
        head += f"<Project name={quoteattr(project)}/>"
    head += '<Application name="LandXML Road &amp; Terrain GIS Tools" desc="LandXML writer"/>'
    if surfaces:
        names = [surface.name for surface in surfaces]
        if len(set(names)) != len(names):
            raise ValueError("Two surfaces have the same name.")
        head += "<Surfaces>" + "".join(surface_xml(surface, northing_first) for surface in surfaces) + "</Surfaces>"
    body = []
    names = set()
    for alignment, profiles in alignments:
        if alignment.name in names:
            raise ValueError(f"Two alignments are named '{alignment.name}'.")
        names.add(alignment.name)
        desc = f" desc={quoteattr(alignment.desc)}" if alignment.desc else ""
        text = (f"<Alignment name={quoteattr(alignment.name)}{desc} length=\"{_fmt(alignment.length)}\" "
                f'staStart="{_fmt(alignment.start_station)}"><CoordGeom>{coord_geom(alignment, northing_first)}</CoordGeom>')
        if profiles:
            end = alignment.start_station + alignment.length
            for profile in profiles:
                ends = profile.points if isinstance(profile, GroundProfile) else profile.pvis
                if ends[0][0] < alignment.start_station - TOLERANCE or ends[-1][0] > end + TOLERANCE:
                    raise ValueError(f"Profile '{profile.name}' runs beyond alignment '{alignment.name}' "
                                     f"({_fmt(alignment.start_station)} to {_fmt(end)}).")
            text += f"<Profile name={quoteattr(alignment.name)}>" + "".join(prof_align(p) for p in profiles) + "</Profile>"
        body.append(text + "</Alignment>")
    text = head + (f"<Alignments name={quoteattr(group)}>" + "".join(body) + "</Alignments>" if body else "")
    for line in feature_lines:
        if len(line.points) < 2:
            raise ValueError(f"Feature line '{line.name}' needs at least two points.")
    if feature_lines:
        text += '<PlanFeatures name="Feature lines">' + "".join(feature_line_xml(line, northing_first) for line in feature_lines) + "</PlanFeatures>"
    return text + "</LandXML>\n"


def write_landxml(path, alignments, **options):
    """Write ``landxml_text(alignments, **options)`` to ``path`` (UTF-8); returns the path."""
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(landxml_text(alignments, **options))
    return path


__all__ = ["RIGHT", "LEFT", "Line", "Curve", "Spiral", "PICurve", "Surface", "FeatureLine", "HorizontalAlignment",
           "VerticalProfile", "alignment_from_pis", "spiral_shift", "surface_xml", "feature_line_xml", "coord_geom",
           "prof_align", "landxml_text", "write_landxml"]
