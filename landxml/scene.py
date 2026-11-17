"""3D scene export: ``scene.json`` plus binary PLY meshes, for any renderer.

Format ``landxmltools-scene``, version 2 (version 1 plus optional ``paths``, extra per-vertex PLY
properties such as ``station`` and ``offset``, the ``road`` style, and ``visible``/``pair`` on surfaces;
readers ignore what they do not know). All coordinates are in the output CRS and units
(x east, y north, z up), stored relative to ``origin`` so renderers that use 32-bit floats
keep millimetre precision. Nothing is exaggerated in the file: ``vertical_exaggeration``
is the suggested factor for the renderer to apply to z (lifts and label heights are given
in true units, before exaggeration).

Top-level keys:

* ``format``, ``version``, ``generator``, ``created``, ``source`` (file, surfaces).
* ``crs`` (``authid``, ``wkt``), ``units`` (``horizontal``, ``vertical``), ``origin`` [x, y, z],
  ``extent`` {``min``, ``max``} (relative), ``vertical_exaggeration``, ``background``.
* ``surfaces``: [{``id``, ``name``, ``role`` (design | existing | context), ``mesh`` (PLY file
  name), ``style`` (resolved surface style: attribute, unit, classes with low/high/colour/label),
  ``opacity``, ``lift``}]. Each PLY holds float32 x, y, z, ``depth`` (design minus existing,
  NaN where unknown), uchar red, green, blue (the style baked per vertex) and the faces.
* ``lines``: [{``id``, ``name``, ``kind`` (daylight | platform_outline | retained_edge | batter_edge |
  road | ...), ``points`` [[x, y, z], ...], ``color``, ``width`` (pixels), ``lift``, ``properties``}].
  ``road`` lines are design centrelines at profile level; ``properties.width`` (if given) is the
  carriageway width for a conceptual ribbon. Roads are not part of the terrain.
* ``labels``: [{``text``, ``position`` [x, y, z], ``lift``, ``kind``}].
* ``views``: [{``name``, ``direction`` [dx, dy, dz] (from the target towards the camera),
  ``distance``, ``target`` [x, y, z], ``view_angle`` (degrees)}].
"""

from __future__ import annotations

from datetime import datetime
import json
import os

import numpy as np

FORMAT = "landxmltools-scene"
VERSION = 2
DEFAULT_VIEWS = "sw: -0.6, -0.8, 0.55; se: 0.8, -0.5, 0.6"
LINE_STYLES = {
    "daylight": ("#222222", 2, 0.05),
    "platform_outline": ("#7b3fa0", 2, 0.05),
    "retained_edge": ("#c2185b", 4, 0.1),
    "batter_edge": ("#ef6c00", 3, 0.1),
    "road": ("#555555", 4, 0.15),
}


def parse_views(text, distance=None):
    views = []
    for part in (text or DEFAULT_VIEWS).split(";"):
        if not part.strip():
            continue
        name, _, vector = part.partition(":")
        try:
            values = [float(value) for value in vector.replace(",", " ").split()]
        except ValueError as exc:
            raise ValueError(f"A view is 'name: dx, dy, dz': {part.strip()}") from exc
        if len(values) != 3 or not any(values):
            raise ValueError(f"A view is 'name: dx, dy, dz' with a non-zero direction: {part.strip()}")
        views.append({"name": name.strip() or f"view{len(views) + 1}", "direction": values, "distance": distance,
                      "view_angle": 30.0})
    return views


BASE_VERTEX = [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("depth", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")]
PLY_TYPES = {"<f4": "float", "u1": "uchar"}


def write_ply(path, xyz, faces, rgb, depth=None, extra=None):
    """Binary little-endian PLY: float x y z depth, uchar red green blue, then any ``extra`` float
    properties (name → per-vertex array, e.g. ``station`` and ``offset`` of a road surface); int32 faces."""
    xyz = np.asarray(xyz, np.float32)
    faces = np.asarray(faces, np.int32)
    extra = extra or {}
    depth = np.full(len(xyz), np.nan, np.float32) if depth is None else np.asarray(depth, np.float32)
    fields = BASE_VERTEX + [(name, "<f4") for name in extra]
    vertex = np.empty(len(xyz), dtype=fields)
    vertex["x"], vertex["y"], vertex["z"], vertex["depth"] = xyz[:, 0], xyz[:, 1], xyz[:, 2], depth
    vertex["red"], vertex["green"], vertex["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    for name, values in extra.items():
        vertex[name] = values
    face = np.empty(len(faces), dtype=[("n", "u1"), ("v", "<i4", (3,))])
    face["n"], face["v"] = 3, faces
    header = ["ply", "format binary_little_endian 1.0", "comment landxmltools-scene mesh", f"element vertex {len(xyz)}"]
    header += [f"property {PLY_TYPES[kind]} {name}" for name, kind in fields]
    header += [f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    with open(path, "wb") as stream:
        stream.write(("\n".join(header) + "\n").encode("ascii"))
        stream.write(vertex.tobytes())
        stream.write(face.tobytes())


def read_ply(path, extras=False):
    """(xyz, faces, rgb, depth) from a PLY written by ``write_ply``; with ``extras``, also a dict of the
    extra per-vertex properties."""
    with open(path, "rb") as stream:
        data = stream.read()
    end = data.index(b"end_header\n") + len(b"end_header\n")
    header = data[:end].decode("ascii").splitlines()
    count_v = int(next(line for line in header if line.startswith("element vertex")).split()[-1])
    count_f = int(next(line for line in header if line.startswith("element face")).split()[-1])
    kinds = {"float": "<f4", "uchar": "u1"}
    fields, in_vertex = [], False
    for line in header:
        if line.startswith("element"):
            in_vertex = line.startswith("element vertex")
        elif in_vertex and line.startswith("property "):
            _p, kind, name = line.split()
            fields.append((name, kinds[kind]))
    vertex = np.frombuffer(data, dtype=fields, count=count_v, offset=end)
    face = np.frombuffer(data, dtype=[("n", "u1"), ("v", "<i4", (3,))], count=count_f, offset=end + vertex.nbytes)
    xyz = np.column_stack([vertex["x"], vertex["y"], vertex["z"]]).astype(float)
    rgb = np.column_stack([vertex["red"], vertex["green"], vertex["blue"]])
    result = (xyz, face["v"].astype(np.int64), rgb, vertex["depth"].astype(float))
    if extras:
        names = [name for name, _k in fields if name not in dict(BASE_VERTEX)]
        return (*result, {name: vertex[name].astype(float) for name in names})
    return result


def station_offset(points, path_xy, path_stations, chunk=2_000_000):
    """Station and offset (positive to the right of the direction of travel) of each plan point
    against a polyline with stations; the nearest segment decides."""
    points = np.asarray(points, float)[:, :2]
    a = np.asarray(path_xy, float)[:-1, :2]
    b = np.asarray(path_xy, float)[1:, :2]
    s0 = np.asarray(path_stations, float)[:-1]
    d = b - a
    length2 = np.maximum((d ** 2).sum(axis=1), 1e-12)
    station = np.empty(len(points))
    offset = np.empty(len(points))
    step = max(1, chunk // max(len(a), 1))
    for start in range(0, len(points), step):
        p = points[start:start + step, None, :]
        t = np.clip(((p - a) * d).sum(axis=2) / length2, 0.0, 1.0)
        foot = a + t[..., None] * d
        dist2 = ((p - foot) ** 2).sum(axis=2)
        k = np.argmin(dist2, axis=1)
        rows = np.arange(len(k))
        tk = t[rows, k]
        station[start:start + step] = s0[k] + tk * np.sqrt(length2[k])
        rel = p[:, 0, :] - foot[rows, k]
        cross = d[k, 0] * rel[:, 1] - d[k, 1] * rel[:, 0]
        offset[start:start + step] = -np.sign(cross) * np.sqrt(dist2[rows, k])
    return station, offset


class SceneWriter:
    """Collects surfaces, lines, labels and views, then writes ``scene.json`` and the meshes to a folder."""

    def __init__(self, folder, origin, units, crs=None, source=None, vertical_exaggeration=1.0):
        self.folder = folder
        self.origin = np.asarray(origin, float)
        self.scene = {
            "format": FORMAT, "version": VERSION, "generator": "LandXML Road & Terrain GIS Tools",
            "created": datetime.now().isoformat(timespec="seconds"), "source": source or {},
            "crs": crs or {}, "units": units, "origin": [float(v) for v in self.origin],
            "vertical_exaggeration": float(vertical_exaggeration), "background": "#ffffff",
            "surfaces": [], "lines": [], "labels": [], "views": [],
        }
        self._low = np.full(3, np.inf)
        self._high = np.full(3, -np.inf)
        os.makedirs(folder, exist_ok=True)

    def _relative(self, points):
        points = np.asarray(points, float)[:, :3] - self.origin
        self._low = np.minimum(self._low, points.min(axis=0))
        self._high = np.maximum(self._high, points.max(axis=0))
        return points

    def surface(self, surface_id, name, role, xyz, faces, rgb, resolved_style, depth=None, opacity=1.0, lift=0.0,
                extra=None, visible=True, pair=None):
        mesh = f"{surface_id}.ply"
        faces = np.asarray(faces, np.int64)
        used = np.unique(faces)
        if len(used) < len(xyz):          # drop vertices no triangle uses
            remap = np.full(len(xyz), -1, np.int64)
            remap[used] = np.arange(len(used))
            xyz, rgb, faces = np.asarray(xyz)[used], np.asarray(rgb)[used], remap[faces]
            depth = None if depth is None else np.asarray(depth)[used]
            extra = {name: np.asarray(values)[used] for name, values in (extra or {}).items()}
        write_ply(os.path.join(self.folder, mesh), self._relative(xyz), faces, rgb, depth, extra)
        item = {"id": surface_id, "name": name, "role": role, "mesh": mesh, "style": resolved_style, "opacity": opacity,
                "lift": lift, "vertices": int(len(xyz)), "triangles": int(len(faces))}
        if not visible:
            item["visible"] = False
        if pair:
            item["pair"] = pair            # {"before": [...ids], "after": [...ids]} for the before/after switch
        self.scene["surfaces"].append(item)

    def path(self, name, points, stations):
        """A 3D path with stations (alignment at profile level), for drive-throughs and station read-outs."""
        relative = self._relative(points)
        self.scene.setdefault("paths", []).append({
            "name": name, "points": [[round(float(v), 3) for v in point] for point in relative],
            "stations": [round(float(v), 3) for v in stations]})

    def line(self, kind, name, points, properties=None, color=None, width=None, lift=None):
        default = LINE_STYLES.get(kind, ("#333333", 2, 0.1))
        relative = self._relative(points)
        self.scene["lines"].append({
            "id": f"{kind}-{len(self.scene['lines']) + 1}", "name": name, "kind": kind,
            "points": [[round(float(v), 4) for v in point] for point in relative],
            "color": color or default[0], "width": width or default[1],
            "lift": default[2] if lift is None else lift, "properties": properties or {}})

    def label(self, text, position, lift=3.0, kind="label"):
        point = np.asarray(position, float)[:3] - self.origin
        self.scene["labels"].append({"text": str(text), "position": [round(float(v), 4) for v in point],
                                     "lift": float(lift), "kind": kind})

    def views(self, views, target=None, size=None):
        """Add views aimed at ``target`` (absolute; default the scene centre); the default distance
        is 1.4 × ``size`` (default the plan diagonal of everything written)."""
        low, high = self._low, self._high
        centre = (low + high) / 2 if target is None else np.asarray(target, float) - self.origin
        size = float(np.linalg.norm((high - low)[:2])) if size is None else float(size)
        for view in views:
            item = dict(view)
            item["target"] = [round(float(v), 4) for v in centre]
            item["distance"] = float(item.get("distance") or max(1.4 * size, 10.0))
            self.scene["views"].append(item)

    def write(self):
        if np.isfinite(self._low).all():
            self.scene["extent"] = {"min": [round(float(v), 4) for v in self._low],
                                    "max": [round(float(v), 4) for v in self._high]}
        path = os.path.join(self.folder, "scene.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.scene, stream, indent=1, ensure_ascii=False)
        return path


def load_scene(folder_or_file):
    path = folder_or_file if folder_or_file.endswith(".json") else os.path.join(folder_or_file, "scene.json")
    with open(path, encoding="utf-8") as stream:
        scene = json.load(stream)
    if scene.get("format") != FORMAT:
        raise ValueError(f"'{path}' is not a {FORMAT} file.")
    if int(scene.get("version", 0)) > VERSION:
        raise ValueError(f"Scene version {scene['version']} is newer than this reader ({VERSION}).")
    return scene, os.path.dirname(os.path.abspath(path))
