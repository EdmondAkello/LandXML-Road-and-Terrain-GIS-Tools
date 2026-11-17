"""Write a scene as one self-contained HTML file with the plugin's own WebGL viewer.

The viewer (``viewer.html``, plain JavaScript and WebGL 2, no libraries, no network) opens
offline in any current browser: rotate, pan and zoom, layer switches and opacity, a legend
panel beside the model, a compass that turns with the camera, clicking a surface to read its
coordinates, level and cut or fill, a vertical exaggeration slider, saved views and PNG export
with the legend and compass beside the model. Meshes are embedded as base64 binary arrays.
"""

from __future__ import annotations

import base64
import html
import json
import os

import numpy as np

from .scene import read_ply

TEMPLATE = os.path.join(os.path.dirname(__file__), "viewer.html")


def _b64(array, dtype):
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


def write_viewer(folder, scene, path=None, title=None):
    """Write ``viewer.html`` (or ``path``) for the scene in ``folder``; returns the file path."""
    data = dict(scene)
    data["title"] = title or scene.get("title") or (scene.get("source") or {}).get("file") or "3D scene"
    meshes = {}
    for surface in scene["surfaces"]:
        xyz, faces, rgb, depth = read_ply(os.path.join(folder, surface["mesh"]))
        meshes[surface["id"]] = {
            "positions": _b64(xyz, "<f4"), "colors": _b64(rgb, "u1"), "indices": _b64(faces, "<u4"),
            "depth": _b64(depth, "<f4") if np.isfinite(depth).any() else None,
        }
    data["meshes"] = meshes
    with open(TEMPLATE, encoding="utf-8") as stream:
        template = stream.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    text = template.replace("__TITLE__", html.escape(data["title"])).replace("__SCENE__", payload)
    path = path or os.path.join(folder, "viewer.html")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)
    return path
