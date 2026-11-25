"""Build a corridor from a section project on an existing LandXML alignment (pure Python + NumPy, no QGIS).

A *section project* (JSON) names the source LandXML, alignment and design profile, the ground, the section
standard, build-ups, assemblies, regions with transitions, superelevation and widening sources, and the
stations. :func:`build_corridor` places the assembly at every station and returns the sections, finished (top)
and datum TINs, feature lines by code and quantities; :func:`write_corridor_landxml` writes them, with the
source alignment copied unchanged, to a new LandXML file. The source file is never edited.

The axis order of the file (northing first, as Civil 3D writes, or easting first) is a project setting given
by the user; it is never inferred. Work is done in map order (easting, northing).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os

import numpy as np

from . import assembly as asm
from . import corridor_features as cf
from .common import child, descendants, local_name
from .criteria import load_criteria
from .geometry import read_alignments
from .parser import load_document
from .profile import read_profile_controls
from .section_standards import load_section_standard
from .superelevation import lane_slopes, read_superelevation
from .tin_overlay import TinIndex
from .writer import VerticalProfile, quoteattr, _fmt

PROJECT_VERSION = 1


# ----------------------------------------------------------------------------------------------------------------
# Project file
# ----------------------------------------------------------------------------------------------------------------
def new_project(landxml="", alignment="", profile="", standard="generic_sections", template=None, northing_first=True,
                speed=80.0, ground=None):
    """A project dict with defaults; ``template`` is a template name of the standard to start from."""
    std = load_section_standard(standard)
    sup = std.superelevation()
    project = {
        "version": PROJECT_VERSION,
        "landxml": landxml, "sha256": file_sha256(landxml) if landxml and os.path.isfile(landxml) else "",
        "alignment": alignment, "profile": profile, "northing_first": bool(northing_first),
        "linear_unit": "meter",
        "ground": ground or {"type": "tin", "path": landxml, "surface": ""},
        "standard": std.id, "design_speed": float(speed),
        "superelevation": {"source": "landxml", "emax": 6.0, "normal_crown": float(sup["normal_crown"]),
                           "rotation": sup["rotation"], "runoff_on_tangent": float(sup["runoff_on_tangent"]),
                           "shoulder_rule": sup["shoulder_rule"], "breakover_max": float(sup.get("breakover_max", 8.0))},
        "widening": {"source": "standard", "placement": "inside"},
        "buildups": std.buildups(),
        "assemblies": {},
        "regions": [],
        "stations": {"interval": 20.0, "extra": [], "key_stations": True},
        "features": [],
        "traffic_keeps": "right" if std.id.startswith("aashto") else "left",
        "earthworks": {"topsoil": 0.15, "unsuitable": 0.0, "unsuitable_under": "fill", "bulking": 1.0},
        "ground_step": 0.5,
        "course_daylight": True,
        "outputs": {"top_surface": "Corridor Top", "datum_surface": "Corridor Datum", "feature_lines": True,
                    "cross_sections": True, "ground_name": "EG"},
    }
    templates = std.templates()
    if template is None and templates:
        template = next(iter(templates))
    if template:
        add_template(project, std, template)
    return project


def add_template(project, standard, template, name=None):
    tpl = standard.templates()[template]
    name = name or template
    project["assemblies"][name] = asm.assign_ids({"median": tpl.get("median"), "right": tpl.get("right", []),
                                                 "left": tpl.get("left", "mirror"), "template": template,
                                                 "template_ref": tpl.get("ref", "")})
    if not project["regions"]:
        project["regions"] = [{"start": None, "end": None, "assembly": name, "transition": 0.0}]
    if tpl.get("speed") and not project.get("design_speed"):
        project["design_speed"] = float(tpl["speed"])
    return name


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_project(project, path):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(project, stream, indent=1)
    return path


def load_project(path):
    with open(path, encoding="utf-8") as stream:
        project = json.load(stream)
    if project.get("version", 0) > PROJECT_VERSION:
        raise ValueError("This section project was written by a newer version of the plugin.")
    base = new_project(standard=project.get("standard", "generic_sections"), template=False)
    for key, value in base.items():
        project.setdefault(key, value)
    for a in project.get("assemblies", {}).values():
        asm.assign_ids(a)
    return project


# ----------------------------------------------------------------------------------------------------------------
# Alignment frame, profile, ground
# ----------------------------------------------------------------------------------------------------------------
class AlignmentFrame:
    """Station -> plan position and heading on a LandXML alignment, in map order (easting, northing)."""

    def __init__(self, record, northing_first):
        pts = np.asarray(record["points"], float)[:, :2]
        if northing_first:
            pts = pts[:, ::-1]
        self.xy = pts
        self.start = float(record.get("sta_start") or 0.0)
        self.s = self.start + np.asarray(record["vertex_distances"], float)
        self.end = float(self.s[-1])
        self.name = record["name"]
        self.segments = record["segments"]
        self.northing_first = northing_first
        d = np.diff(self.xy, axis=0)
        self.seg_heading = np.arctan2(d[:, 1], d[:, 0])

    def at(self, stations):
        """(xy (n, 2), unit tangent (n, 2), right normal (n, 2)) at ``stations``."""
        st = np.clip(np.asarray(stations, float), self.start, self.end)
        i = np.clip(np.searchsorted(self.s, st, "right") - 1, 0, len(self.s) - 2)
        t = (st - self.s[i]) / np.maximum(self.s[i + 1] - self.s[i], 1e-12)
        xy = self.xy[i] + (self.xy[i + 1] - self.xy[i]) * t[:, None]
        # heading: blend the chord headings around the vertex so the normal turns smoothly
        h0 = self.seg_heading[i]
        h_prev = self.seg_heading[np.maximum(i - 1, 0)]
        h_next = self.seg_heading[np.minimum(i + 1, len(self.seg_heading) - 1)]
        a = _angle_mix(h_prev, h0, 0.5)
        b = _angle_mix(h0, h_next, 0.5)
        heading = _angle_mix(a, b, t)
        tangent = np.column_stack([np.cos(heading), np.sin(heading)])
        normal = np.column_stack([tangent[:, 1], -tangent[:, 0]])
        return xy, tangent, normal

    def elements(self):
        """[(kind, start station, end station, radius, turn)] of the horizontal elements (turn +1 right)."""
        out = []
        running = self.start
        for seg in self.segments:
            length = seg.get("length") or 0.0
            start = seg.get("station_start")
            if start is None or abs(float(start) - running) > max(1.0, 1e-4 * running):
                start = running                     # missing staStart (or station equations): the running station
            running = float(start) + float(length)
            turn = 1 if (seg.get("direction") or "").lower() == "cw" else -1
            radius = seg.get("radius")
            if seg["geometry_type"] == "spiral":
                radius = seg.get("radius_end") or seg.get("radius_start")
            out.append((seg["geometry_type"], float(start), float(start) + float(length), radius, turn,
                        seg.get("radius_start"), seg.get("radius_end")))
        return out

    def station_of(self, x, y):
        """(station, offset) of a map point (nearest point on the alignment)."""
        p = np.array([x, y], float)
        a, b = self.xy[:-1], self.xy[1:]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        q = a + ab * t[:, None]
        dist = np.hypot(*(q - p).T)
        k = int(dist.argmin())
        station = self.s[k] + t[k] * (self.s[k + 1] - self.s[k])
        cross = ab[k, 0] * (p[1] - a[k, 1]) - ab[k, 1] * (p[0] - a[k, 0])
        return float(station), float(-np.sign(cross) * dist[k])


def _angle_mix(a, b, t):
    diff = (b - a + np.pi) % (2 * np.pi) - np.pi
    return a + diff * t


def alignment_element(root, name):
    found = [a for a in descendants(root, "Alignment") if a.attrib.get("name") == name]
    if not found:
        raise ValueError(f"Alignment '{name}' was not found in the LandXML file.")
    if len(found) > 1:
        raise ValueError(f"Alignment name '{name}' is ambiguous ({len(found)} alignments).")
    return found[0]


def design_profiles(alignment_node):
    return [p for p in descendants(alignment_node, "ProfAlign")]


def load_profile(alignment_node, name=""):
    profiles = design_profiles(alignment_node)
    if not profiles:
        raise ValueError("The alignment has no design profile (ProfAlign).")
    if name:
        match = [p for p in profiles if p.attrib.get("name") == name]
        if not match:
            raise ValueError(f"Design profile '{name}' was not found on the alignment.")
        node = match[0]
    elif len(profiles) == 1:
        node = profiles[0]
    else:
        raise ValueError("The alignment has several design profiles; choose one: "
                         + ", ".join(p.attrib.get("name", "?") for p in profiles))
    controls = read_profile_controls(node)
    pvis = [(c["station"], c["elevation"], c["curve_length"] or 0.0) for c in controls]
    return VerticalProfile(node.attrib.get("name") or "Design", pvis), [c["station"] for c in controls]


class Ground:
    """Ground levels at map points: a TIN (from a LandXML surface) or a DEM raster (GDAL), or none."""

    def __init__(self, spec, northing_first, extent=None, document=None):
        self.kind = (spec or {}).get("type", "none")
        self.name = (spec or {}).get("surface") or (spec or {}).get("name") or "EG"
        self.index = None
        self.raster = None
        self.xyz = self.faces = None
        if self.kind == "tin":
            from .. import core                       # GDAL only needed for reading
            path = spec.get("path")
            bounds = None
            if extent is not None:
                x0, y0, x1, y1 = extent
                bounds = (y0, x0, y1, x1) if northing_first else (x0, y0, x1, y1)
            xyz, faces, _meta = core.read_tin(path, spec.get("surface") or None, bounds=bounds)
            xyz = np.asarray(xyz, float)
            if northing_first:
                xyz = xyz[:, [1, 0, 2]]
            self.index = TinIndex(xyz, faces)
            self.xyz, self.faces = xyz, np.asarray(faces)
            self.name = spec.get("surface") or _meta.get("surface_name") or "EG"
        elif self.kind == "dem":
            from osgeo import gdal
            ds = gdal.Open(spec["path"])
            if ds is None:
                raise ValueError(f"Cannot open the DEM {spec['path']}.")
            gt = ds.GetGeoTransform()
            band = ds.GetRasterBand(int(spec.get("band", 1)))
            nodata = band.GetNoDataValue()
            if extent is not None:
                x0, y0, x1, y1 = extent
                c0 = max(int((x0 - gt[0]) / gt[1]) - 2, 0)
                c1 = min(int((x1 - gt[0]) / gt[1]) + 3, ds.RasterXSize)
                r0 = max(int((y1 - gt[3]) / gt[5]) - 2, 0)
                r1 = min(int((y0 - gt[3]) / gt[5]) + 3, ds.RasterYSize)
                if c1 <= c0 or r1 <= r0:
                    raise ValueError("The DEM does not cover the alignment (check that it is in the same CRS).")
            else:
                c0, r0, c1, r1 = 0, 0, ds.RasterXSize, ds.RasterYSize
            data = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(float)
            if nodata is not None:
                data[data == nodata] = np.nan
            self.raster = (data, gt[0] + c0 * gt[1], gt[1], gt[3] + r0 * gt[5], gt[5])
            self.name = spec.get("name") or os.path.splitext(os.path.basename(spec["path"]))[0]

    def sample(self, xy):
        xy = np.asarray(xy, float)
        if self.index is not None:
            return self.index.sample(xy)
        if self.raster is not None:
            data, x0, dx, y0, dy = self.raster
            col = (xy[:, 0] - x0) / dx - 0.5
            row = (xy[:, 1] - y0) / dy - 0.5
            c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
            fc, fr = col - c0, row - r0
            h, w = data.shape
            ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
            out = np.full(len(xy), np.nan)
            c, r, a, b = c0[ok], r0[ok], fc[ok], fr[ok]
            out[ok] = (data[r, c] * (1 - a) * (1 - b) + data[r, c + 1] * a * (1 - b)
                       + data[r + 1, c] * (1 - a) * b + data[r + 1, c + 1] * a * b)
            return out
        return np.full(len(xy), np.nan)


# ----------------------------------------------------------------------------------------------------------------
# Superelevation and widening schedules
# ----------------------------------------------------------------------------------------------------------------
def curve_groups(frame):
    """[(curve start, curve end, radius, turn, spiral in length, spiral out length, TS, ST)] per circular curve."""
    els = frame.elements()
    out = []
    for k, (kind, s0, s1, radius, turn, _rs, _re) in enumerate(els):
        if kind != "curve" or not radius:
            continue
        sp_in = els[k - 1] if k > 0 and els[k - 1][0] == "spiral" else None
        sp_out = els[k + 1] if k + 1 < len(els) and els[k + 1][0] == "spiral" else None
        out.append({"start": s0, "end": s1, "radius": float(radius), "turn": turn,
                    "spiral_in": (sp_in[2] - sp_in[1]) if sp_in else 0.0,
                    "spiral_out": (sp_out[2] - sp_out[1]) if sp_out else 0.0,
                    "ts": sp_in[1] if sp_in else s0, "st": sp_out[2] if sp_out else s1})
    return out


def computed_superelevation(frame, criteria, standard, speed, emax, normal_crown, runoff_on_tangent, lanes_rotated,
                            lane_width, warnings):
    """Superelevation records (as :func:`read_superelevation` returns) computed from the design criteria."""
    records = []
    nc = abs(normal_crown)
    for curve in curve_groups(frame):
        rate, label = criteria.required_superelevation(curve["radius"], speed, emax)
        if rate is None:
            warnings.append(f"Curve R={curve['radius']:g} at {curve['start']:.1f}: radius below the minimum for "
                            f"{speed:g} at emax {emax:g} % - superelevation set to emax.")
            rate, label = float(emax), f"{emax:g}% (minimum radius exceeded)"
        if label == "NC" or rate <= 0:
            continue
        rate = max(rate, nc)
        n1 = max(lanes_rotated, 1.0)
        bw = (1 + 0.5 * (n1 - 1)) / n1
        delta = standard.relative_gradient(speed) if standard is not None else None
        lengths = []
        if delta:
            lengths.append(lane_width * n1 * bw * rate / delta)
        base = criteria.runoff_min(speed, rate, lane_width) if criteria is not None else None
        if base:
            lengths.append(base)
        runoff = max(lengths) if lengths else 30.0
        source = "computed from " + (standard.name if standard else "the criteria")
        if curve["spiral_in"] > 0:
            begin_runoff, full_start = curve["ts"], curve["start"]
            runoff_in = curve["spiral_in"]
            source += ", runoff over the spiral"
        else:
            begin_runoff = curve["start"] - runoff_on_tangent * runoff
            full_start = curve["start"] + (1 - runoff_on_tangent) * runoff
            runoff_in = runoff
        if curve["spiral_out"] > 0:
            full_end, end_runoff = curve["end"], curve["st"]
            runoff_out = curve["spiral_out"]
        else:
            full_end = curve["end"] - (1 - runoff_on_tangent) * runoff
            end_runoff = curve["end"] + runoff_on_tangent * runoff
            runoff_out = runoff
        if full_end < full_start:                         # short curve: full rate only at the middle
            mid = (curve["start"] + curve["end"]) / 2
            full_start = full_end = mid
        runout_in = runoff_in * nc / rate
        runout_out = runoff_out * nc / rate
        records.append({"begin_runout": begin_runoff - runout_in, "begin_runoff": begin_runoff,
                        "full_start": full_start, "rate": -rate if curve["turn"] < 0 else rate,
                        "full_end": full_end, "end_runoff": end_runoff, "end_runout": end_runoff + runout_out,
                        "curve_start": curve["start"], "curve_end": curve["end"], "radius": curve["radius"],
                        "label": label, "source": source, "turn": curve["turn"]})
    records.sort(key=lambda r: r["begin_runout"])
    for a, b in zip(records, records[1:]):               # reverse / broken-back curves: meet in the middle
        if a["end_runout"] > b["begin_runout"]:
            mid = (a["end_runoff"] + b["begin_runoff"]) / 2
            a["end_runout"] = max(min(a["end_runout"], mid), a["end_runoff"])
            b["begin_runout"] = min(max(b["begin_runout"], mid), b["begin_runoff"])
            warnings.append(f"Superelevation transitions overlap between {a['curve_end']:.1f} and "
                            f"{b['curve_start']:.1f}; the runouts were shortened to meet.")
    return records


class Schedules:
    """Per-station lane falls (superelevation) and widening for each side."""

    def __init__(self, frame, project, criteria, standard, assemblies, warnings):
        sup = project.get("superelevation") or {}
        self.normal_crown = float(sup.get("normal_crown", 2.5))
        self.source = sup.get("source", "landxml")
        self.records = []
        speed = float(project.get("design_speed") or 80.0)
        first = next(iter(assemblies.values()), {"right": []})
        lanes = [e for e in first.get("right", []) if e["type"] == "lane"]
        lane_width = float(lanes[0]["width"]) if lanes else 3.5
        if self.source == "landxml":
            self.records = read_superelevation(project["_alignment_node"])
            for r in self.records:
                r["source"] = "superelevation from the LandXML"
            if not self.records:
                warnings.append("The alignment carries no superelevation data; lanes keep their normal crossfall "
                                "(choose 'computed from the standard' to develop it).")
        elif self.source == "standard":
            if criteria is None:
                warnings.append("The standard has no design criteria for superelevation rates; none applied.")
            else:
                self.records = computed_superelevation(
                    frame, criteria, standard, speed, float(sup.get("emax", 6.0)), self.normal_crown,
                    float(sup.get("runoff_on_tangent", 0.5)), max(len(lanes), 1), lane_width, warnings)
        left, right = lane_slopes(self.records, self.normal_crown) if self.records else ([], [])
        self.left = _breaks(left)
        self.right = _breaks(right)
        # widening
        self.widen = []
        wid = project.get("widening") or {}
        if wid.get("source", "standard") == "standard" and standard is not None:
            curves = curve_groups(frame)
            for curve in curves:
                width, ref = standard.widening(curve["radius"], speed, lanes=2 * max(len(lanes), 1))
                if width <= 0:
                    continue
                rec = next((r for r in self.records if r.get("curve_start", r["full_start"]) <= curve["start"] + 1e-6
                            and r.get("curve_end", r["full_end"]) >= curve["end"] - 1e-6), None)
                if rec is None:
                    rec = next((r for r in self.records if r["begin_runoff"] <= curve["start"] <= r["end_runoff"]), None)
                if rec is not None:
                    a, b, c, d = rec["begin_runoff"], max(rec["full_start"], curve["start"]), \
                        min(rec["full_end"], curve["end"]), rec["end_runoff"]
                    a, d = min(a, curve["ts"]), max(d, curve["st"])
                else:
                    ramp = max(curve["spiral_in"], 20.0)
                    a, b, c, d = curve["ts"] - (0 if curve["spiral_in"] else ramp), curve["start"], curve["end"], \
                        curve["st"] + (0 if curve["spiral_out"] else ramp)
                self.widen.append({"stations": (a, b, c, d), "width": width, "turn": curve["turn"],
                                   "ref": f"{ref}, R = {curve['radius']:g}", "placement": wid.get("placement", "inside")})

    def states(self, station):
        out = {}
        active = any(r["begin_runout"] - 1e-6 <= station <= r["end_runout"] + 1e-6 for r in self.records)
        rec = next((r for r in self.records if r["begin_runout"] - 1e-6 <= station <= r["end_runout"] + 1e-6), None)
        for side, table in (("left", self.left), ("right", self.right)):
            fall = None
            if active and table is not None:
                fall = -float(np.interp(station, table[0], table[1]))
            source = rec.get("source", "") if rec else ""
            if rec:
                source += f", full {abs(rec['rate']):g} %"
            width, wsrc = 0.0, ""
            for w in self.widen:
                a, b, c, d = w["stations"]
                if a <= station <= d:
                    if station < b:
                        f = (station - a) / max(b - a, 1e-9)
                    elif station > c:
                        f = (d - station) / max(d - c, 1e-9)
                    else:
                        f = 1.0
                    inside = "right" if w["turn"] > 0 else "left"
                    share = 1.0 if w["placement"] == "inside" else 0.5
                    if w["placement"] == "inside" and side != inside:
                        share = 0.0
                    width += w["width"] * f * share
                    wsrc = w["ref"]
            out[side] = asm.SideState(fall, width, source, wsrc, active)
        return out

    def key_stations(self):
        out = []
        for r in self.records:
            out += [r[k] for k in ("begin_runout", "begin_runoff", "full_start", "full_end", "end_runoff", "end_runout")]
        for w in self.widen:
            out += list(w["stations"])
        return out


def _breaks(points):
    if not points:
        return None
    s = np.array([p[0] for p in points], float)
    v = np.array([p[1] for p in points], float)
    order = np.argsort(s, kind="stable")
    return s[order], v[order]


# ----------------------------------------------------------------------------------------------------------------
# Regions and transitions
# ----------------------------------------------------------------------------------------------------------------
NUMERIC_BLEND = ("width", "crossfall", "slope", "height", "channel", "depth", "bottom", "offset", "top_fall")


def resolve_regions(project, start, end):
    regions = []
    for r in project.get("regions") or []:
        a = start if r.get("start") in (None, "") else float(r["start"])
        b = end if r.get("end") in (None, "") else float(r["end"])
        regions.append(dict(r, start=a, end=b))
    regions.sort(key=lambda r: r["start"])
    if not regions:
        raise ValueError("The project has no regions: add an assembly to a station range.")
    return regions


def assembly_at(station, regions, assemblies):
    """(assembly, region, transition fraction or None) at ``station``."""
    region = next((r for r in regions if r["start"] - 1e-9 <= station <= r["end"] + 1e-9), None)
    if region is None:
        region = regions[0] if station < regions[0]["start"] else regions[-1]
    k = regions.index(region)
    current = assemblies[region["assembly"]]
    t_len = float(region.get("transition") or 0.0)
    if k > 0 and t_len > 0 and station < region["start"] + t_len:
        previous = assemblies[regions[k - 1]["assembly"]]
        t = (station - region["start"]) / t_len
        return blend(previous, current, max(min(t, 1.0), 0.0)), region, t
    return current, region, None


def blend(a, b, t):
    """Assembly ``b`` with numeric parameters moved linearly from ``a`` at fraction ``t``: elements matched by
    id and type; an element only in ``b`` grows from zero width, one only in ``a`` shrinks to zero."""
    out = copy.deepcopy(b)
    for side in asm.SIDES:
        old = {(e["id"], e["type"]): e for e in a.get(side, [])}
        new_keys = {(e["id"], e["type"]) for e in b.get(side, [])}
        items = []
        for e in out.get(side, []):
            o = old.get((e["id"], e["type"]))
            if o is None:
                if "width" in e:
                    e["width"] = float(e["width"]) * t
                items.append(e)
                continue
            for key in NUMERIC_BLEND:
                if key in e and key in o and isinstance(e[key], (int, float)) and isinstance(o[key], (int, float)):
                    e[key] = float(o[key]) + (float(e[key]) - float(o[key])) * t
            items.append(e)
        # elements that disappear: keep them in their old order, shrinking
        positions = [k for k, e in enumerate(a.get(side, [])) if (e["id"], e["type"]) not in new_keys]
        for k in positions:
            e = copy.deepcopy(a[side][k])
            if "width" in e and e["type"] not in asm.TERMINAL:
                e["width"] = float(e["width"]) * (1 - t)
                items.insert(min(k, len(items)), e)
        out[side] = items
    if a.get("median") and not b.get("median"):
        out["median"] = copy.deepcopy(a["median"])
        out["median"]["width"] = float(a["median"]["width"]) * (1 - t)
    elif b.get("median") and not a.get("median"):
        out["median"]["width"] = float(b["median"]["width"]) * t
    if a.get("median") and b.get("median"):
        for key in NUMERIC_BLEND:
            if key in b["median"] and key in a["median"]:
                out["median"][key] = float(a["median"][key]) + (float(b["median"][key]) - float(a["median"][key])) * t
    return out


# ----------------------------------------------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------------------------------------------
class Corridor:
    """The result of a build: ``stations``, ``sections`` (dicts from :func:`assembly.build_section` with plan
    data), ``frame``, ``profile``, ``schedules``, ``warnings`` and ``checks``."""

    def __init__(self):
        self.stations, self.sections, self.warnings, self.checks = [], [], [], []


def prepare(project, progress=None):
    """Load everything a build needs; returns a context dict (reuse it for live editing)."""
    path = project["landxml"]
    if not path or not os.path.isfile(path):
        raise ValueError("The section project's LandXML file was not found.")
    warnings = []
    if project.get("sha256"):
        try:
            if file_sha256(path) != project["sha256"]:
                warnings.append("The source LandXML has changed since the project was saved.")
        except OSError:
            pass
    document = load_document(path)
    records, unsupported = read_alignments(path, segment_length=1.0)
    names = [r["name"] for r in records]
    if not project.get("alignment"):
        if len(records) != 1:
            raise ValueError("Choose an alignment: " + ", ".join(names))
        project["alignment"] = names[0]
    record = next((r for r in records if r["name"] == project["alignment"]), None)
    if record is None:
        raise ValueError(f"Alignment '{project['alignment']}' could not be read. " + "; ".join(unsupported[:3]))
    if record.get("station_equations"):
        warnings.append("The alignment has station equations; the corridor uses internal stations.")
    node = alignment_element(document.root, project["alignment"])
    frame = AlignmentFrame(record, project.get("northing_first", True))
    profile, pvi_stations = load_profile(node, project.get("profile", ""))
    std = load_section_standard(project.get("standard") or "generic_sections")
    criteria = None
    if std.data.get("review_criteria"):
        try:
            criteria = load_criteria(std.data["review_criteria"])
        except ValueError:
            criteria = None
    unit = (document.horizontal_unit or "meter")
    project["linear_unit"] = {"meter": "meter", "metre": "meter", "foot": "foot", "internationalfoot": "foot",
                              "ussurveyfoot": "USSurveyFoot"}.get(str(unit).lower(), "meter")
    std_metric = std.units == "metric"
    if (project["linear_unit"] == "meter") != std_metric:
        warnings.append(f"The standard ({std.name}) is in {'metres' if std_metric else 'feet'} but the file is in "
                        f"{project['linear_unit']}: check every dimension (no automatic conversion).")
    assemblies = {name: asm.normalise_assembly(a, std, project.get("buildups")) for name, a in project["assemblies"].items()}
    project["_alignment_node"] = node
    schedules = Schedules(frame, project, criteria, std, assemblies, warnings)
    project.pop("_alignment_node", None)
    # extent for the ground: alignment box plus the widest section
    reach = 0.0
    for a in assemblies.values():
        width = sum(float(e.get("width") or 0) + float(e.get("channel") or 0) for e in a["right"])
        dl = next((e for e in a["right"] if e["type"] == "daylight"), None)
        reach = max(reach, width + (float(dl["max_width"]) if dl else 0.0) + 10.0
                    + (float(a["median"]["width"]) / 2 if a.get("median") else 0.0))
    x0, y0 = frame.xy.min(0) - reach
    x1, y1 = frame.xy.max(0) + reach
    gspec = dict(project.get("ground") or {"type": "none"})
    if gspec.get("type") == "tin" and not gspec.get("path"):
        gspec["path"] = path
    try:
        ground = Ground(gspec, project.get("northing_first", True), (x0, y0, x1, y1)) if gspec.get("type") != "none" \
            else Ground({"type": "none"}, False)
    except ValueError as exc:
        raise ValueError(f"Ground: {exc}") from exc
    regions = resolve_regions(project, frame.start, frame.end)
    for r in regions:
        if r["assembly"] not in assemblies:
            raise ValueError(f"Region {r['start']:.1f}-{r['end']:.1f} uses an unknown assembly '{r['assembly']}'.")
    features = cf.resolve(project.get("features"), frame, profile, project.get("traffic_keeps", "left"))
    return {"features": features,"project": project, "document": document, "frame": frame, "profile": profile, "pvis": pvi_stations,
            "standard": std, "criteria": criteria, "assemblies": assemblies, "schedules": schedules,
            "ground": ground, "regions": regions, "reach": reach, "warnings": warnings, "unit": project["linear_unit"],
            "alignment_node": node}


def refresh_context(context):
    """Apply edits of the project (assemblies, regions, standard, superelevation, widening, build-ups) to a
    prepared context without reading the file or the ground again (the live editor calls this on every edit)."""
    project = context["project"]
    if project.get("standard") != context["standard"].id:
        context["standard"] = load_section_standard(project.get("standard") or "generic_sections")
        review = context["standard"].data.get("review_criteria")
        try:
            context["criteria"] = load_criteria(review) if review else None
        except ValueError:
            context["criteria"] = None
    std = context["standard"]
    warnings = []
    context["assemblies"] = {name: asm.normalise_assembly(a, std, project.get("buildups"))
                             for name, a in project["assemblies"].items()}
    project["_alignment_node"] = context["alignment_node"]
    try:
        context["schedules"] = Schedules(context["frame"], project, context["criteria"], std, context["assemblies"], warnings)
    finally:
        project.pop("_alignment_node", None)
    context["regions"] = resolve_regions(project, context["frame"].start, context["frame"].end)
    context["features"] = cf.resolve(project.get("features"), context["frame"], context["profile"],
                                     project.get("traffic_keeps", "left"))
    for r in context["regions"]:
        if r["assembly"] not in context["assemblies"]:
            raise ValueError(f"Region {r['start']:.1f}-{r['end']:.1f} uses an unknown assembly '{r['assembly']}'.")
    context["refresh_warnings"] = warnings
    return context


def station_list(context):
    project, frame = context["project"], context["frame"]
    spec = project.get("stations") or {}
    interval = float(spec.get("interval") or 20.0)
    start, end = frame.start, frame.end
    st = list(np.arange(math.ceil(start / interval) * interval, end + 1e-9, interval))
    st += [start, end]
    if spec.get("key_stations", True):
        for kind, s0, s1, *_rest in frame.elements():
            st += [s0, s1]
        st += context["schedules"].key_stations()
        for r in context["regions"]:
            st += [r["start"], r["end"]]
            if r.get("transition"):
                st.append(r["start"] + float(r["transition"]))
        st += [p for p in context["pvis"]]
        st += cf.key_stations(context.get("features", []))
    st += [float(s) for s in spec.get("extra") or []]
    st = sorted({round(s, 4) for s in st if start - 1e-6 <= s <= end + 1e-6})
    return st


def ground_profiles(context, stations, xy, normal):
    step = float(context["project"].get("ground_step") or 0.5)
    reach = context["reach"]
    offsets = np.arange(-reach, reach + step / 2, step)
    if context["ground"].kind == "none":
        return offsets, None
    pts = xy[:, None, :] + normal[:, None, :] * offsets[None, :, None]
    z = context["ground"].sample(pts.reshape(-1, 2)).reshape(len(stations), len(offsets))
    return offsets, z


def section_at(context, station, ground_offsets=None, ground_z=None):
    """One section (for the live editor): the build_section dict plus plan data."""
    frame, profile = context["frame"], context["profile"]
    xy, tangent, normal = frame.at([station])
    if ground_offsets is None:
        offs, gz = ground_profiles(context, [station], xy, normal)
        ground_offsets, ground_z = offs, (gz[0] if gz is not None else None)
    assembly, region, t = assembly_at(station, context["regions"], context["assemblies"])
    assembly, active = cf.apply(assembly, station, context.get("features", []))
    states = context["schedules"].states(station)
    project = context["project"]
    sup = project.get("superelevation") or {}
    options = {"unit": "m" if context["unit"] == "meter" else "ft", "rotation": sup.get("rotation", "centreline"),
               "normal_crown": float(sup.get("normal_crown", 2.5)), "shoulder_rule": sup.get("shoulder_rule", "own"),
               "breakover_max": float(sup.get("breakover_max", 8.0)),
               "course_daylight": project.get("course_daylight", True)}
    grade = profile.elevation(station)
    ground = (ground_offsets, ground_z) if ground_z is not None and np.isfinite(ground_z).any() else None
    section = asm.build_section(assembly, grade, states, ground, context["standard"], project.get("buildups"), options)
    section.update(station=station, xy=xy[0], normal=normal[0], tangent=tangent[0], region=region, transition=t,
                   states=states, ground=ground, features=active)
    for f in active:
        for p in section["top"]:
            if p.eid and p.eid.startswith(f"feat_{f['id']}"):
                p.why += f" [{f['label']}: {f['ref']}; {f.get('remark', '')}]".replace("; ]", "]")
    if t is not None:
        for p in section["top"]:
            p.why += f" [transition {t * 100:.0f} % into region '{region['assembly']}']"
    return section


def build_corridor(project, progress=None, context=None):
    context = context or prepare(project, progress)
    result = Corridor()
    result.context = context
    result.warnings = list(context["warnings"])
    stations = station_list(context)
    frame = context["frame"]
    xy, tangent, normal = frame.at(stations)
    offsets, gz = ground_profiles(context, stations, xy, normal)
    if gz is None:
        result.warnings.append("No ground: daylight runs to the maximum width and no earthworks are computed.")
    seen = set()
    for k, station in enumerate(stations):
        section = section_at(context, station, offsets, gz[k] if gz is not None else None)
        for w in section["warnings"]:
            key = w.split(":")[0]
            if key not in seen:
                seen.add(key)
                result.warnings.append(f"{w} (first at {station:.2f})")
        result.sections.append(section)
        if progress and k % 25 == 0:
            progress(100.0 * k / max(len(stations), 1), f"Section {k + 1} of {len(stations)}")
    result.stations = stations
    for name, a in context["assemblies"].items():
        for c in asm.check_assembly(a, context["standard"], project.get("design_speed")):
            c["assembly"] = name
            result.checks.append(c)
    return result


# ----------------------------------------------------------------------------------------------------------------
# Surfaces (ladder triangulation between consecutive sections)
# ----------------------------------------------------------------------------------------------------------------
def _ladder(a_idx, a_off, b_idx, b_off):
    faces = []
    i = j = 0
    n, m = len(a_idx), len(b_idx)
    if n == 0 or m == 0:
        return faces
    while i < n - 1 or j < m - 1:
        if j >= m - 1 or (i < n - 1 and a_off[i + 1] <= b_off[j + 1]):
            faces.append((a_idx[i], b_idx[j], a_idx[i + 1]))
            i += 1
        else:
            faces.append((a_idx[i], b_idx[j], b_idx[j + 1]))
            j += 1
    return faces


def _distinct(offsets):
    """A vertical step (two levels at one offset) moved 1 mm apart, away from the baseline, for TIN output."""
    out = list(offsets)
    for k in range(1, len(out)):
        if out[k] <= out[k - 1] + 1e-9:
            if out[k] >= 0:
                out[k] = out[k - 1] + 0.001
            else:
                out[k - 1] = out[k] - 0.001
    return out


def section_plan(section, offsets, levels):
    xy, normal = section["xy"], section["normal"]
    offsets = np.asarray(offsets, float)
    plan = xy[None, :] + normal[None, :] * offsets[:, None]
    return np.column_stack([plan, np.asarray(levels, float)])


def corridor_surface(result, which="top"):
    """(xyz (n, 3) map order, faces (m, 3)) of the top or datum surface."""
    rows, xyz = [], []
    count = 0
    for section in result.sections:
        if which == "top":
            off = [p.d for p in section["top"]]
            lev = [p.z for p in section["top"]]
        else:
            off = [p[0] for p in section["datum"]]
            lev = [p[1] for p in section["datum"]]
        off = _distinct(off)
        pts = section_plan(section, off, lev)
        xyz.append(pts)
        rows.append((np.arange(count, count + len(pts)), np.asarray(off)))
        count += len(pts)
    faces = []
    for (ai, ao), (bi, bo) in zip(rows, rows[1:]):
        faces += _ladder(ai, ao, bi, bo)
    return np.concatenate(xyz) if xyz else np.zeros((0, 3)), np.asarray(faces, np.int64).reshape(-1, 3)


# ----------------------------------------------------------------------------------------------------------------
# Feature lines by code (Civil 3D form)
# ----------------------------------------------------------------------------------------------------------------
LINE_CODES = {"ETW", "EPS", "ESH", "LANE", "FL", "KERB_T", "KERB_BK", "SW_B", "CY_B", "VERGE", "DITCH_I", "DITCH_IF",
              "DITCH_IB", "HINGE", "DAYLIGHT", "BENCH_T", "BENCH_B", "BENCH_E", "TOE", "TOP_CUT", "FILL_W",
              "MED_E", "MED_FL", "MED_KT", "MED_C", "CL", "BARRIER", "CUTOFF_I", "WALL_T", "WALL_B"}


def feature_strings(result):
    """{(side, element, code, n): [(station, offset, level)]}: every coded point strung along the corridor."""
    strings = {}
    for section in result.sections:
        station = section["station"]
        counts = {}
        for p in list(section["top"]) + [b for s in asm.SIDES for b in section["sides"][s]["barriers"]]:
            base = p.code.split("#")[0] if p.code else ""
            if base not in LINE_CODES:
                continue
            side = "C" if abs(p.d) < 1e-6 and base in ("CL", "MED_C") else ("L" if p.d < 0 else "R")
            key0 = (side, p.element, base)
            counts[key0] = counts.get(key0, 0) + 1
            key = key0 + (counts[key0],)
            strings.setdefault(key, []).append((station, p.d, p.z))
    return strings


def feature_lines(result, weed=0.005):
    """[{name, code, side, points [(x, y, z)], segments, elevation_points}] in map order."""
    frame = result.context["frame"]
    curves = [(s0, s1, r, turn) for kind, s0, s1, r, turn, *_ in frame.elements() if kind == "curve" and r]
    lines_ = [(s0, s1) for kind, s0, s1, *_ in frame.elements() if kind == "line"]
    stations = result.stations
    out = []
    for (side, element, code, n), pts in sorted(feature_strings(result).items(), key=lambda kv: str(kv[0])):
        if len(pts) < 2:
            continue
        # split where the string skips a station (element absent there)
        runs, run = [], [pts[0]]
        index = {s: k for k, s in enumerate(stations)}
        for a, b in zip(pts, pts[1:]):
            if index.get(b[0], 0) - index.get(a[0], 0) > 1:
                runs.append(run)
                run = []
            run.append(b)
        runs.append(run)
        for r_i, run in enumerate(runs):
            if len(run) < 2:
                continue
            st = np.array([p[0] for p in run])
            off = np.array([p[1] for p in run])
            lev = np.array([p[2] for p in run])
            xy, _t, normal = frame.at(st)
            plan = xy + normal * off[:, None]
            xyz = np.column_stack([plan, lev])
            segments, elevation_points = _civil3d_segments(frame, st, off, xyz, curves, lines_, weed)
            name = f"{code} {side} {element}".strip() + (f" {n}" if n > 1 else "") + (f" ({r_i + 1})" if len(runs) > 1 else "")
            out.append({"name": name, "code": code, "side": side, "element": element, "points": xyz.tolist(),
                        "segments": segments, "elevation_points": elevation_points})
    return out


def _civil3d_segments(frame, st, off, xyz, curves, lines_, weed):
    """Plan geometry as Line and three-point Curve segments (Center = mid-arc point, level the mean of its ends)
    where the offset is constant over an alignment line or curve; weeded chords elsewhere. Levels between the
    segment ends are kept as elevation points."""
    segments, elevation = [], []
    n = len(st)
    k = 0
    while k < n - 1:
        span = None
        for s0, s1, radius, turn in curves:
            if s0 - 1e-6 <= st[k] < s1 - 1e-6:
                span = ("curve", s1, radius, turn)
                break
        if span is None:
            for s0, s1 in lines_:
                if s0 - 1e-6 <= st[k] < s1 - 1e-6:
                    span = ("line", s1, None, None)
                    break
        if span is not None:
            j = k
            while j + 1 < n and st[j + 1] <= span[1] + 1e-6 and abs(off[j + 1] - off[k]) < 1e-4:
                j += 1
            if j > k:
                a, b = xyz[k], xyz[j]
                if span[0] == "line":
                    segments.append(("line", a.tolist(), b.tolist()))
                else:
                    mid_s = (st[k] + st[j]) / 2
                    mxy, _t, mn = frame.at([mid_s])
                    m = (mxy[0] + mn[0] * off[k]).tolist() + [(a[2] + b[2]) / 2]
                    radius = span[2] - span[3] * off[k]          # right turn: right offsets are inside
                    segments.append(("arc", a.tolist(), m, b.tolist(), abs(radius)))
                elevation += [xyz[i].tolist() for i in range(k + 1, j)]
                k = j
                continue
        # variable offset or a spiral: chords, weeded
        j = k + 1
        while j + 1 < n and _collinear(xyz[k], xyz[j], xyz[j + 1], weed):
            j += 1
        segments.append(("line", xyz[k].tolist(), xyz[j].tolist()))
        k = j
    return segments, elevation


def _collinear(a, b, c, tolerance):
    ab, ac = b[:2] - a[:2], c[:2] - a[:2]
    length = np.hypot(*ac)
    if length < 1e-9:
        return True
    plan = abs(ab[0] * ac[1] - ab[1] * ac[0]) / length
    t = np.dot(ab, ac) / (length * length)
    level = abs(a[2] + (c[2] - a[2]) * t - b[2])
    return plan <= tolerance and level <= tolerance


# ----------------------------------------------------------------------------------------------------------------
# LandXML output
# ----------------------------------------------------------------------------------------------------------------
def _xml(node, skip=()):
    """Serialise a parsed element with local names (no namespace prefixes), skipping child tags in ``skip``."""
    tag = local_name(node.tag)
    attrs = "".join(f" {local_name(k)}={quoteattr(v)}" for k, v in node.attrib.items())
    inner = _escape(node.text or "") + "".join(_xml(c, skip) + _escape(getattr(c, "tail", None) or "")
                                               for c in node if local_name(c.tag) not in skip)
    return f"<{tag}{attrs}>{inner}</{tag}>" if inner else f"<{tag}{attrs}/>"


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pt3(p, northing_first):
    x, y, z = p
    return f"{_fmt(y)} {_fmt(x)} {_fmt(z)}" if northing_first else f"{_fmt(x)} {_fmt(y)} {_fmt(z)}"


def cross_sects_xml(result, ground_name="EG", top_name="Corridor Top"):
    parts = ["<CrossSects>"]
    for section in result.sections:
        sta = section["station"]
        grade = section["grade"]
        parts.append(f'<CrossSect sta="{_fmt(sta)}" name="{_station_label(sta)}">')
        g = section.get("ground")
        if g is not None:
            offs, zs = g
            ok = np.isfinite(zs)
            lo = min(p.d for p in section["top"]) - 5.0
            hi = max(p.d for p in section["top"]) + 5.0
            sel = ok & (offs >= lo) & (offs <= hi)
            if sel.sum() >= 2:
                parts.append(f"<CrossSectSurf name={quoteattr(ground_name)}><PntList2D>"
                             + " ".join(f"{_fmt(o)} {_fmt(z)}" for o, z in zip(offs[sel], zs[sel]))
                             + "</PntList2D></CrossSectSurf>")
        parts.append(f"<CrossSectSurf name={quoteattr(top_name)}><PntList2D>"
                     + " ".join(f"{_fmt(p.d)} {_fmt(p.z)}" for p in section["top"]) + "</PntList2D></CrossSectSurf>")
        for shape in section["shapes"]:
            ring = shape["ring"] + [shape["ring"][0]]
            name = shape["name"] if not shape.get("buildup") else f"{shape['name']}"
            parts.append(f'<DesignCrossSectSurf name={quoteattr(name)} side="{shape.get("side", "both")}" '
                         f'closedArea="true" area="{_fmt(round(shape["area"], 6))}">'
                         + "".join(f"<CrossSectPnt>{_fmt(d)} {_fmt(z)}</CrossSectPnt>" for d, z in ring)
                         + "</DesignCrossSectSurf>")
        parts.append('<DesignCrossSectSurf name="Datum">' + "".join(
            f"<CrossSectPnt>{_fmt(d)} {_fmt(z - grade)}</CrossSectPnt>" for d, z in section["datum"]) + "</DesignCrossSectSurf>")
        parts.append("<DesignCrossSectSurf>" + "".join(
            f"<CrossSectPnt code={quoteattr(p.code)}>{_fmt(p.d)} {_fmt(p.z - grade)}</CrossSectPnt>"
            for p in section["top"] if p.code) + "</DesignCrossSectSurf>")
        parts.append("</CrossSect>")
    parts.append("</CrossSects>")
    return "".join(parts)


def _station_label(s):
    km = int(s // 1000)
    return f"{km}+{s - km * 1000:07.3f}"


def surface_xml(name, xyz, faces, northing_first, desc=""):
    pts = "".join(f'<P id="{k}">{_pt3(p, northing_first)}</P>' for k, p in enumerate(xyz, 1))
    fs = "".join(f"<F>{a + 1} {b + 1} {c + 1}</F>" for a, b, c in faces)
    d = f" desc={quoteattr(desc)}" if desc else ""
    return f'<Surface name={quoteattr(name)}{d}><Definition surfType="TIN"><Pnts>{pts}</Pnts><Faces>{fs}</Faces></Definition></Surface>'


def feature_line_xml(line, northing_first):
    parts = []
    for seg in line["segments"]:
        if seg[0] == "line":
            parts.append(f"<Line><Start>{_pt3(seg[1], northing_first)}</Start><End>{_pt3(seg[2], northing_first)}</End></Line>")
        else:
            parts.append(f'<Curve radius="{_fmt(seg[4])}"><Start>{_pt3(seg[1], northing_first)}</Start>'
                         f"<Center>{_pt3(seg[2], northing_first)}</Center><End>{_pt3(seg[3], northing_first)}</End></Curve>")
    locs = "".join(f'<Location desc="FeatureLine Elevation Point">{_pt3(p, northing_first)}</Location>'
                   for p in line["elevation_points"])
    return (f"<PlanFeature name={quoteattr(line['name'])} desc={quoteattr(line['code'])}><CoordGeom>{''.join(parts)}</CoordGeom>"
            f'{locs}<Feature code="FeatureLine"><Property label="layer" value="C-ROAD-FEAT"/>'
            f'<Property label="code" value={quoteattr(line["code"])}/></Feature></PlanFeature>')


def escape_ramps(result):
    """Escape ramp geometry of the built corridor (see :func:`corridor_features.escape_ramps`)."""
    context = result.context
    feats = [f for f in context.get("features", []) if f["insert"] == "ramp"]
    if not feats:
        return []

    def edge(station, side):
        sec = section_at(context, station - (0.5 if True else 0))
        f = next(f for f in feats if abs(f["at"] - station) < 1e-6)
        pts = [p for p in sec["top"] if p.eid == f"feat_{f['id']}"]
        if pts:
            return pts[-1].d - (1 if side == "right" else -1) * float(f.get("width") or 8.0) / 2
        etw = [p for p in sec["top"] if p.code == "ETW" and (p.d > 0) == (side == "right")]
        return etw[0].d if etw else 0.0
    return cf.escape_ramps(feats, context["frame"], context["profile"], edge)


TARGETS = ("civil3d", "openroads")
STAGES = ("alignment", "profile", "surfaces", "corridor")
STAGE_LABELS = {"alignment": "Horizontal alignment only", "profile": "Alignment and profiles (design and ground)",
                "surfaces": "Alignment, profiles, finished and datum surfaces, feature lines",
                "corridor": "Everything: surfaces, feature lines and cross-sections (CrossSects)"}


def _near_alignment(points, frame, distance):
    """Mask of map points within ``distance`` of the alignment (chunked, no spatial index needed)."""
    st = np.arange(frame.start, frame.end + 5.0, 5.0)
    line, _t, _n = frame.at(st)
    a, b = line[:-1], line[1:]
    ab = b - a
    l2 = np.maximum((ab ** 2).sum(1), 1e-12)
    out = np.zeros(len(points), bool)
    step = max(1, 2_000_000 // max(len(a), 1))
    for k in range(0, len(points), step):
        p = points[k:k + step, None, :]
        t = np.clip(((p - a) * ab).sum(2) / l2, 0, 1)
        d = np.sqrt(((a + t[..., None] * ab - p) ** 2).sum(2)).min(1)
        out[k:k + step] = d <= distance
    return out


def ground_footprint_tin(result, margin=20.0):
    """The ground as a TIN over the corridor footprint plus ``margin`` (for exporting a concept with its terrain)."""
    context = result.context
    ground, frame = context["ground"], context["frame"]
    half = max(max(abs(p.d) for p in sec["top"]) for sec in result.sections) + margin
    if ground.xyz is not None:
        xyz, faces = ground.xyz, ground.faces
        centroids = xyz[faces][:, :, :2].mean(1)
        keep = _near_alignment(centroids, frame, half)
        faces = faces[keep]
        used = np.unique(faces)
        remap = np.full(len(xyz), -1)
        remap[used] = np.arange(len(used))
        return xyz[used], remap[faces]
    if ground.raster is not None:
        data, x0, dx, y0, dy = ground.raster
        rows, cols = data.shape
        xs = x0 + (np.arange(cols) + 0.5) * dx
        ys = y0 + (np.arange(rows) + 0.5) * dy
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        keep = _near_alignment(pts, frame, half).reshape(rows, cols) & np.isfinite(data)
        index = np.full((rows, cols), -1)
        index[keep] = np.arange(int(keep.sum()))
        a, b, c, d = index[:-1, :-1], index[:-1, 1:], index[1:, :-1], index[1:, 1:]
        quads = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
        faces = np.concatenate([np.column_stack([a[quads], c[quads], d[quads]]),
                                np.column_stack([a[quads], d[quads], b[quads]])])
        return np.column_stack([pts[keep.ravel()], data[keep]]), faces
    return np.zeros((0, 3)), np.zeros((0, 3), int)


def _densified(line, step=2.0):
    """A feature line as plain points (arcs sampled every ``step``), for targets without three-point curves."""
    pts = []
    for seg in line["segments"]:
        if seg[0] == "line":
            chain = [seg[1], seg[2]]
        else:
            from .features import _three_point_arc
            chain = _three_point_arc(seg[1], seg[2], seg[3], step, line["name"])
        for p in chain:
            if not pts or math.dist(pts[-1][:2], p[:2]) > 1e-6:
                pts.append(list(p))
    src = np.asarray(line["points"], float)
    if len(src) >= 2 and pts:
        # levels from the section points: each point takes the level of its projection on the station-point chain
        p = np.asarray(pts, float)
        a, b = src[:-1], src[1:]
        ab = b[:, :2] - a[:, :2]
        l2 = np.maximum((ab ** 2).sum(1), 1e-12)
        for k in range(len(p)):
            t = np.clip(((p[k, :2] - a[:, :2]) * ab).sum(1) / l2, 0, 1)
            d = np.hypot(*(a[:, :2] + ab * t[:, None] - p[k, :2]).T)
            j = int(d.argmin())
            pts[k][2] = float(a[j, 2] + t[j] * (b[j, 2] - a[j, 2]))
    return pts


def plain_feature_line_xml(line, northing_first):
    """A PlanFeature as Line segments only (no three-point curves, no elevation points)."""
    pts = _densified(line)
    segs = "".join(f"<Line><Start>{_pt3(a, northing_first)}</Start><End>{_pt3(b, northing_first)}</End></Line>"
                   for a, b in zip(pts, pts[1:]))
    return (f"<PlanFeature name={quoteattr(line['name'])} desc={quoteattr(line['code'])}><CoordGeom>{segs}</CoordGeom>"
            f"<Feature code={quoteattr(line['code'])}/></PlanFeature>")


def breaklines_xml(lines, northing_first):
    parts = []
    for line in lines:
        pts = _densified(line)
        if len(pts) < 2:
            continue
        parts.append(f'<Breakline brkType="standard" name={quoteattr(line["name"])} desc={quoteattr(line["code"])}>'
                     f"<PntList3D>{' '.join(_pt3(p, northing_first) for p in pts)}</PntList3D></Breakline>")
    return f"<SourceData><Breaklines>{''.join(parts)}</Breaklines></SourceData>" if parts else ""


def write_corridor_landxml(result, path, include_ground_surface=False, target="civil3d", stage="corridor"):
    """A new LandXML with the source alignment (copied unchanged) and, by ``stage``: its profiles (and a ground
    profile along the centreline), the top and datum TINs and the feature lines, and the CrossSects. ``target``
    shapes the parts that differ between Civil 3D and OpenRoads Designer: Civil 3D gets feature lines as PlanFeatures
    with three-point curves and elevation points; OpenRoads gets them as surface breaklines and plain PlanFeatures.
    ``include_ground_surface`` adds the ground over the corridor footprint. Returns the path."""
    if target not in TARGETS or stage not in STAGES:
        raise ValueError(f"Target must be one of {TARGETS} and stage one of {STAGES}.")
    level = STAGES.index(stage)
    context = result.context
    project = context["project"]
    nf = project.get("northing_first", True)
    out = project.get("outputs") or {}
    src = context["document"].root
    units = child(src, "Units")
    crs = child(src, "CoordinateSystem")
    node = alignment_element(src, project["alignment"])
    top_name = out.get("top_surface", "Corridor Top")
    datum_name = out.get("datum_surface", "Corridor Datum")
    ground_name = context["ground"].name or "EG"
    from datetime import datetime
    now = datetime.now()
    text = [f'<?xml version="1.0" encoding="UTF-8"?>\n<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" '
            f'version="1.2" date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}">']
    if units is not None:
        text.append(_xml(units))
    if crs is not None:
        text.append(_xml(crs))
    text.append('<Application name="LandXML Road &amp; Terrain GIS Tools" desc="Corridor Section Editor" '
                f'manufacturer="QGIS plugin" version={quoteattr(target + " " + stage)}/>')
    text.append(f"<Project name={quoteattr(project.get('name') or project['alignment'] + ' corridor')}/>")
    ramps = escape_ramps(result) if level >= 2 else []
    lines = (feature_lines(result) + [_bed_outline(r) for r in ramps]) if level >= 2 and out.get("feature_lines", True) else []
    surfaces = []
    if level >= 2:
        top = corridor_surface(result, "top")
        datum = corridor_surface(result, "datum")
        for name, (xyz, faces), desc, brk in ((top_name, top, "Finished surface", True),
                                              (datum_name, datum, "Bottom of pavement", False)):
            if len(faces):
                xml_ = surface_xml(name, xyz, faces, nf, desc)
                if target == "openroads" and brk and lines:
                    xml_ = xml_.replace("<Definition", breaklines_xml(lines, nf) + "<Definition", 1)
                surfaces.append(xml_)
    if include_ground_surface and context["ground"].kind != "none":
        gxyz, gfaces = ground_footprint_tin(result)
        if len(gfaces):
            surfaces.append(surface_xml(ground_name, gxyz, gfaces, nf, "Existing ground over the corridor footprint"))
    if surfaces:
        text.append("<Surfaces>" + "".join(surfaces) + "</Surfaces>")
    skip = ("CrossSects",) if level >= 1 else ("CrossSects", "Profile", "Superelevation")
    body = _xml(node, skip=skip)
    tail = ""
    if level >= 1 and context["ground"].kind != "none":
        tail += _ground_profile_xml(result, ground_name)
    if level >= 3 and out.get("cross_sections", True):
        tail += cross_sects_xml(result, ground_name, top_name)
    if tail:
        if "<Profile" in body and "</Profile>" in body and tail.startswith("<ProfSurf"):
            cut = tail.index("<CrossSects>") if "<CrossSects>" in tail else len(tail)
            body = body.replace("</Profile>", tail[:cut] + "</Profile>", 1)
            tail = tail[cut:]
        elif tail.startswith("<ProfSurf"):
            cut = tail.index("<CrossSects>") if "<CrossSects>" in tail else len(tail)
            tail = f"<Profile name={quoteattr(project['alignment'])}>" + tail[:cut] + "</Profile>" + tail[cut:]
        body = body[: body.rfind("</Alignment>")] + tail + "</Alignment>"
    for ramp in ramps:
        body += _ramp_alignment_xml(ramp, nf)
    text.append("<Alignments name=\"Corridor\">" + body + "</Alignments>")
    if lines:
        writer = feature_line_xml if target == "civil3d" else plain_feature_line_xml
        text.append('<PlanFeatures name="Corridor feature lines">' + "".join(writer(line, nf) for line in lines)
                    + "</PlanFeatures>")
    text.append("</LandXML>\n")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("".join(text))
    return path


def _ground_profile_xml(result, name):
    """The ground along the centreline (from the sections) as a <ProfSurf>, when it is not in the source already."""
    pts = []
    for sec in result.sections:
        g = sec.get("ground")
        if g is None:
            continue
        offs, zs = g
        ok = np.isfinite(zs)
        if ok.sum() >= 2 and offs[ok].min() <= 0 <= offs[ok].max():
            pts.append((sec["station"], float(np.interp(0.0, offs[ok], zs[ok]))))
    if len(pts) < 2:
        return ""
    return (f"<ProfSurf name={quoteattr(name + ' (corridor ground)')} state=\"existing\"><PntList2D>"
            + " ".join(f"{_fmt(s)} {_fmt(z)}" for s, z in pts) + "</PntList2D></ProfSurf>")


def _ramp_alignment_xml(ramp, northing_first):
    from .writer import HorizontalAlignment, Line, VerticalProfile, coord_geom, prof_align

    f = ramp["feature"]
    length = math.dist(ramp["points"][0], ramp["points"][-1])
    hx, hy = ramp["heading"]
    bearing = math.degrees(math.atan2(hx, hy)) % 360.0
    al = HorizontalAlignment(f["label"], ramp["start"], bearing, [Line(length)])
    pv = VerticalProfile(f"{f['label']} FRL", [(0.0, ramp["levels"][0], 0.0),
                                                (ramp["entry"], ramp["levels"][min(int(ramp["entry"]), len(ramp["levels"]) - 1)], 0.0),
                                                (length, ramp["levels"][-1], 0.0)])
    return (f"<Alignment name={quoteattr(f['label'])} desc={quoteattr(ramp['why'])} length=\"{_fmt(length)}\" staStart=\"0\">"
            f"<CoordGeom>{coord_geom(al, northing_first)}</CoordGeom><Profile name={quoteattr(f['label'])}>"
            f"{prof_align(pv)}</Profile></Alignment>")


def _bed_outline(ramp):
    (x0, y0), (hx, hy) = ramp["start"], ramp["heading"]
    nx, ny = hy, -hx
    w = ramp["width"] / 2
    e = ramp["entry"]
    total = e + ramp["bed"]
    z_a = ramp["levels"][min(int(e), len(ramp["levels"]) - 1)]
    z_b = ramp["levels"][-1]
    pts = []
    for d, z, s in ((e, z_a, 1), (total, z_b, 1), (total, z_b, -1), (e, z_a, -1), (e, z_a, 1)):
        pts.append([x0 + hx * d + nx * w * s, y0 + hy * d + ny * w * s, z])
    return {"name": f"{ramp['feature']['label']} arrester bed", "code": "ARRESTER_BED", "side": "", "element": "",
            "points": pts, "segments": [("line", a, b) for a, b in zip(pts, pts[1:])], "elevation_points": []}


__all__ = ["escape_ramps", "refresh_context", "new_project", "add_template", "save_project", "load_project", "prepare", "build_corridor", "section_at",
           "station_list", "corridor_surface", "feature_lines", "write_corridor_landxml", "AlignmentFrame",
           "computed_superelevation", "blend", "assembly_at", "Corridor"]
