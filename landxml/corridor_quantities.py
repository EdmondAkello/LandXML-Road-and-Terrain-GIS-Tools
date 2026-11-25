"""Quantities of a built corridor, the bill of quantities export and variant comparison (pure Python + NumPy).

Volumes are average end areas between consecutive sections, as Corridor Section Quantities computes them on the
written CrossSects; earthworks are the cut and fill between the ground and the datum (the bottom of the pavement
under paved elements, the finished surface elsewhere). Topsoil strip and unsuitable material are depth times the
footprint (daylight to daylight; unsuitable under fill only, or under the whole footprint). Linear items (kerbs,
channels, drains, barriers) are measured along their feature lines.

The bill of quantities uses the user's item list and rates; no rate is ever assumed. The Excel file is written
without any third-party library.
"""

from __future__ import annotations

import csv
import html
import json
import math
import zipfile

import numpy as np

from .earthworks import section_areas


def _footprint(section):
    top = section["top"]
    return top[0].d, top[-1].d


def quantities(result):
    """{"rows": per station, "items": {key: {"quantity", "unit", "description"}}, "totals": {...}}."""
    project = result.context["project"]
    ew = project.get("earthworks") or {}
    topsoil = float(ew.get("topsoil") or 0.0)
    unsuitable = float(ew.get("unsuitable") or 0.0)
    under = ew.get("unsuitable_under", "fill")
    unit_len = "m" if result.context["unit"] == "meter" else "ft"
    rows = []
    for section in result.sections:
        row = {"station": section["station"], "shapes": {}, "widths": {}}
        g = section.get("ground")
        if g is not None:
            offs, zs = g
            ok = np.isfinite(zs)
            ground = list(zip(offs[ok].tolist(), zs[ok].tolist()))
            areas = section_areas(section["datum"], ground) if len(ground) >= 2 else None
            if areas is not None:
                cut, fill, _lo, _hi = areas
                row["cut"], row["fill"] = cut, fill
            lo, hi = _footprint(section)
            row["footprint"] = hi - lo
            fill_width = 0.0
            design = section["top"]
            for a, b in zip(design, design[1:]):
                mid = (a.d + b.d) / 2
                gm = float(np.interp(mid, offs[ok], zs[ok])) if ok.any() else float("nan")
                if math.isfinite(gm) and (a.z + b.z) / 2 > gm + 1e-6:
                    fill_width += b.d - a.d
            row["fill_width"] = fill_width
        for shape in section["shapes"]:
            key = _shape_key(shape)
            row["shapes"][key] = row["shapes"].get(key, 0.0) + shape["area"]
            if shape.get("top_width"):
                row["widths"][key] = row["widths"].get(key, 0.0) + shape["top_width"]
        rows.append(row)
    items = {}

    def add(key, value, unit, description):
        entry = items.setdefault(key, {"quantity": 0.0, "unit": unit, "description": description})
        entry["quantity"] += value

    vol = f"{unit_len}3"
    area = f"{unit_len}2"
    for a, b in zip(rows, rows[1:]):
        ds = b["station"] - a["station"]
        if ds <= 0:
            continue
        for key in ("cut", "fill"):
            if key in a and key in b:
                add(f"earthworks/{key}", ds * (a[key] + b[key]) / 2, vol, f"Earthworks {key} (to the datum)")
        if "footprint" in a and "footprint" in b:
            fp = ds * (a["footprint"] + b["footprint"]) / 2
            add("earthworks/footprint", fp, area, "Corridor footprint (daylight to daylight)")
            if topsoil > 0:
                add("earthworks/topsoil", fp * topsoil, vol, f"Topsoil strip {topsoil:g} deep over the footprint")
            if unsuitable > 0:
                width = (a["fill_width"] + b["fill_width"]) / 2 if under == "fill" else (a["footprint"] + b["footprint"]) / 2
                add("earthworks/unsuitable", ds * width * unsuitable, vol,
                    f"Unsuitable material {unsuitable:g} deep under {'fill' if under == 'fill' else 'the footprint'}")
        for key in set(a["shapes"]) | set(b["shapes"]):
            v = ds * (a["shapes"].get(key, 0.0) + b["shapes"].get(key, 0.0)) / 2
            add(f"shape/{key}", v, vol, key.replace("|", " - "))
            if key in a["widths"] or key in b["widths"]:
                w = ds * (a["widths"].get(key, 0.0) + b["widths"].get(key, 0.0)) / 2
                add(f"area/{key}", w, area, key.replace("|", " - ") + " (top area)")
    # linear items along their feature strings
    from .corridor_build import feature_strings
    frame = result.context["frame"]
    for (side, element, code, n), pts in feature_strings(result).items():
        name = {"KERB_T": "kerb", "BARRIER": "barrier", "DITCH_I": "drain", "DITCH_IF": "drain", "FL": "channel",
                "WALL_T": "retaining wall"}.get(code)
        if not name:
            continue
        st = np.array([p[0] for p in pts])
        off = np.array([p[1] for p in pts])
        xy, _t, normal = frame.at(st)
        plan = xy + normal * off[:, None]
        steps = np.hypot(*np.diff(plan, axis=0).T)
        jumps = np.diff(st) > 0
        length = float(steps[jumps].sum())
        add(f"length/{name}/{element}", length, unit_len, f"{element} {name} length ({'left' if side == 'L' else 'right'})"
            if side in ("L", "R") else f"{element} {name} length")
    for f in result.context.get("features", []):
        if f["insert"] == "gap" and f.get("culvert"):
            add(f"feature/culvert/{f['label']}", 1.0, "nr", f"Culvert under {f['label']} at {f['at']:.1f} ({f['side']})")
    from .corridor_build import escape_ramps
    for ramp in escape_ramps(result):
        add(f"feature/arrester_bed/{ramp['feature']['label']}", ramp["volume"], vol,
            f"{ramp['feature']['label']}: arrester bed ({ramp['material']}, {ramp['bed']:.0f} long)")
    totals = {"cut": items.get("earthworks/cut", {}).get("quantity", 0.0),
              "fill": items.get("earthworks/fill", {}).get("quantity", 0.0)}
    totals["net"] = totals["fill"] - totals["cut"] * float(ew.get("bulking") or 1.0)
    return {"rows": rows, "items": items, "totals": totals, "unit": unit_len}


def _shape_key(shape):
    if shape.get("buildup"):
        return f"{shape['buildup']}|{shape['name']}"
    return f"{shape.get('type', '')}|{shape['name']}"


def course_totals(q):
    """{buildup|course: (volume, top area)} summed over elements and sides."""
    out = {}
    for key, item in q["items"].items():
        if key.startswith("shape/"):
            k = key[6:]
            out.setdefault(k, [0.0, 0.0])[0] += item["quantity"]
        elif key.startswith("area/"):
            k = key[5:]
            out.setdefault(k, [0.0, 0.0])[1] += item["quantity"]
    return out


# ----------------------------------------------------------------------------------------------------------------
# Bill of quantities
# ----------------------------------------------------------------------------------------------------------------
def default_items(q):
    """A starting item list from the computed quantities: every key once, no rates (the user's to enter)."""
    out = []
    n = 0
    for key in sorted(q["items"]):
        if key == "earthworks/footprint":
            continue
        n += 1
        item = q["items"][key]
        out.append({"item": f"{n}", "description": item["description"], "unit": item["unit"], "rate": None,
                    "quantity": key})
    return out


def read_items(path):
    """An item list from JSON ([{item, description, unit, rate, quantity}]) or CSV with those columns."""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        return data["items"] if isinstance(data, dict) else data
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        rate = (row.get("rate") or "").strip()
        row["rate"] = float(rate) if rate else None
    return rows


def boq_rows(q, items):
    """[(item, description, unit, quantity, rate, amount or None)] with quantities looked up by key (several keys
    may be joined with '+'; a key ending in '*' sums every key starting with it)."""
    out = []
    for it in items:
        total = 0.0
        found = False
        for key in str(it.get("quantity") or "").split("+"):
            key = key.strip()
            if not key:
                continue
            if key.endswith("*"):
                for k, v in q["items"].items():
                    if k.startswith(key[:-1]):
                        total += v["quantity"]
                        found = True
            elif key in q["items"]:
                total += q["items"][key]["quantity"]
                found = True
        rate = it.get("rate")
        rate = float(rate) if rate not in (None, "") else None
        out.append((str(it.get("item", "")), it.get("description", ""), it.get("unit", ""),
                    total if found else None, rate, total * rate if (found and rate is not None) else None))
    return out


def escape(text):
    """Text for an XML element or attribute value (written without an XML library)."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value, style=0):
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, str) and value.startswith("="):
        return f'<c r="{ref}" s="{style}"><f>{escape(value[1:])}</f></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value:.6g}</v></c>' if math.isfinite(value) else f'<c r="{ref}"/>'
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t>{escape(str(value))}</t></is></c>'


def _sheet(rows, widths=None):
    cols = ""
    if widths:
        cols = "<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                                  for i, w in enumerate(widths)) + "</cols>"
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            style = 1 if r == 1 else (2 if isinstance(value, (int, float)) or (isinstance(value, str) and value.startswith("=")) else 0)
            cells.append(_cell(f"{_col(c)}{r}", value, style))
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{cols}<sheetData>{"".join(body)}</sheetData></worksheet>')


def write_xlsx(path, sheets):
    """Write ``sheets`` [(name, rows, widths)] as an .xlsx workbook (formulas allowed as '=...')."""
    names = [s[0][:31] for s in sheets]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets))) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
                   + "</sheets><calcPr fullCalcOnLoad=\"1\"/></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="3"><xf/><xf fontId="1" applyFont="1"/><xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>')
        for i, (_name, rows, widths) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(rows, widths))
    return path


def boq_sheets(q, items, title="Bill of quantities"):
    rows = [["Item", "Description", "Unit", "Quantity", "Rate", "Amount"]]
    for k, (item, desc, unit, qty, rate, _amount) in enumerate(boq_rows(q, items), 2):
        rows.append([item, desc, unit, round(qty, 3) if qty is not None else None, rate, f"=IF(E{k}=\"\",\"\",D{k}*E{k})"])
    n = len(rows)
    rows.append(["", "Total", "", None, None, f"=SUM(F2:F{n})"])
    detail = [["Key", "Description", "Unit", "Quantity"]]
    for key in sorted(q["items"]):
        item = q["items"][key]
        detail.append([key, item["description"], item["unit"], round(item["quantity"], 3)])
    sections = [["Station", "Cut area", "Fill area", "Footprint"]]
    for r in q["rows"]:
        sections.append([round(r["station"], 3), r.get("cut"), r.get("fill"), r.get("footprint")])
    return [(title[:31], rows, [8, 60, 8, 14, 12, 16]), ("Quantities", detail, [50, 60, 8, 14]),
            ("Sections", sections, [12, 12, 12, 12])]


def write_boq(path, q, items=None, comparison=None):
    items = items if items is not None else default_items(q)
    sheets = boq_sheets(q, items)
    if comparison is not None:
        sheets.append(("Comparison", comparison_rows(comparison), [50, 10, 16, 16, 16, 10]))
    return write_xlsx(path, sheets)


# ----------------------------------------------------------------------------------------------------------------
# Variant comparison
# ----------------------------------------------------------------------------------------------------------------
def compare(q_a, q_b, labels=("A", "B"), items=None):
    keys = sorted(set(q_a["items"]) | set(q_b["items"]))
    rows = []
    for key in keys:
        a = q_a["items"].get(key, {}).get("quantity", 0.0)
        b = q_b["items"].get(key, {}).get("quantity", 0.0)
        unit = (q_a["items"].get(key) or q_b["items"].get(key))["unit"]
        rows.append({"key": key, "unit": unit, "a": a, "b": b, "difference": b - a,
                     "percent": (b - a) / a * 100 if abs(a) > 1e-9 else None})
    out = {"labels": labels, "rows": rows}
    if items:
        ca = sum(r[5] or 0 for r in boq_rows(q_a, items))
        cb = sum(r[5] or 0 for r in boq_rows(q_b, items))
        out["cost"] = (ca, cb, cb - ca)
    return out


def comparison_rows(comparison):
    a, b = comparison["labels"]
    rows = [["Quantity", "Unit", a, b, "Difference", "%"]]
    for r in comparison["rows"]:
        rows.append([r["key"], r["unit"], round(r["a"], 3), round(r["b"], 3), round(r["difference"], 3),
                     round(r["percent"], 1) if r["percent"] is not None else None])
    if comparison.get("cost"):
        ca, cb, d = comparison["cost"]
        rows.append(["Cost (user rates)", "", ca, cb, d, round(d / ca * 100, 1) if ca else None])
    return rows


# ----------------------------------------------------------------------------------------------------------------
# HTML summary
# ----------------------------------------------------------------------------------------------------------------
def report_html(result, q, title="Corridor build", comparison=None):
    ctx = result.context
    project = ctx["project"]
    e = html.escape
    parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{e(title)}</title><style>"
             "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}table{border-collapse:collapse;margin:8px 0}"
             "td,th{border:1px solid #ccc;padding:3px 8px;font-size:13px}th{background:#eef2f5;text-align:left}"
             "td.n{text-align:right}.dep{color:#9a5b00}.warn{color:#a00}</style></head><body>"]
    parts.append(f"<h1>{e(title)}</h1><p>Alignment <b>{e(project['alignment'])}</b>, profile <b>{e(ctx['profile'].name)}</b>; "
                 f"standard <b>{e(ctx['standard'].name)}</b> (advisory); design speed {project.get('design_speed'):g}; "
                 f"{len(result.sections)} sections from {result.stations[0]:.2f} to {result.stations[-1]:.2f}.</p>")
    parts.append("<h2>Regions</h2><table><tr><th>From</th><th>To</th><th>Assembly</th><th>Transition</th></tr>")
    for r in ctx["regions"]:
        parts.append(f"<tr><td class=n>{r['start']:.2f}</td><td class=n>{r['end']:.2f}</td><td>{e(r['assembly'])}</td>"
                     f"<td class=n>{float(r.get('transition') or 0):g}</td></tr>")
    parts.append("</table>")
    sch = ctx["schedules"]
    if sch.records:
        parts.append("<h2>Superelevation</h2><table><tr><th>Curve</th><th>Rate %</th><th>Runout</th><th>Runoff</th>"
                     "<th>Full</th><th>Source</th></tr>")
        for r in sch.records:
            parts.append(f"<tr><td class=n>{r.get('curve_start', r['full_start']):.1f}</td><td class=n>{r['rate']:.2f}</td>"
                         f"<td class=n>{r['begin_runout']:.1f}</td><td class=n>{r['begin_runoff']:.1f}</td>"
                         f"<td class=n>{r['full_start']:.1f}-{r['full_end']:.1f}</td><td>{e(r.get('source', ''))}</td></tr>")
        parts.append("</table>")
    if sch.widen:
        parts.append("<h2>Curve widening</h2><table><tr><th>From</th><th>Full</th><th>To</th><th>Width</th><th>Source</th></tr>")
        for w in sch.widen:
            a, b, c, d = w["stations"]
            parts.append(f"<tr><td class=n>{a:.1f}</td><td class=n>{b:.1f}-{c:.1f}</td><td class=n>{d:.1f}</td>"
                         f"<td class=n>{w['width']:.2f}</td><td>{e(w['ref'])}</td></tr>")
        parts.append("</table>")
    feats = ctx.get("features", [])
    if feats:
        parts.append("<h2>Corridor features</h2><table><tr><th>Feature</th><th>Side</th><th>Chainages</th><th>Source</th></tr>")
        for f in feats:
            a, b, c, d = f["spans"]
            parts.append(f"<tr><td>{e(f['label'])}</td><td>{e(f['side'])}</td><td class=n>{a:.1f} / {b:.1f} - {c:.1f} / {d:.1f}</td>"
                         f"<td>{e(f['ref'])}</td></tr>")
        parts.append("</table>")
    parts.append("<h2>Quantities</h2><table><tr><th>Quantity</th><th>Unit</th><th>Value</th></tr>")
    for key in sorted(q["items"]):
        item = q["items"][key]
        parts.append(f"<tr><td>{e(item['description'])}</td><td>{e(item['unit'])}</td><td class=n>{item['quantity']:,.2f}</td></tr>")
    parts.append("</table>")
    if result.checks:
        parts.append("<h2>Departures from the standard (advisory)</h2><ul>")
        for c in result.checks:
            parts.append(f"<li class=dep>{e(c.get('assembly', ''))}, {e(c.get('side', ''))}: {e(c['message'])}</li>")
        parts.append("</ul>")
    if result.warnings:
        parts.append("<h2>Warnings</h2><ul>" + "".join(f"<li class=warn>{e(w)}</li>" for w in result.warnings) + "</ul>")
    if comparison is not None:
        a, b = comparison["labels"]
        parts.append(f"<h2>Variant comparison: {e(a)} vs {e(b)}</h2><table><tr><th>Quantity</th><th>Unit</th><th>{e(a)}</th>"
                     f"<th>{e(b)}</th><th>Difference</th><th>%</th></tr>")
        for r in comparison["rows"]:
            pct = f"{r['percent']:+.1f}" if r["percent"] is not None else ""
            parts.append(f"<tr><td>{e(r['key'])}</td><td>{e(r['unit'])}</td><td class=n>{r['a']:,.2f}</td>"
                         f"<td class=n>{r['b']:,.2f}</td><td class=n>{r['difference']:+,.2f}</td><td class=n>{pct}</td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


__all__ = ["quantities", "course_totals", "default_items", "read_items", "boq_rows", "write_boq", "write_xlsx",
           "compare", "comparison_rows", "report_html"]

