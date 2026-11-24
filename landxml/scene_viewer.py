"""Write a scene as one self-contained HTML file with the plugin's own WebGL viewer.

The viewer (``viewer.html``, plain JavaScript and WebGL 2, no libraries, no network) opens
offline in any current browser: rotate, pan and zoom, layer switches and opacity, a legend
panel beside the model, a compass that turns with the camera, clicking a surface to read its
coordinates, level and cut or fill, a vertical exaggeration slider, saved views and PNG export
with the legend and compass beside the model. Meshes are embedded as base64 binary arrays.
"""

from __future__ import annotations

import base64
import gzip
import html
import json
import os

import numpy as np

from .scene import read_ply

TEMPLATE = os.path.join(os.path.dirname(__file__), "viewer.html")
PHONE_TRIANGLES = 1_500_000          # above this, a lighter copy for phones is written beside the viewer
PHONE_TARGET = 600_000               # triangles the lighter copy aims for
THINNED_ROLES = ("surface", "raster")  # ground only: designs, structures and water keep every triangle


def decimate(xyz, faces, rgb, depth, target):
    """A coarser copy of a mesh by vertex clustering: vertices share a square cell of the plan and are replaced by their
    mean, and triangles that collapse are dropped. The cell grows until at most ``target`` triangles are left.
    Returns (xyz, faces, rgb, depth); a mesh already within the target is returned as it is."""
    faces = np.asarray(faces, np.int64)
    if len(faces) <= target:
        return xyz, faces, rgb, depth
    xyz = np.asarray(xyz, float)
    low, high = xyz[:, :2].min(axis=0), xyz[:, :2].max(axis=0)
    size = max(float(np.prod(np.maximum(high - low, 1e-9))), 1e-9)
    cell = np.sqrt(size / max(target / 2.0, 1.0))                # a regular mesh has about two triangles a cell
    for _attempt in range(24):
        ij = np.floor((xyz[:, :2] - low) / cell).astype(np.int64)
        _unique, cluster = np.unique(ij[:, 0] * (int(ij[:, 1].max()) + 1) + ij[:, 1], return_inverse=True)
        tri = cluster[faces]
        keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
        key = np.sort(tri[keep], axis=1)
        _u, first = np.unique(key, axis=0, return_index=True)    # the same three clusters twice is one triangle
        if len(first) <= target:
            break
        cell *= 1.25
    count = int(cluster.max()) + 1
    weight = np.bincount(cluster, minlength=count).astype(float)

    def mean(values, dtype=float):
        values = np.asarray(values, float)
        if values.ndim == 1:
            return (np.bincount(cluster, weights=values, minlength=count) / weight).astype(dtype)
        return np.column_stack([np.bincount(cluster, weights=values[:, k], minlength=count) / weight
                                for k in range(values.shape[1])]).astype(dtype)

    new_faces = tri[keep][first]
    return (mean(xyz), new_faces, np.rint(mean(rgb)).astype(np.uint8), None if depth is None else mean(depth))


_TYPES = {"<f4": "f4", "<u4": "u4", "u1": "u1"}


def _b64(array, dtype, delta=False):
    """An array packed for the viewer: stored one component after another, the bytes of each value grouped by position
    (floats and indices then compress far better), indices as zig-zag deltas, then gzipped and base64-encoded.
    Lossless: the viewer gets back exactly the array written."""
    data = np.ascontiguousarray(array, dtype=dtype)
    components = data.shape[1] if data.ndim == 2 else 1
    flat = data.T.ravel() if components > 1 else data.ravel()
    if delta:
        steps = np.diff(np.concatenate([[0], flat.astype(np.int64)]))
        flat = ((steps << 1) ^ (steps >> 63)).astype("<u4")
    size = flat.dtype.itemsize
    planes = flat.view(np.uint8).reshape(-1, size).T.tobytes() if size > 1 else flat.tobytes()
    return {"z": base64.b64encode(gzip.compress(planes, 6, mtime=0)).decode("ascii"), "t": _TYPES[dtype],
            "c": components, "d": int(bool(delta))}


def unpack(packed):
    """The array back from ``_b64`` (used by the tests and by tools reading a viewer file)."""
    if isinstance(packed, str):
        return np.frombuffer(base64.b64decode(packed), np.uint8)
    dtype = {"f4": "<f4", "u4": "<u4", "u1": "u1"}[packed["t"]]
    raw = np.frombuffer(gzip.decompress(base64.b64decode(packed["z"])), np.uint8)
    size = np.dtype(dtype).itemsize
    flat = raw.reshape(size, -1).T.copy().view(dtype).ravel() if size > 1 else raw.copy()
    if packed["d"]:
        steps = flat.astype(np.int64)
        flat = np.cumsum((steps >> 1) ^ -(steps & 1)).astype(dtype)
    components = packed["c"]
    return flat.reshape(components, -1).T.copy() if components > 1 else flat


def _role(scene, surface_id):
    return next(item["role"] for item in scene["surfaces"] if item["id"] == surface_id)


def write_viewer(folder, scene, path=None, title=None, max_triangles=None):
    """Write ``viewer.html`` (or ``path``) for the scene in ``folder``; returns the file path.

    With ``max_triangles`` the ground meshes (not designs, structures or water) are thinned in proportion until the scene is
    within it: the file for a phone, where a big scene is too heavy to draw."""
    data = dict(scene)
    data["title"] = title or scene.get("title") or (scene.get("source") or {}).get("file") or "3D scene"
    meshes = {}
    loaded = {surface["id"]: read_ply(os.path.join(folder, surface["mesh"]), extras=True) for surface in scene["surfaces"]} \
        if max_triangles else None
    share = 1.0
    if loaded:
        thin = sum(len(v[1]) for sid, v in loaded.items() if _role(scene, sid) in THINNED_ROLES)
        fixed = sum(len(v[1]) for sid, v in loaded.items() if _role(scene, sid) not in THINNED_ROLES)
        share = min(1.0, max(max_triangles - fixed, 0.05 * max_triangles) / max(thin, 1))
    for surface in scene["surfaces"]:
        if loaded:
            xyz, faces, rgb, depth, extra = loaded[surface["id"]]
            if surface["role"] in THINNED_ROLES and share < 1.0:
                xyz, faces, rgb, depth = decimate(xyz, faces, rgb, depth, int(len(faces) * share))
                extra = {}
        else:
            xyz, faces, rgb, depth, extra = read_ply(os.path.join(folder, surface["mesh"]), extras=True)
        meshes[surface["id"]] = {
            "positions": _b64(xyz, "<f4"), "colors": _b64(rgb, "u1"), "indices": _b64(np.asarray(faces).ravel(), "<u4", delta=True),
            "depth": _b64(depth, "<f4") if np.isfinite(depth).any() else None,
        }
        if "station" in extra and "offset" in extra:
            meshes[surface["id"]]["road"] = _b64(np.column_stack([extra["station"], extra["offset"]]), "<f4")
    data["meshes"] = meshes
    trees = scene.get("vegetation")
    if trees and trees.get("file"):
        table = np.loadtxt(os.path.join(folder, trees["file"]), delimiter=",", skiprows=1, ndmin=2)
        kinds = table[:, 5] if table.shape[1] > 5 else np.zeros(len(table))      # older scenes: everything is grass
        data["vegetation"] = {"count": int(len(table)), "positions": _b64(table[:, :3], "<f4"), "sizes": _b64(table[:, 3:5], "<f4"),
                              "kinds": _b64(kinds, "u1")}
    with open(TEMPLATE, encoding="utf-8") as stream:
        template = stream.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    text = template.replace("__TITLE__", html.escape(data["title"])).replace("__SCENE__", payload)
    path = path or os.path.join(folder, "viewer.html")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)
    return path
