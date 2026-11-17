"""Optional binary cache of large TIN surfaces, in the system temporary folder only.

Reading a few million points from LandXML text takes tens of seconds; the same arrays load
from a binary copy in about a second. Entries are keyed by the source file's real path,
size and modification time, the surface's byte spans and the hidden-face choice, so a
changed file is never served from the cache. Nothing is written beside the source file.

The cache is on by default for surfaces whose point block is at least ``MIN_BYTES``. Turn
it off with the QGIS setting ``LandXMLTools/tinCache`` = false or the environment variable
``LANDXMLTOOLS_TIN_CACHE=0``. The folder keeps at most ``MAX_BYTES``; the least recently
used entries are removed first. Any cache error falls back to reading the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

import numpy as np

VERSION = 1
MIN_BYTES = 16 << 20
MAX_BYTES = 4 << 30


def folder():
    return os.path.join(tempfile.gettempdir(), "LandXMLTools", "tin-cache")


def enabled():
    if os.environ.get("LANDXMLTOOLS_TIN_CACHE", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        from qgis.PyQt.QtCore import QSettings
        value = QSettings().value("LandXMLTools/tinCache", True)
    except Exception:  # nosec B110 - outside QGIS the environment variable decides
        return True
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def wanted(spans):
    return enabled() and spans[0][1] - spans[0][0] >= MIN_BYTES


def _key(path, spans, include_invisible):
    info = os.stat(path)
    text = json.dumps([VERSION, os.path.realpath(path), info.st_size, info.st_mtime_ns,
                       list(spans[0]), list(spans[1]), bool(include_invisible)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def load(path, spans, include_invisible):
    """(xyz, faces, info) from the cache, or None. Arrays are memory-mapped read-only."""
    try:
        base = os.path.join(folder(), _key(path, spans, include_invisible))
        with open(base + ".json", encoding="utf-8") as stream:
            info = json.load(stream)
        xyz = np.load(base + ".xyz.npy", mmap_mode="r")
        faces = np.load(base + ".faces.npy", mmap_mode="r")
        if xyz.ndim != 2 or xyz.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3 or \
                len(xyz) != info.get("points") or len(faces) != info.get("faces"):
            return None
        for suffix in (".json", ".xyz.npy", ".faces.npy"):
            os.utime(base + suffix)
        return xyz, faces, info["info"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(path, spans, include_invisible, xyz, faces, info):
    """Write an entry (atomically) and trim the folder; errors are ignored."""
    try:
        directory = folder()
        os.makedirs(directory, exist_ok=True)
        base = os.path.join(directory, _key(path, spans, include_invisible))
        for suffix, array in ((".xyz.npy", np.ascontiguousarray(xyz, np.float64)),
                              (".faces.npy", np.ascontiguousarray(faces, np.int32))):
            temporary = base + suffix + ".part"
            with open(temporary, "wb") as stream:
                np.save(stream, array)
            os.replace(temporary, base + suffix)
        temporary = base + ".json.part"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"points": len(xyz), "faces": len(faces), "info": info,
                       "source": os.path.basename(path)}, stream)
        os.replace(temporary, base + ".json")
        trim()
    except (OSError, ValueError):
        pass


def trim(limit=None):
    """Remove the least recently used entries until the folder is under ``limit`` bytes."""
    limit = MAX_BYTES if limit is None else limit
    try:
        entries = {}
        for name in os.listdir(folder()):
            full = os.path.join(folder(), name)
            key = name.split(".", 1)[0]
            stat = os.stat(full)
            size, used = entries.get(key, (0, 0))
            entries[key] = (size + stat.st_size, max(used, stat.st_mtime))
        total = sum(size for size, _used in entries.values())
        for key, (size, _used) in sorted(entries.items(), key=lambda item: item[1][1]):
            if total <= limit:
                break
            for name in os.listdir(folder()):
                if name.startswith(key + "."):
                    os.remove(os.path.join(folder(), name))
            total -= size
    except OSError:
        pass


def clear():
    trim(0)
