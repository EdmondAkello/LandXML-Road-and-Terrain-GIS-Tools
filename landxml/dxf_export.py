"""LandXML to DXF (ASCII, AutoCAD R12 / AC1009): alignments, profiles' 3D centrelines, feature lines and breaklines,
TIN surfaces as 3DFACEs, and station ticks. No external library; Civil 3D, AutoCAD, BricsCAD and QGIS read it.

The coordinates are written as stored in the file, in the axis order the caller states (``northing_first`` puts the
second stored value on X); the unit and CRS are the file's. Nothing is inferred.
"""

from __future__ import annotations

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


class DxfWriter:
    def __init__(self):
        self.layers = {}
        self.entities = []

    def _layer(self, name, colour=7):
        self.layers.setdefault(name, colour)
        return name

    def polyline(self, layer, points, closed=False, colour=7, three_d=True):
        layer = self._layer(layer, colour)
        flags = (8 if three_d else 0) | (1 if closed else 0)
        out = ["0", "POLYLINE", "8", layer, "66", "1", "10", "0.0", "20", "0.0", "30", "0.0", "70", str(flags)]
        for p in points:
            z = p[2] if len(p) > 2 and p[2] is not None and np.isfinite(p[2]) else 0.0
            out += ["0", "VERTEX", "8", layer, "10", f"{p[0]:.4f}", "20", f"{p[1]:.4f}", "30", f"{z:.4f}",
                    "70", "32" if three_d else "0"]
        out += ["0", "SEQEND", "8", layer]
        self.entities.append("\n".join(out))

    def faces(self, layer, xyz, triangles, colour=8):
        layer = self._layer(layer, colour)
        chunk = []
        for a, b, c in triangles:
            pa, pb, pc = xyz[a], xyz[b], xyz[c]
            chunk.append("\n".join(["0", "3DFACE", "8", layer,
                                    "10", f"{pa[0]:.4f}", "20", f"{pa[1]:.4f}", "30", f"{pa[2]:.4f}",
                                    "11", f"{pb[0]:.4f}", "21", f"{pb[1]:.4f}", "31", f"{pb[2]:.4f}",
                                    "12", f"{pc[0]:.4f}", "22", f"{pc[1]:.4f}", "32", f"{pc[2]:.4f}",
                                    "13", f"{pc[0]:.4f}", "23", f"{pc[1]:.4f}", "33", f"{pc[2]:.4f}"]))
        self.entities.extend(chunk)

    def text(self, layer, x, y, height, value, rotation=0.0, colour=7):
        layer = self._layer(layer, colour)
        self.entities.append("\n".join(["0", "TEXT", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0",
                                        "40", f"{height:.3f}", "1", str(value), "50", f"{rotation:.3f}"]))

    def line(self, layer, a, b, colour=7):
        layer = self._layer(layer, colour)
        self.entities.append("\n".join(["0", "LINE", "8", layer, "10", f"{a[0]:.4f}", "20", f"{a[1]:.4f}", "30", "0.0",
                                        "11", f"{b[0]:.4f}", "21", f"{b[1]:.4f}", "31", "0.0"]))

    def write(self, path):
        layers = ["0", "TABLE", "2", "LAYER", "70", str(len(self.layers) + 1),
                  "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS"]
        for name, colour in self.layers.items():
            layers += ["0", "LAYER", "2", name, "70", "0", "62", str(colour), "6", "CONTINUOUS"]
        layers += ["0", "ENDTAB"]
        with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as out:
            out.write("\n".join(["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "0", "ENDSEC",
                                 "0", "SECTION", "2", "TABLES"] + layers + ["0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]))
            out.write("\n")
            for e in self.entities:
                out.write(e)
                out.write("\n")
            out.write("0\nENDSEC\n0\nEOF\n")


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
