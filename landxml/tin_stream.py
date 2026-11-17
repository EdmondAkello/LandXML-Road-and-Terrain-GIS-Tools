"""Memory-bounded reading of LandXML TIN surfaces.

A Civil 3D surface export can hold tens of millions of ``<P>`` points and
``<F>`` faces. Building a Python object for every one of them (what a DOM
parse does) needs roughly 17 bytes of RAM per byte of file, so a 1.8 GB export
cannot be opened. This module keeps the DOM for everything else in the file and
reads the TIN blocks straight from disk into NumPy arrays:

* :func:`open_skeleton` gives the hardened parser a copy of the file in which
  the *contents* of every ``<Pnts>``, ``<Faces>`` and source ``<Contours>`` block are removed and
  replaced by an attribute holding the block's byte span. The parser (and its
  DOCTYPE/entity checks) sees only the small remainder.
* :func:`read_points` and :func:`read_faces` read one block in bounded chunks,
  turn the text into numbers with a vectorised fast path, and fall back to an
  element-by-element path (with the exact error messages of the original
  reader) for anything unusual.
* The point and face text is never given to an XML parser, so entity tricks in
  the blocks have nothing to expand; every number is validated.
"""

from __future__ import annotations

import mmap
import re
import warnings

import numpy as np

SPAN_ATTRIBUTE = "landxmltools_span"
# Blocks whose contents stay on disk. Pnts and Faces are the TIN itself; Contours is the
# surface's source contour data (often hundreds of MB of text in one element), which no
# tool reads.
REGION_NAMES = (b"Pnts", b"Faces", b"Contours")
BLOCK_BYTES = 48 << 20            # bytes of TIN text parsed at a time
_NAME = rb"(?:[A-Za-z_][\w.\-]*:)?"
_TAG_PREFIX = re.compile(rb"<(?:[A-Za-z_][\w.\-]*:)?$")
_END_PREFIX = re.compile(rb"</(?:[A-Za-z_][\w.\-]*:)?$")
_START_TAIL = re.compile(rb"""(?:[^>"']|"[^"]*"|'[^']*')*>""")
_CLOSE_TAIL = re.compile(rb"\s*>")
_FAST_POINT = re.compile(rb"<" + _NAME + rb"""P\s+id\s*=\s*"(\d+)"\s*>([^<]*)</""" + _NAME + rb"P\s*>")
_ATTR_FACE = re.compile(rb"<" + _NAME + rb"F(?:\s[^>]*)?>([^<]*)</" + _NAME + rb"F\s*>")
_HIDDEN_FACE = re.compile(
    rb"<" + _NAME + rb"""F\s+[^>]*?\bi\s*=\s*(?:"\s*1\s*"|'\s*1\s*')[^>]*>[^<]*</""" + _NAME + rb"F\s*>")
_END_TAGS = re.compile(rb"</")
_ANY_POINT = re.compile(rb"<" + _NAME + rb"P(?:\s([^>]*))?>([^<]*)</" + _NAME + rb"P\s*>")
_ANY_FACE = re.compile(rb"<" + _NAME + rb"F(?:\s([^>]*))?>([^<]*)</" + _NAME + rb"F\s*>")
_ID_ATTR = re.compile(rb"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_HIDDEN_ATTR = re.compile(rb"""\bi\s*=\s*(?:"([^"]*)"|'([^']*)')""")


class TinError(ValueError):
    """Raised for malformed TIN data; the message names the surface and element."""


# --------------------------------------------------------------------------
# Skeleton: the file without the contents of <Pnts> and <Faces>
# --------------------------------------------------------------------------

def _is_text_compatible(head: bytes) -> bool:
    """True for ASCII/UTF-8/Latin-1 style files (not UTF-16/32), so bytes can be searched."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        return False
    return b"\x00" not in head[:64]


def _find_regions(mm):
    """Yield (name, tag_start, gt_index, content_start, content_end, end_close) for each TIN block."""
    size = len(mm)
    names = REGION_NAMES
    nxt = {n: mm.find(n, 0) for n in names}
    pos = 0
    while True:
        live = [(i, n) for n, i in nxt.items() if i >= 0]
        if not live:
            return
        i, name = min(live)
        nxt[name] = mm.find(name, i + len(name))
        for other, at in list(nxt.items()):
            if 0 <= at < i + len(name):
                nxt[other] = mm.find(other, i + len(name))
        if i < pos:
            continue
        before = bytes(mm[max(0, i - 80):i])
        match = _TAG_PREFIX.search(before)
        after = mm[i + len(name):i + len(name) + 1]
        if match is None or after not in (b" ", b"\t", b"\r", b"\n", b">", b"/"):
            continue
        tag_start = i - (len(before) - match.start())
        tail = _START_TAIL.match(bytes(mm[i + len(name):i + len(name) + 8192]))
        if tail is None:
            continue
        gt = i + len(name) + tail.end() - 1
        if mm[gt - 1:gt] == b"/":
            continue                                   # self-closing: no content
        content_start = gt + 1
        j = mm.find(name, content_start)
        end = None
        while j >= 0:
            back = bytes(mm[max(0, j - 80):j])
            e = _END_PREFIX.search(back)
            close = _CLOSE_TAIL.match(bytes(mm[j + len(name):j + len(name) + 64]))
            if e is not None and close is not None:
                end = (j - (len(back) - e.start()), j + len(name) + close.end())
                break
            j = mm.find(name, j + len(name))
        if end is None:
            continue
        pos = end[1]
        yield name, tag_start, gt, content_start, end[0], end[1]


class _SkeletonReader:
    """File-like object for the hardened parser: the file minus TIN contents."""

    def __init__(self, path):
        self._fh = open(path, "rb")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):                   # empty file or no mmap support
            self._fh.close()
            raise
        self._chunks = self._generate()
        self._buffer = b""
        self.regions = 0

    def _generate(self):
        mm = self._mm
        emit = 0
        step = 1 << 20
        for name, _start, gt, content_start, content_end, end_close in _find_regions(mm):
            self.regions += 1
            for k in range(emit, gt, step):
                yield mm[k:min(k + step, gt)]
            yield (' %s="%d:%d">' % (SPAN_ATTRIBUTE, content_start, content_end)).encode("ascii")
            yield mm[content_end:end_close]
            emit = end_close
        for k in range(emit, len(mm), step):
            yield mm[k:min(k + step, len(mm))]

    def read(self, size=-1):
        while size < 0 or len(self._buffer) < size:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        if size < 0:
            data, self._buffer = self._buffer, b""
        else:
            data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def close(self):
        self._chunks = iter(())
        try:
            self._mm.close()
        finally:
            self._fh.close()


def open_skeleton(path):
    """Return a file-like reader of ``path`` with TIN block contents removed, or None.

    None means the file cannot be handled this way (UTF-16 or similar); callers then
    parse the whole file.
    """
    with open(path, "rb") as probe:
        head = probe.read(256)
    if not head or not _is_text_compatible(head):
        return None
    return _SkeletonReader(path)


def span_of(element):
    """Byte span (start, end) of a skeleton ``<Pnts>``/``<Faces>`` element, or None."""
    text = element.attrib.get(SPAN_ATTRIBUTE) if element is not None else None
    if not text:
        return None
    start, end = text.split(":")
    return int(start), int(end)


# --------------------------------------------------------------------------
# Block reading
# --------------------------------------------------------------------------

def _blocks(path, span, block_bytes=None, cancel=None):
    """Yield bytes of whole elements covering ``span``, never splitting an element."""
    block_bytes = block_bytes or BLOCK_BYTES
    start, end = span
    tail = b""
    with open(path, "rb") as handle:
        handle.seek(start)
        position = start
        while position < end:
            if cancel and cancel():
                raise RuntimeError("Cancelled while reading the TIN.")
            chunk = handle.read(min(block_bytes, end - position))
            if not chunk:
                break
            position += len(chunk)
            data = tail + chunk
            if position < end:
                cut = _last_complete(data)
                if cut <= 0:
                    tail = data
                    continue
                yield data[:cut]
                tail = data[cut:]
            else:
                yield data
                tail = b""
        if tail.strip():
            yield tail


def _last_complete(data):
    """Index just after the last complete end tag in ``data`` (0 if none)."""
    i = data.rfind(b"</")
    while i >= 0:
        j = data.find(b">", i)
        if j >= 0:
            return j + 1
        i = data.rfind(b"</", 0, i)
    return 0


def _numbers(text, dtype):
    """Whitespace-separated numbers to an array; raises ValueError on any junk."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            return np.fromstring(text, dtype=dtype, sep=" ")
        except (DeprecationWarning, RuntimeWarning) as exc:
            raise ValueError(str(exc)) from exc


def count_elements(path, span, cancel=None):
    """Number of elements in a block (one end tag each)."""
    if span is None:
        return 0
    total = 0
    for block in _blocks(path, span, cancel=cancel):
        total += block.count(b"</")
    return total


def _points_block_slow(block, ordinal, surface):
    rows = []
    for k, match in enumerate(_ANY_POINT.finditer(block), start=ordinal + 1):
        attrs = match.group(1) or b""
        found = _ID_ATTR.search(attrs)
        try:
            pid = int((found.group(1) if found.group(1) is not None else found.group(2)).strip())
            values = [float(v) for v in match.group(2).split()]
            if len(values) != 3 or not all(np.isfinite(values)):
                raise ValueError
        except Exception as exc:
            raise TinError(f"Invalid LandXML point at position {k}.") from exc
        rows.append((pid, *values))
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


def iter_points(path, span, surface_name="", progress=None, cancel=None, block_bytes=None):
    """Yield (ids int64[k], xyz float64[k,3]) for each block of a ``<Pnts>`` span."""
    ordinal = 0
    total = max(span[1] - span[0], 1)
    done = 0
    for block in _blocks(path, span, block_bytes, cancel):
        count = block.count(b"</")
        array = None
        text, replaced = _FAST_POINT.subn(rb"\1 \2 ", block)
        if replaced == count and b"<" not in text:
            try:
                flat = _numbers(text, np.float64)
                if flat.size == 4 * count:
                    array = flat.reshape(-1, 4)
                    if not np.isfinite(array[:, 1:]).all():
                        array = None
            except ValueError:
                array = None
        if array is None:
            array = _points_block_slow(block, ordinal, surface_name)
        ordinal += len(array)
        done += len(block)
        if progress:
            progress(done / total, f"Reading points of {surface_name}: {ordinal:,}")
        yield array[:, 0].astype(np.int64), array[:, 1:]


def read_points(path, span, surface_name="", progress=None, cancel=None, block_bytes=None):
    """Return (ids int64[n], xyz float64[n,3]) for a ``<Pnts>`` block."""
    ids, parts = [], []
    for block_ids, block_xyz in iter_points(path, span, surface_name, progress, cancel, block_bytes):
        ids.append(block_ids)
        parts.append(block_xyz)
    if not parts:
        return np.empty(0, np.int64), np.empty((0, 3))
    return np.concatenate(ids), np.concatenate(parts)


def points_extent(path, span, cancel=None):
    """(xmin, ymin, zmin, xmax, ymax, zmax, count) of a ``<Pnts>`` span without keeping the points."""
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    count = 0
    for _ids, xyz in iter_points(path, span, "", None, cancel):
        low = np.minimum(low, xyz.min(axis=0))
        high = np.maximum(high, xyz.max(axis=0))
        count += len(xyz)
    return (*low, *high, count) if count else None


def _faces_block_slow(block, ordinal, surface, include_invisible):
    rows, hidden = [], 0
    for k, match in enumerate(_ANY_FACE.finditer(block), start=ordinal + 1):
        attrs = match.group(1) or b""
        flag = _HIDDEN_ATTR.search(attrs)
        if not include_invisible and flag is not None:
            value = flag.group(1) if flag.group(1) is not None else flag.group(2)
            if value.strip() == b"1":
                hidden += 1
                continue
        values = match.group(2).split()
        if len(values) != 3:
            raise TinError(f"Surface '{surface}' face {k} must reference exactly three point IDs.")
        try:
            rows.append([int(v) for v in values])
        except ValueError as exc:
            raise TinError(f"Surface '{surface}' face {k} contains a noninteger point ID.") from exc
    return np.array(rows, dtype=np.int64).reshape(-1, 3), hidden


def _faces_in_block(block, ordinal, surface, include_invisible):
    """(refs int64[m,3], hidden count, elements in block) for one block of faces."""
    elements = block.count(b"</")
    hidden = 0
    work = block
    if not include_invisible and b" i" in block:
        work, hidden = _HIDDEN_FACE.subn(b"", block)
    text, replaced = _ATTR_FACE.subn(rb"\1 ", work)
    if replaced == elements - hidden and b"<" not in text:
        try:
            flat = _numbers(text, np.int64)
        except ValueError:
            flat = None
        if flat is not None and flat.size == 3 * replaced:
            return flat.reshape(-1, 3), hidden, elements
    refs, hidden_slow = _faces_block_slow(block, ordinal, surface, include_invisible)
    return refs, hidden_slow, elements


def read_faces(path, span, ids, surface_name="", include_invisible=False, progress=None,
               cancel=None, block_bytes=None, inside=None):
    """Return (faces int32[m,3] as indices into the id order of ``ids``, hidden count, element count).

    ``inside`` is an optional boolean array over the points; when given only faces with at
    least one vertex inside are kept (the crop).
    """
    first = int(ids[0]) if len(ids) else 0
    sequential = bool(len(ids)) and bool(np.array_equal(ids, np.arange(first, first + len(ids))))
    if not sequential:
        order = np.argsort(ids, kind="stable")
        sorted_ids = ids[order]
    kept, hidden_total, ordinal = [], 0, 0
    total = max(span[1] - span[0], 1)
    done = 0
    for block in _blocks(path, span, block_bytes, cancel):
        refs, hidden, elements = _faces_in_block(block, ordinal, surface_name, include_invisible)
        hidden_total += hidden
        if refs.size:
            if sequential:
                index = refs - first
                bad = (index < 0) | (index >= len(ids))
            else:
                pos = np.searchsorted(sorted_ids, refs)
                pos[pos >= len(ids)] = 0
                bad = sorted_ids[pos] != refs
                index = order[pos]
            if bad.any():
                _raise_missing(path, span, surface_name, include_invisible, ids, refs, bad)
            if inside is not None:
                keep = inside[index].any(axis=1)
                index = index[keep]
            kept.append(index.astype(np.int32))
        ordinal += elements
        done += len(block)
        if progress:
            progress(done / total, f"Reading triangles of {surface_name}: {ordinal:,}")
    faces = np.concatenate(kept) if kept else np.empty((0, 3), np.int32)
    return faces, hidden_total, ordinal


def _raise_missing(path, span, surface_name, include_invisible, ids, refs, bad):
    """Find the first offending face (counting hidden ones, as the original reader did)."""
    known = set(ids.tolist())
    ordinal = 0
    for block in _blocks(path, span):
        for match in _ANY_FACE.finditer(block):
            ordinal += 1
            attrs = match.group(1) or b""
            flag = _HIDDEN_ATTR.search(attrs)
            if not include_invisible and flag is not None and (flag.group(1) or flag.group(2) or b"").strip() == b"1":
                continue
            for v in match.group(2).split()[:3]:
                if int(v) not in known:
                    raise TinError(f"Face {ordinal} references missing point ID {int(v)}.")
    raise TinError("A face references a missing point ID.")


def _check_unique(ids, surface_name):
    """Raise for the first repeated point id in file order, as the element-wise reader did."""
    first = int(ids[0])
    if np.array_equal(ids, np.arange(first, first + len(ids))):
        return
    order = np.argsort(ids, kind="stable")
    repeated = ids[order][1:] == ids[order][:-1]
    if repeated.any():
        later = order[1:][repeated].min()
        raise TinError(f"Surface '{surface_name}' has duplicate point ID {int(ids[later])}.")


def read_tin_arrays(path, pnts_span, faces_span, surface_name="", include_invisible=False,
                    bounds=None, progress=None, cancel=None, block_bytes=None):
    """Read one surface from its byte spans.

    Returns ``(xyz, faces, info)`` with ``info`` holding ``invisible_face_count``,
    ``source_point_count``, ``source_face_count`` and ``cropped`` (bool). With ``bounds``
    only triangles touching the box are kept and the vertices are renumbered.
    """
    ids, xyz = read_points(path, pnts_span, surface_name,
                           (lambda f, m: progress(f * 0.3, m)) if progress else None, cancel, block_bytes)
    if not len(ids):
        raise TinError(f"Surface '{surface_name}' contains no TIN points.")
    _check_unique(ids, surface_name)
    inside = None
    if bounds is not None:
        x0, y0, x1, y1 = bounds
        inside = (xyz[:, 0] >= x0) & (xyz[:, 0] <= x1) & (xyz[:, 1] >= y0) & (xyz[:, 1] <= y1)
    faces, hidden, elements = read_faces(
        path, faces_span, ids, surface_name, include_invisible,
        (lambda f, m: progress(0.3 + 0.65 * f, m)) if progress else None, cancel, block_bytes, inside)
    if elements == 0:
        raise TinError(f"Surface '{surface_name}' contains no TIN faces.")
    info = {"invisible_face_count": hidden, "source_point_count": len(ids),
            "source_face_count": elements, "cropped": bounds is not None}
    if bounds is not None and len(faces):
        used = np.unique(faces)
        remap = np.full(len(ids), -1, np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        faces = remap[faces]
        xyz = xyz[used]
    elif bounds is not None:
        xyz = xyz[:0]
    if not len(faces):
        raise TinError("No visible triangular faces were found." if bounds is None else
                       "No triangular faces touch the requested area.")
    return xyz, faces, info
