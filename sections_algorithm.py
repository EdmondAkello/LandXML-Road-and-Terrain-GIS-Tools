"""Cross-section sheets (PDF), per-section images and an HTML section report."""

from __future__ import annotations

import html
import os

from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
)

from .landxml.catalog import match_name, read_catalog
from .landxml.corridor import guess_surfaces, read_corridor_sections, roadway_surface_refs
from .landxml.earthworks import parse_station_ranges
from .landxml.longsection import default_pair, find_profile, read_alignment_profiles
from .landxml.parser import load_document
from .landxml.reports import _cards, _document, _table, station_label
from .landxml.section_view import build_section_view
from .params import number_param
from .processing_widgets import name_param

PAGES = ["A3 landscape", "A4 landscape", "A1 landscape", "A3 portrait", "A4 portrait"]
PER_PAGE = [1, 2, 4, 6, 8, 9, 12]


def _resolve(candidates, value, label):
    try:
        return match_name(candidates, value, label)
    except ValueError as exc:
        raise QgsProcessingException(str(exc)) from exc


def select_sections(sections, station_range=None, interval=0.0, require_ground=None):
    """Filter sections by range, station multiple and presence of a ground line."""
    result = []
    for section in sections:
        if station_range and not station_range[0] - 1e-6 <= section.station <= station_range[1] + 1e-6:
            continue
        if interval > 0:
            remainder = section.station % interval
            if min(remainder, interval - remainder) > 1e-3:
                continue
        if require_ground and section.surface(require_ground) is None:
            continue
        result.append(section)
    return result


def section_report_html(views, unit, title, svgs, source_file, settings):
    rows = []
    for view in views:
        depth = (
            view["design_cl"] - view["ground_cl"]
            if view["design_cl"] is not None and view["ground_cl"] is not None
            else None
        )
        rows.append(
            (
                station_label(view["station"], unit),
                view["design_cl"],
                view["ground_cl"],
                depth,
                view["cut_area"],
                view["fill_area"],
                view["daylight"][0],
                view["daylight"][1],
            )
        )
    cut = sum(v["cut_area"] or 0 for v in views)
    fill = sum(v["fill_area"] or 0 for v in views)
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · {html.escape(settings)}</p>",
        _cards(
            [
                ("Sections", f"{len(views)}"),
                ("From", station_label(views[0]["station"], unit)),
                ("To", station_label(views[-1]["station"], unit)),
                ("Largest cut area", f"{max((v['cut_area'] or 0) for v in views):,.2f}"),
                ("Largest fill area", f"{max((v['fill_area'] or 0) for v in views):,.2f}"),
                ("Sum of areas (cut / fill)", f"{cut:,.1f} / {fill:,.1f}"),
            ]
        ),
        "<h2>Section table</h2><div class='scroll'>",
        _table(["Chainage", "FRL", "EG", "CL depth", "Cut area", "Fill area", "Left daylight", "Right daylight"], rows, 3),
        "</div>",
    ]
    if svgs:
        body.append(f"<h2>Sections ({len(svgs)} shown)</h2>")
        body.append("<style>.sheet{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:12px}"
                    ".sheet svg{border:1px solid #d8dde5;border-radius:6px} @media print{.sheet>div{break-inside:avoid}}</style>")
        body.append("<div class='sheet'>" + "".join(f"<div>{svg}</div>" for svg in svgs) + "</div>")
    return _document(title or "Cross-sections", "".join(body))


class CrossSectionSheetsAlgorithm(QgsProcessingAlgorithm):
    def createInstance(self):
        return CrossSectionSheetsAlgorithm()

    def name(self):
        return "landxml_cross_section_sheets"

    def displayName(self):
        return "Cross-Section Sheets and Report"

    def group(self):
        return "Earthworks and quantities"

    def groupId(self):
        return "earthworks_quantities"

    def flags(self):
        # Painting fonts into PDF/SVG is kept on the main thread.
        return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading

    def shortHelpString(self):
        return (
            "Plots LandXML corridor cross-sections (Civil 3D sample-line exports) as multi-page PDF sheets, "
            "with an optional HTML report (section table and drawings) and one PNG per section.\n\n"
            "Each section shows the existing ground and design lines, shaded cut and fill, pavement layers "
            "from the corridor shapes, the Datum, side-slope ratios (steeper than the stabilisation threshold "
            "in red), daylight offsets, centreline FRL/EG levels and the cut/fill areas. Every section is "
            "fitted to its own width at the chosen vertical exaggeration unless a fixed half-width is set.\n\n"
            "Blank surface names are detected from the file; names can be chosen from the lists or typed in "
            "part. Use the station range and interval to plot, for example, every 50 m between 10000 and 12000."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT", "LandXML file", behavior=QgsProcessingParameterFile.Behavior.File, fileFilter="LandXML (*.xml *.landxml)"
            )
        )
        self.addParameter(name_param("ALIGNMENT", "Alignment (blank = first with cross-sections)", "alignments"))
        self.addParameter(name_param("GROUND_SURFACE", "Existing-ground section surface (blank = detect)", "section_surfaces"))
        self.addParameter(name_param("DESIGN_SURFACE", "Design section surface (blank = detect)", "section_surfaces"))
        self.addParameter(name_param("DESIGN_PROFILE", "Design profile for levels (blank = first ProfAlign)", "design_profiles"))
        self.addParameter(
            QgsProcessingParameterString("RANGE", "Station range (blank = all), e.g. 10000-12000", defaultValue="", optional=True)
        )
        self.addParameter(number_param("INTERVAL", "Plot sections at multiples of (0 = every section)", 0, 0, 100000, decimals=2))
        self.addParameter(
            QgsProcessingParameterBoolean("ONLY_GROUND", "Only sections with an existing-ground line", defaultValue=True)
        )
        self.addParameter(QgsProcessingParameterBoolean("USE_DATUM", "Show Datum and measure areas to it", defaultValue=True))
        self.addParameter(number_param("EXAGGERATION", "Vertical exaggeration", 2, 0.1, 50, decimals=2))
        self.addParameter(number_param("HALF_WIDTH", "Fixed offset half-width (0 = fit each section)", 0, 0, 10000, decimals=2))
        self.addParameter(number_param("STEEP_RATIO", "Mark slopes steeper than 1:n in red", 1.5, 0.01, 100, decimals=2))
        self.addParameter(QgsProcessingParameterEnum("PAGE", "Sheet size", options=PAGES, defaultValue=0))
        self.addParameter(
            QgsProcessingParameterEnum(
                "PER_PAGE", "Sections per sheet", options=[str(n) for n in PER_PAGE], defaultValue=PER_PAGE.index(6)
            )
        )
        self.addParameter(QgsProcessingParameterString("TITLE", "Sheet title", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Cross-section sheets (PDF)", fileFilter="PDF (*.pdf)"))
        self.addParameter(
            QgsProcessingParameterFileDestination("REPORT", "HTML section report", fileFilter="HTML (*.html)", optional=True)
        )
        self.addParameter(number_param("REPORT_LIMIT", "Drawings in the HTML report (table lists all)", 200, 0, 100000))
        self.addParameter(
            QgsProcessingParameterFolderDestination("PNG_FOLDER", "Folder for one PNG per section", optional=True)
        )

    def processAlgorithm(self, p, c, fb):
        from .gui.section_chart import SectionStyle, material_colors, section_image, section_svg, write_section_sheets

        path = self.parameterAsFile(p, "INPUT", c)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        catalog = read_catalog(path)
        with_sections = [name for name in catalog.alignments if catalog.cross_sections.get(name)]
        if not with_sections:
            raise QgsProcessingException("This LandXML file has no cross-sections (CrossSect) to plot.")
        requested = self.parameterAsString(p, "ALIGNMENT", c).strip()
        alignment_name = _resolve(catalog.alignments, requested, "alignment") if requested else with_sections[0]
        doc = load_document(path)
        sections, warnings, surface_names, _shapes = read_corridor_sections(doc.root, alignment_name)
        for warning in warnings[:10]:
            fb.pushWarning(warning)
        if not sections:
            raise QgsProcessingException(f"Alignment '{alignment_name}' has no cross-sections.")
        element = next(a for a in doc.alignments() if (a.attrib.get("name") or "") == alignment_name)
        profiles = read_alignment_profiles(element, 2.0)
        design_profile, _ground = default_pair(profiles)
        profile_name = self.parameterAsString(p, "DESIGN_PROFILE", c).strip()
        if profile_name:
            names = [x.name for x in profiles if x.kind == "design"]
            design_profile = find_profile(profiles, _resolve(names, profile_name, "design profile"), "design")
        ground_guess, design_guess = guess_surfaces(
            sections, [x for x in profiles if x.kind == "surface"], roadway_surface_refs(doc.root)
        )
        ground_name = self.parameterAsString(p, "GROUND_SURFACE", c).strip()
        design_name = self.parameterAsString(p, "DESIGN_SURFACE", c).strip()
        ground_name = _resolve(surface_names, ground_name, "ground section surface") if ground_name else ground_guess
        design_name = _resolve(surface_names, design_name, "design section surface") if design_name else design_guess
        if not design_name and not ground_name:
            raise QgsProcessingException(
                f"Could not identify the section surfaces; choose them from the list ({', '.join(surface_names)})."
            )
        fb.pushInfo(f"{alignment_name}: ground '{ground_name or '–'}', design '{design_name or '–'}'.")
        try:
            ranges = parse_station_ranges(self.parameterAsString(p, "RANGE", c))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        station_range = (ranges[0][0], ranges[-1][1]) if ranges else None
        require = ground_name if self.parameterAsBoolean(p, "ONLY_GROUND", c) and ground_name else None
        chosen = select_sections(sections, station_range, self.parameterAsDouble(p, "INTERVAL", c), require)
        if not chosen:
            raise QgsProcessingException("No sections match the station range, interval and ground filter.")
        if len(chosen) > 3000:
            fb.pushWarning(f"Plotting {len(chosen)} sections; consider a station range or interval.")
        use_datum = self.parameterAsBoolean(p, "USE_DATUM", c)
        steep = self.parameterAsDouble(p, "STEEP_RATIO", c)
        views = [
            build_section_view(
                section,
                ground_name,
                design_name,
                design_profile.elevation_at(section.station) if design_profile else None,
                use_datum,
                steep_ratio=steep,
            )
            for section in chosen
        ]
        exaggeration = self.parameterAsDouble(p, "EXAGGERATION", c)
        half = self.parameterAsDouble(p, "HALF_WIDTH", c)
        x_range = (-half, half) if half > 0 else None
        title = self.parameterAsString(p, "TITLE", c).strip()
        page = PAGES[self.parameterAsEnum(p, "PAGE", c)]
        per_page = PER_PAGE[self.parameterAsEnum(p, "PER_PAGE", c)]
        output = self.parameterAsFileOutput(p, "OUTPUT", c)
        pages = write_section_sheets(
            output,
            views,
            page,
            per_page,
            exaggeration,
            doc.horizontal_unit,
            title or "Cross-sections",
            alignment_name,
            x_range,
            progress=lambda value: fb.setProgress(int(value * 0.7)),
            cancel=fb.isCanceled,
        )
        fb.pushInfo(f"Wrote {len(views)} sections on {pages} {page} sheet(s).")
        style = SectionStyle(x_range, exaggeration, doc.horizontal_unit, False, material_colors(views))
        folder = self.parameterAsString(p, "PNG_FOLDER", c)
        if folder and p.get("PNG_FOLDER"):
            os.makedirs(folder, exist_ok=True)
            for index, view in enumerate(views):
                if fb.isCanceled():
                    break
                name = station_label(view["station"], doc.horizontal_unit).replace("+", "_")
                section_image(view, style, 1400, 620, 1.5).save(os.path.join(folder, f"CH_{name}.png"), "PNG")
                fb.setProgress(70 + int(15 * (index + 1) / len(views)))
            fb.pushInfo(f"Saved {len(views)} PNG images to {folder}.")
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            limit = int(self.parameterAsDouble(p, "REPORT_LIMIT", c))
            step = max(1, -(-len(views) // limit)) if limit else 0
            svgs = [section_svg(view, style) for view in views[::step]] if step else []
            settings = f"Alignment {alignment_name} · ground {ground_name or '–'} · design {design_name or '–'} · VE {exaggeration:g}"
            with open(report, "w", encoding="utf-8") as stream:
                stream.write(section_report_html(views, doc.horizontal_unit, title, svgs, os.path.basename(path), settings))
            if step > 1:
                fb.pushInfo(f"The HTML report draws every {step}th section ({len(svgs)} drawings); the table lists all.")
        return {
            "OUTPUT": output,
            "REPORT": report or "",
            "PNG_FOLDER": folder or "",
            "SECTION_COUNT": len(views),
            "PAGE_COUNT": pages,
        }
