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


def prof_align(profile):
    """The <ProfAlign> of a profile: PVIs, with ParaCurve where a vertical curve is set."""
    parts = []
    for s, z, length in profile.pvis:
        if length:
            parts.append(f'<ParaCurve length="{_fmt(length)}">{_fmt(s)} {_fmt(z)}</ParaCurve>')
        else:
            parts.append(f"<PVI>{_fmt(s)} {_fmt(z)}</PVI>")
    desc = f" desc={quoteattr(profile.desc)}" if profile.desc else ""
    return f"<ProfAlign name={quoteattr(profile.name)}{desc}>{''.join(parts)}</ProfAlign>"


def landxml_text(alignments, linear_unit="meter", epsg=None, northing_first=True, project="", group="Alignments"):
    """The LandXML document for [(HorizontalAlignment, [VerticalProfile, ...]), ...] as text."""
    if linear_unit not in UNITS:
        raise ValueError(f"Linear unit must be one of {', '.join(UNITS)}.")
    system, linear, area, volume = UNITS[linear_unit]
    now = datetime.now()
    head = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}">'
            f'<Units><{system} linearUnit="{linear}" areaUnit="{area}" volumeUnit="{volume}" angularUnit="decimal degrees" '
            f'directionUnit="decimal degrees"/></Units>')
    if epsg is not None:
        head += f'<CoordinateSystem epsgCode="{int(epsg)}"/>'
    if project:
        head += f"<Project name={quoteattr(project)}/>"
    head += '<Application name="LandXML Road &amp; Terrain GIS Tools" desc="LandXML writer"/>'
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
                if profile.pvis[0][0] < alignment.start_station - TOLERANCE or profile.pvis[-1][0] > end + TOLERANCE:
                    raise ValueError(f"Profile '{profile.name}' runs beyond alignment '{alignment.name}' "
                                     f"({_fmt(alignment.start_station)} to {_fmt(end)}).")
            text += f"<Profile name={quoteattr(alignment.name)}>" + "".join(prof_align(p) for p in profiles) + "</Profile>"
        body.append(text + "</Alignment>")
    return head + f"<Alignments name={quoteattr(group)}>" + "".join(body) + "</Alignments></LandXML>\n"


def write_landxml(path, alignments, **options):
    """Write ``landxml_text(alignments, **options)`` to ``path`` (UTF-8); returns the path."""
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(landxml_text(alignments, **options))
    return path


__all__ = ["RIGHT", "LEFT", "Line", "Curve", "Spiral", "HorizontalAlignment", "VerticalProfile", "coord_geom",
           "prof_align", "landxml_text", "write_landxml"]
