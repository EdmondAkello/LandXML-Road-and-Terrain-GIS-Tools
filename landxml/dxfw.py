"""A small DXF (R2018 / AC1032) writer and reader (from the Road Furniture Designer plugin, by the same author; its
files open in AutoCAD and Civil 3D), with 3D polylines and 3D faces added for alignments, feature lines and TINs.

The drawing starts from a template (data/RSD_template.dxf: an empty R2018 drawing with the standard
tables, objects and layouts, built once by tools/build_dxf_template.py) and adds layers, linetypes,
blocks and model space entities with fresh handles. Sign blocks are copied from the block library
(data/RSD_SignBlocks.dxf) tag by tag. Entities: LINE, CIRCLE, POINT, LWPOLYLINE, TEXT, MTEXT, solid
HATCH (polyline boundaries), SOLID, INSERT with ATTRIBs, ATTDEF; XDATA on any entity.

No third-party library: the plugin ships without bundled packages. The tests check every file
written here with ezdxf (a development dependency), including its audit.
"""
from __future__ import annotations

import math
import os

Tag = tuple  # (group code: int, value: str)


# ------------------------------------------------------------------ reading
def read_tags(path_or_text: str):
    """(code, value) pairs of an ASCII DXF, from a path or the file's text."""
    if "\n" not in path_or_text and os.path.exists(path_or_text):
        with open(path_or_text, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = path_or_text
    lines = text.splitlines()
    out = []
    for i in range(0, len(lines) - 1, 2):
        try:
            out.append((int(lines[i].strip()), lines[i + 1]))
        except ValueError:
            break
    return out


def split_entities(tags):
    """Group a flat tag list into entities, each starting at a (0, TYPE) tag."""
    ents, cur = [], None
    for t in tags:
        if t[0] == 0:
            cur = [t]
            ents.append(cur)
        elif cur is not None:
            cur.append(t)
    return ents


def sections(tags):
    """{section name: tags between (2, NAME) and ENDSEC}."""
    out, i = {}, 0
    while i < len(tags):
        if tags[i] == (0, "SECTION"):
            name = tags[i + 1][1]
            j = i + 2
            while tags[j] != (0, "ENDSEC"):
                j += 1
            out[name] = tags[i + 2:j]
            i = j
        i += 1
    return out


def blocks_of(tags):
    """{block name: [entity tag lists]} from the BLOCKS section (BLOCK/ENDBLK themselves excluded)."""
    out, name = {}, None
    for e in split_entities(sections(tags).get("BLOCKS", [])):
        if e[0][1] == "BLOCK":
            name = value(e, 2)
            out[name] = []
        elif e[0][1] == "ENDBLK":
            name = None
        elif name is not None:
            out[name].append(e)
    return out


def value(ent, code, default=None):
    for c, v in ent[1:]:
        if c == code:
            return v
    return default


def fvalue(ent, code, default=0.0):
    v = value(ent, code)
    return float(v) if v is not None else default


def values(ent, code):
    return [v for c, v in ent[1:] if c == code]


def xdata(ent, appid):
    """The values after (1001, appid) up to the next 1001, as (code, value) pairs."""
    out, on = [], False
    for c, v in ent[1:]:
        if c == 1001:
            on = v == appid
            continue
        if on and c >= 1000:
            out.append((c, v))
    return out


def body(ent):
    """The entity's tags after its type, without handle, owner, reactors or extension dictionary
    (a copy gets new ones), and without XDATA."""
    out, skip = [], False
    for c, v in ent[1:]:
        if c == 102:
            skip = v.startswith("{")
            continue
        if skip or c in (5, 330, 360) or c >= 1000:
            continue
        out.append((c, v))
    return out


# ------------------------------------------------------------------ geometry of copied entities
def points(ent):
    """The 2D points an entity covers (for extents): vertices, ends, circle boxes, text inserts."""
    t = ent[0][1]
    pts = []
    if t == "CIRCLE":
        x, y, r = fvalue(ent, 10), fvalue(ent, 20), fvalue(ent, 40)
        return [(x - r, y - r), (x + r, y + r)]
    if t in ("TEXT", "ATTDEF", "ATTRIB", "MTEXT"):
        x, y, h = fvalue(ent, 10), fvalue(ent, 20), fvalue(ent, 40, 1.0)
        n = len(value(ent, 1, "") or "")
        return [(x, y), (x + 0.6 * h * n, y + h)]
    xs, ys = values(ent, 10), values(ent, 20)
    if t in ("LINE",):
        xs, ys = xs + values(ent, 11), ys + values(ent, 21)
    if t == "SOLID":
        for a, b in ((11, 21), (12, 22), (13, 23)):
            xs, ys = xs + values(ent, a), ys + values(ent, b)
    if t == "HATCH":                                        # skip the elevation point (first 10/20)
        xs, ys = xs[1:], ys[1:]
    for x, y in zip(xs, ys):
        pts.append((float(x), float(y)))
    return pts


def extents(ents):
    pts = [p for e in ents for p in points(e)]
    if not pts:
        return None
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


# ------------------------------------------------------------------ writing
def _f(v: float) -> str:
    return repr(float(v))


def rgb_int(rgb) -> int:
    r, g, b = rgb
    return (int(r) << 16) | (int(g) << 8) | int(b)


class Entity:
    """An entity as tags after its type and before XDATA; handle and owner are given on writing."""

    def __init__(self, dxftype, tags, layer="0", xdata=None, attribs=None):
        self.dxftype = dxftype
        self.tags = list(tags)
        self.layer = layer
        self.xdata = list(xdata or [])
        self.attribs = attribs           # INSERT: list of ATTRIB entities (written with a SEQEND)

    @classmethod
    def copy_of(cls, ent):
        """An entity from read tags (a library block): its body, with layer kept inside the tags."""
        return cls(ent[0][1], body(ent), layer=None)

    def get(self, code, default=None):
        for c, v in self.tags:
            if c == code:
                return v
        return default

    def set(self, code, val):
        for i, (c, _v) in enumerate(self.tags):
            if c == code:
                self.tags[i] = (code, val)
                return
        raise KeyError(code)

    def set_xdata(self, appid, items):
        self.xdata = [(1001, appid)] + [(c, v) for c, v in items]


def _common(layer, linetype=None, color=None, rgb=None):
    t = [(100, "AcDbEntity"), (8, layer)]
    if linetype:
        t.append((6, linetype))
    if color is not None:
        t.append((62, str(int(color))))
    if rgb is not None:
        t.append((420, str(rgb_int(rgb))))
    return t


def _xyz(code, p):
    return [(code, _f(p[0])), (code + 10, _f(p[1])), (code + 20, _f(p[2] if len(p) > 2 else 0.0))]


# halign (72), valign (73 for TEXT, 74 for ATTDEF/ATTRIB)
ALIGN = {"LEFT": (0, 0), "MIDDLE_LEFT": (0, 2), "MIDDLE_RIGHT": (2, 2), "MIDDLE_CENTER": (1, 2),
         "TOP_CENTER": (1, 3)}


class Layout:
    """Model space or a block: add_* return the Entity so callers can set XDATA."""

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.entities = []

    def add(self, ent):
        self.entities.append(ent)
        return ent

    def add_line(self, a, b, layer="0", rgb=None):
        return self.add(Entity("LINE", _common(layer, rgb=rgb) + [(100, "AcDbLine")] + _xyz(10, a) + _xyz(11, b),
                               layer))

    def add_circle(self, c, r, layer="0", rgb=None):
        return self.add(Entity("CIRCLE", _common(layer, rgb=rgb) + [(100, "AcDbCircle")] + _xyz(10, c)
                               + [(40, _f(r))], layer))

    def add_point(self, p, layer="0"):
        return self.add(Entity("POINT", _common(layer) + [(100, "AcDbPoint")] + _xyz(10, p), layer))

    def add_lwpolyline(self, pts, close=False, layer="0", linetype=None, const_width=None, rgb=None):
        # flag 128 (linetype generation): the dash pattern runs on along the whole polyline instead of
        # restarting at every vertex, so a dashed marking with a vertex every 2 m still shows its dashes
        t = _common(layer, linetype=linetype, rgb=rgb) + [(100, "AcDbPolyline"), (90, str(len(pts))),
                                                           (70, str((1 if close else 0) | 128))]
        if const_width:
            t.append((43, _f(const_width)))
        for p in pts:
            t += [(10, _f(p[0])), (20, _f(p[1]))]
        return self.add(Entity("LWPOLYLINE", t, layer))

    def add_text(self, text, insert, height, layer="0", rotation=0.0, style=None, align=None, rgb=None):
        t = _common(layer, rgb=rgb) + [(100, "AcDbText")] + _xyz(10, insert) + [(40, _f(height)), (1, text)]
        if rotation:
            t.append((50, _f(rotation)))
        if style:
            t.append((7, style))
        h, v = ALIGN[align] if align else (0, 0)
        if h:
            t.append((72, str(h)))
        if align:
            t += _xyz(11, insert)
        t.append((100, "AcDbText"))
        if v:
            t.append((73, str(v)))
        return self.add(Entity("TEXT", t, layer))

    def add_mtext(self, text, insert, char_height, layer="0"):
        t = _common(layer) + [(100, "AcDbMText")] + _xyz(10, insert) + [(40, _f(char_height)), (71, "1")]
        chunks = [text[i:i + 250] for i in range(0, len(text), 250)] or [""]
        t += [(3, c) for c in chunks[:-1]] + [(1, chunks[-1])]
        return self.add(Entity("MTEXT", t, layer))

    def add_solid_hatch(self, pts, layer="0", rgb=None, color=None):
        t = _common(layer, color=color if color is not None else (7 if rgb is not None else None), rgb=rgb)
        t += [(100, "AcDbHatch")] + _xyz(10, (0, 0, 0)) + [(210, "0.0"), (220, "0.0"), (230, "1.0"),
                                                           (2, "SOLID"), (70, "1"), (71, "0"), (91, "1"),
                                                           (92, "3"), (72, "0"), (73, "1"), (93, str(len(pts)))]
        for p in pts:
            t += [(10, _f(p[0])), (20, _f(p[1]))]
        t += [(97, "0"), (75, "1"), (76, "1"), (98, "0")]
        return self.add(Entity("HATCH", t, layer))

    def add_attdef(self, tag, insert, height, layer="0", rotation=0.0, invisible=False, align=None):
        t = _common(layer) + [(100, "AcDbText")] + _xyz(10, insert) + [(40, _f(height)), (1, "")]
        if rotation:
            t.append((50, _f(rotation)))
        h, v = ALIGN[align] if align else (0, 0)
        if h:
            t.append((72, str(h)))
        if align:
            t += _xyz(11, insert)
        t += [(100, "AcDbAttributeDefinition"), (3, ""), (2, tag), (70, "1" if invisible else "0")]
        if v:
            t.append((74, str(v)))
        t += [(71, "1"), (72, "0")]
        if align:
            t += _xyz(11, insert)
        self.doc._blocks_with_attdefs.add(self.name)
        return self.add(Entity("ATTDEF", t, layer))

    def add_blockref(self, name, insert, layer="0", rotation=0.0, scale=1.0):
        t = _common(layer) + [(100, "AcDbBlockReference"), (2, name)] + _xyz(10, insert)
        if scale != 1.0:
            t += [(41, _f(scale)), (42, _f(scale)), (43, _f(scale))]
        if rotation:
            t.append((50, _f(rotation)))
        return self.add(Entity("INSERT", t, layer))

    def add_polyline3d(self, pts, close=False, layer="0", color=None):
        """A 3D polyline (POLYLINE with VERTEX children and a SEQEND)."""
        ent = Entity("POLYLINE", _common(layer, color=color) + [(100, "AcDb3dPolyline"), (66, "1")]
                     + _xyz(10, (0.0, 0.0, 0.0)) + [(70, "9" if close else "8")], layer)
        ent.children = [("VERTEX", [(100, "AcDbEntity"), (8, layer), (100, "AcDbVertex"), (100, "AcDb3dPolylineVertex")]
                         + _xyz(10, p) + [(70, "32")]) for p in pts]
        return self.add(ent)

    def add_3dface(self, a, b, c, d=None, layer="0", color=None):
        d = c if d is None else d
        return self.add(Entity("3DFACE", _common(layer, color=color) + [(100, "AcDbFace")] + _xyz(10, a) + _xyz(11, b)
                               + _xyz(12, c) + _xyz(13, d), layer))

    def add_entity(self, ent: Entity):
        return self.add(Entity(ent.dxftype, list(ent.tags), ent.layer))


def _attrib_from(attdef: Entity, text, ins, rot, scale, layer_default):
    """ATTRIB for a block reference at ins (rotation rot degrees, uniform scale) from an ATTDEF."""
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def tr(x, y):
        x, y = x * scale, y * scale
        return (ins[0] + x * c - y * s, ins[1] + x * s + y * c)

    tg = attdef.tags
    layer = attdef.get(8, layer_default)
    p = tr(float(attdef.get(10, 0)), float(attdef.get(20, 0)))
    out = [(100, "AcDbEntity"), (8, layer)]
    for k in (62, 420):
        if attdef.get(k) is not None:
            out.append((k, attdef.get(k)))
    out += [(100, "AcDbText")] + _xyz(10, p) + [(40, _f(float(attdef.get(40, 1.0)) * scale)), (1, text)]
    r = (float(attdef.get(50, 0.0)) + rot) % 360.0
    if r:
        out.append((50, _f(r)))
    if attdef.get(7):
        out.append((7, attdef.get(7)))
    # the alignment point: the first 11/21 inside AcDbText
    seen_def = False
    h = v = 0
    ax = ay = None
    for code, val in tg:
        if code == 100 and val == "AcDbAttributeDefinition":
            seen_def = True
        elif code == 72 and not seen_def:
            h = int(val)
        elif code == 74 and seen_def:
            v = int(val)
        elif code == 11 and not seen_def and ax is None:
            ax = float(val)
        elif code == 21 and not seen_def and ay is None:
            ay = float(val)
    if h:
        out.append((72, str(h)))
    a = tr(ax, ay) if ax is not None else p
    out += _xyz(11, a)
    out += [(100, "AcDbAttribute"), (2, attdef_tag(attdef)), (70, attdef.get_after_def(70, "0"))]
    if v:
        out.append((74, str(v)))
    out += [(71, "1"), (72, "0")] + _xyz(11, a)
    return Entity("ATTRIB", out, layer)


def attdef_tag(attdef: Entity) -> str:
    return attdef.get_tag()


def _get_after_def(self, code, default=None):
    """A tag in the AcDbAttributeDefinition subclass (2 tag, 70 flags) of an ATTDEF."""
    on = False
    for c, v in self.tags:
        if c == 100:
            on = v == "AcDbAttributeDefinition"
        elif on and c == code:
            return v
    return default


Entity.get_after_def = _get_after_def


def _get_tag(self):
    """The tag of an ATTDEF or ATTRIB (group 2 after its AcDbAttribute* subclass marker)."""
    on = False
    for c, v in self.tags:
        if c == 100:
            on = v.startswith("AcDbAttribute")
        elif on and c == 2:
            return v
    return ""


def _set_rotation(self, deg):
    """Set (or add) the text rotation (50) of a TEXT / ATTRIB / ATTDEF, inside its first AcDbText part."""
    for i, (c, _v) in enumerate(self.tags):
        if c == 50:
            self.tags[i] = (50, repr(float(deg)))
            return
    i = next(k for k, (c, v) in enumerate(self.tags) if c == 40)
    self.tags.insert(i + 2, (50, repr(float(deg))))           # after 40 height and 1 text


Entity.get_tag = _get_tag
Entity.set_rotation = _set_rotation


class Drawing:
    """A DXF built on the template: add layers, linetypes, blocks and model space entities, then save."""

    def __init__(self, template: str):
        tags = read_tags(template)
        self._tags = tags
        self.header = {}
        hdr = sections(tags)["HEADER"]
        for i, (c, v) in enumerate(hdr):
            if c == 9:
                self.header[v] = hdr[i + 1][1] if i + 1 < len(hdr) else ""
        self._next = int(self.header.get("$HANDSEED", "FFFF"), 16)
        self._tables = {}                              # table name -> (table handle, [record names])
        tbl = None
        for e in split_entities(sections(tags)["TABLES"]):
            if e[0][1] == "TABLE":
                tbl = value(e, 2)
                self._tables[tbl] = [value(e, 5), []]
            elif e[0][1] != "ENDTAB" and tbl:
                self._tables[tbl][1].append(value(e, 2))
                if tbl == "BLOCK_RECORD" and value(e, 2) == "*Model_Space":
                    self._msp_handle = value(e, 5)
        self._new_records = {"LAYER": [], "LTYPE": [], "BLOCK_RECORD": []}
        self.blocks = {}                               # new blocks: name -> Layout
        self._blocks_with_attdefs = set()
        self.modelspace = Layout(self, "*Model_Space")
        self.custom = []
        self.header_set = {}

    def handle(self) -> str:
        h = "%X" % self._next
        self._next += 1
        return h

    # -- tables
    def has_layer(self, name):
        return name in self._tables["LAYER"][1]

    def add_layer(self, name, color=7, plot=True, linetype="Continuous"):
        if self.has_layer(name):
            return
        self._tables["LAYER"][1].append(name)
        t = [(100, "AcDbSymbolTableRecord"), (100, "AcDbLayerTableRecord"), (2, name), (70, "0"),
             (62, str(int(color))), (6, linetype)]
        if not plot:
            t.append((290, "0"))
        t += [(370, "-3")]
        t += self._layer_tail
        self._new_records["LAYER"].append(("LAYER", t))

    @property
    def _layer_tail(self):
        """390 plot style / 347 material handles, as on the template's layer 0."""
        if not hasattr(self, "_lt"):
            self._lt = []
            for e in split_entities(sections(self._tags)["TABLES"]):
                if e[0][1] == "LAYER" and value(e, 2) == "0":
                    self._lt = [(c, v) for c, v in e[1:] if c in (390, 347)]
                    break
        return self._lt

    def has_linetype(self, name):
        return name in self._tables["LTYPE"][1]

    def add_linetype(self, name, pattern, description=""):
        """pattern: [total length, dash, -gap, ...] as ezdxf."""
        if self.has_linetype(name):
            return
        self._tables["LTYPE"][1].append(name)
        elems = pattern[1:]
        t = [(100, "AcDbSymbolTableRecord"), (100, "AcDbLinetypeTableRecord"), (2, name), (70, "0"),
             (3, description), (72, "65"), (73, str(len(elems))), (40, _f(pattern[0]))]
        for d in elems:
            t += [(49, _f(d)), (74, "0")]
        self._new_records["LTYPE"].append(("LTYPE", t))

    def has_block(self, name):
        return name in self.blocks or name in self._tables["BLOCK_RECORD"][1]

    def new_block(self, name) -> Layout:
        lay = Layout(self, name)
        self.blocks[name] = lay
        return lay

    def delete_block(self, name):
        self.blocks.pop(name, None)
        self._blocks_with_attdefs.discard(name)

    def add_attribs(self, insert: Entity, values: dict):
        """ATTRIBs on a block reference from the block's ATTDEFs (ezdxf's add_auto_attribs)."""
        blk = self.blocks.get(insert.get(2))
        if blk is None:
            return []
        ins = (float(insert.get(10, 0)), float(insert.get(20, 0)))
        rot = float(insert.get(50, 0.0))
        sc = float(insert.get(41, 1.0))
        out = []
        for e in blk.entities:
            if e.dxftype != "ATTDEF":
                continue
            tag = attdef_tag(e)
            a = _attrib_from(e, str(values.get(tag, "")), ins, rot, sc, insert.layer)
            out.append(a)
        insert.attribs = out
        if out:
            i = next(k for k, (c, _v) in enumerate(insert.tags) if c == 100 and _v == "AcDbBlockReference")
            insert.tags.insert(i + 1, (66, "1"))
        return out

    def set_header(self, name, *vals):
        """A header variable: vals as (code, value) pairs."""
        self.header_set[name] = list(vals)

    # -- output
    def _entity_tags(self, ent: Entity, owner: str):
        h = self.handle()
        tags = [(0, ent.dxftype), (5, h), (330, owner)] + ent.tags + ent.xdata
        out = [tags]
        children = getattr(ent, "children", None)
        if children:
            for kind, child_tags in children:
                out.append([(0, kind), (5, self.handle()), (330, h)] + child_tags)
            out.append([(0, "SEQEND"), (5, self.handle()), (330, h), (100, "AcDbEntity"), (8, ent.layer or "0")])
            return out
        if ent.attribs:
            for a in ent.attribs:
                out.append([(0, "ATTRIB"), (5, self.handle()), (330, owner)] + a.tags)
            out.append([(0, "SEQEND"), (5, self.handle()), (330, h), (100, "AcDbEntity"), (8, ent.layer or "0")])
        return out

    def saveas(self, path):
        # block records and block definitions for the new blocks
        br_handle = self._tables["BLOCK_RECORD"][0]
        block_tags = []
        for name, lay in self.blocks.items():
            brh = self.handle()
            self._new_records["BLOCK_RECORD"].append(
                ("BLOCK_RECORD", [(100, "AcDbSymbolTableRecord"), (100, "AcDbBlockTableRecord"), (2, name),
                                  (340, "0"), (70, "0"), (280, "1"), (281, "0")], brh))
            flags = "2" if name in self._blocks_with_attdefs else "0"
            block_tags += [(0, "BLOCK"), (5, self.handle()), (330, brh), (100, "AcDbEntity"), (8, "0"),
                           (100, "AcDbBlockBegin"), (2, name), (70, flags), (10, "0.0"), (20, "0.0"),
                           (30, "0.0"), (3, name), (1, "")]
            for e in lay.entities:
                for part in self._entity_tags(e, brh):
                    block_tags += part
            block_tags += [(0, "ENDBLK"), (5, self.handle()), (330, brh), (100, "AcDbEntity"), (8, "0"),
                           (100, "AcDbBlockEnd")]
        ent_tags = []
        for e in self.modelspace.entities:
            for part in self._entity_tags(e, self._msp_handle):
                ent_tags += part
        # table records (handles last, so table counts and the seed include everything)
        rec_tags = {}
        for tbl, recs in self._new_records.items():
            th = self._tables[tbl][0]
            out = []
            for r in recs:
                kind, t = r[0], r[1]
                h = r[2] if len(r) > 2 else self.handle()
                out += [(0, kind), (5, h), (330, th)] + t
            rec_tags[tbl] = out
        seed = "%X" % self._next

        out, i, tags = [], 0, self._tags
        sec = tbl = None
        while i < len(tags):
            c, v = tags[i]
            if (c, v) == (0, "SECTION"):
                sec = tags[i + 1][1]
            if sec == "HEADER" and c == 9:
                if v == "$HANDSEED":
                    out += [(9, v), (5, seed)]
                    i += 2
                    continue
                if v in self.header_set:
                    out.append((9, v))
                    out += self.header_set.pop(v)
                    i += 1
                    while i < len(tags) and tags[i][0] not in (0, 9):
                        i += 1
                    continue
            if sec == "HEADER" and (c, v) == (0, "ENDSEC"):
                for name, vals in self.header_set.items():
                    out.append((9, name))
                    out += vals
                for k, val in self.custom:
                    out += [(9, "$CUSTOMPROPERTYTAG"), (1, k), (9, "$CUSTOMPROPERTY"), (1, val)]
            if sec == "TABLES" and (c, v) == (0, "TABLE"):
                tbl = tags[i + 1][1]
            if sec == "TABLES" and c == 70 and tbl in rec_tags and tags[i - 1] == (100, "AcDbSymbolTable"):
                out.append((70, str(int(v) + sum(1 for t in rec_tags[tbl] if t[0] == 0))))
                i += 1
                continue
            if sec == "TABLES" and (c, v) == (0, "ENDTAB") and tbl in rec_tags:
                out += rec_tags[tbl]
            if sec == "BLOCKS" and (c, v) == (0, "ENDSEC"):
                out += block_tags
            if sec == "ENTITIES" and (c, v) == (0, "ENDSEC"):
                out += ent_tags
            out.append((c, v))
            i += 1
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("".join(f"{c:>3}\n{v}\n" for c, v in out))
