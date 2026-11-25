"""LandXML to DXF (ASCII, AutoCAD 2018 / AC1032, written from a template drawing): alignments, profiles' 3D centrelines, feature lines and breaklines,
TIN surfaces as 3DFACEs, and station ticks. No external library; Civil 3D, AutoCAD, BricsCAD and QGIS read it.

The coordinates are written as stored in the file, in the axis order the caller states (``northing_first`` puts the
second stored value on X); the unit and CRS are the file's. Nothing is inferred.
"""

from __future__ import annotations

import os
import re

import numpy as np

from .features import read_line_features
from .geometry import read_alignments
from .parser import load_document

LAYER_COLOURS = {"ALIGNMENT": 1, "CENTRELINE-3D": 3, "STATIONS": 7, "FEATURE": 4, "BREAKLINE": 6, "TIN": 8,
                 "DAYLIGHT": 30, "OUTLINE": 5}


def layer_name(*parts):
    """A DXF layer name: letters, digits, - and _ only, at most 255 characters."""
    text = "-".join(p for p in parts if p)
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_") or "0"
    return text[:255]


TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dxf_template.dxf")


class DxfWriter:
    """Layers and entities written through :mod:`dxfw` (R2018 with handles, from a template drawing)."""

    def __init__(self):
        from . import dxfw

        self.doc = dxfw.Drawing(TEMPLATE)
        self.msp = self.doc.modelspace

    def _layer(self, name, colour=7):
        if not self.doc.has_layer(name):
            self.doc.add_layer(name, color=colour)
        return name

    def polyline(self, layer, points, closed=False, colour=7, three_d=True):
        layer = self._layer(layer, colour)
        pts = [(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 and p[2] is not None and np.isfinite(p[2]) else 0.0)
               for p in points]
        if three_d:
            self.msp.add_polyline3d(pts, close=closed, layer=layer)
        else:
            self.msp.add_lwpolyline([p[:2] for p in pts], close=closed, layer=layer)

    def faces(self, layer, xyz, triangles, colour=8):
        layer = self._layer(layer, colour)
        for a, b, c in triangles:
            self.msp.add_3dface(tuple(xyz[a]), tuple(xyz[b]), tuple(xyz[c]), layer=layer)

    def text(self, layer, x, y, height, value, rotation=0.0, colour=7):
        layer = self._layer(layer, colour)
        self.msp.add_text(str(value), (float(x), float(y)), float(height), layer=layer, rotation=float(rotation))

    def line(self, layer, a, b, colour=7):
        layer = self._layer(layer, colour)
        self.msp.add_line((float(a[0]), float(a[1]), 0.0), (float(b[0]), float(b[1]), 0.0), layer=layer)

    def write(self, path):
        self.doc.saveas(path)


def export_dxf(path, out, northing_first, surfaces=True, station_interval=100.0, text_height=2.5, segment_length=2.0):
    """Write ``out`` from the LandXML ``path``. Returns counts by kind."""
    swap = bool(northing_first)
    document = load_document(path)
    dxf = DxfWriter()
    counts = {"alignments": 0, "feature_lines": 0, "surfaces": 0, "triangles": 0}

    def xy(p):
        return (p[1], p[0]) + tuple(p[2:]) if swap else tuple(p)

    try:
        records, _bad = read_alignments(path, segment_length)
    except ValueError:
        records = []
    for r in records:
        pts = [xy(p) for seg in r["segments"] for p in seg["points"]]
        if len(pts) < 2:
            continue
        name = r["name"]
        dxf.polyline(layer_name("ALIGNMENT", name), [(p[0], p[1], 0.0) for p in pts], colour=1, three_d=False)
        counts["alignments"] += 1
        # station ticks and labels along the alignment
        arr = np.array([p[:2] for p in pts], float)
        chain = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(arr, axis=0).T))])
        start = float(r.get("sta_start") or 0.0)
        if station_interval and chain[-1] > 0:
            first = np.ceil(start / station_interval) * station_interval
            for station in np.arange(first, start + chain[-1] + 1e-6, station_interval):
                s = station - start
                k = min(max(int(np.searchsorted(chain, s)) - 1, 0), len(arr) - 2)
                t = (s - chain[k]) / max(chain[k + 1] - chain[k], 1e-12)
                p = arr[k] + t * (arr[k + 1] - arr[k])
                d = arr[k + 1] - arr[k]
                d = d / (np.hypot(*d) or 1.0)
                n = np.array([-d[1], d[0]])
                dxf.line(layer_name("STATIONS", name), p - n * text_height, p + n * text_height)
                angle = np.degrees(np.arctan2(n[1], n[0]))
                km, m = divmod(round(station, 3), 1000)
                dxf.text(layer_name("STATIONS", name), *(p + n * text_height * 1.4), text_height,
                         f"{int(km)}+{m:07.3f}", angle)
    lines, _warn = read_line_features(document.root)
    for f in lines:
        if len(f.points) < 2:
            continue
        kind = "BREAKLINE" if f.kind.lower().startswith("break") else "FEATURE"
        label = (f.name or f.feature_code or "unnamed")
        sub = "DAYLIGHT" if label.lower().startswith("daylight") else ("OUTLINE" if label.lower().endswith("outline") else kind)
        closed = len(f.points) > 2 and np.allclose(f.points[0][:2], f.points[-1][:2])
        dxf.polyline(layer_name(sub, re.sub(r"\s+\d+$", "", label) if sub == "DAYLIGHT" else label),
                     [xy(p) for p in f.points], closed=closed, colour=LAYER_COLOURS[sub])
        counts["feature_lines"] += 1
    if surfaces:
        from ..core import read_tin

        for i, s in enumerate(document.surfaces()):
            name = s.attrib.get("name", f"Surface {i + 1}")
            try:
                xyz, faces, _meta = read_tin(path, name, document=document)
            except ValueError:
                continue
            xyz = np.asarray(xyz, float)
            if swap:
                xyz = xyz[:, [1, 0, 2]]
            if len(faces):
                dxf.faces(layer_name("TIN", name), xyz, np.asarray(faces), colour=8)
                counts["surfaces"] += 1
                counts["triangles"] += len(faces)
    dxf.write(out)
    return counts


__all__ = ["export_dxf", "DxfWriter", "layer_name"]
