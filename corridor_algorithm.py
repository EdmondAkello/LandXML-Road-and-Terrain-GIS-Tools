"""Processing tools of the Corridor Section Editor: create a section project and build the corridor from it."""

from __future__ import annotations

import os

from qgis.core import (
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from .landxml import corridor_build as cb
from .landxml import corridor_quantities as cq
from .landxml.section_standards import list_section_standards, load_section_standard
from .params import number_param
from .processing_widgets import name_param
from .project_settings import ProjectSettingsAlgorithm

GROUP = "9. Corridor design"
GROUP_ID = "corridor_design"
AXIS = ["Choose…", "Northing first (N E Z, as Civil 3D writes)", "Easting first (E N Z)"]
SUPER = ["From the LandXML", "Computed from the standard", "None (normal crossfall)"]
SUPER_KEYS = ["landxml", "standard", "none"]


class CreateSectionProjectAlgorithm(ProjectSettingsAlgorithm):
    """A section project (JSON) on a LandXML alignment from a standard's template; edit it in the Section Editor."""

    def createInstance(self):
        return CreateSectionProjectAlgorithm()

    def name(self):
        return "landxml_section_project"

    def displayName(self):
        return "Create Corridor Section Project (from a Standard Template)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return ("Starts a corridor section project on an alignment and design profile of a LandXML file: the assembly comes "
                "from a template of the chosen section standard (Kenya RDM 1.3, AASHTO 2018, Generic or your own file), "
                "with its build-ups, superelevation and widening settings. Open the project in the Corridor Section Editor "
                "(toolbar) to edit every element on the real ground, or build it straight away with 'Build Corridor from "
                "Section Project'. Standards give defaults and advisory checks only: any value can be used.\n\n"
                "The axis order of the file is required: Civil 3D writes northing first.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("INPUT", "LandXML (alignment with a design profile)",
                                                     fileFilter="LandXML (*.xml *.landxml)"))
        self.addParameter(name_param("ALIGNMENT", "Alignment (empty = the only one)", "alignments"))
        self.addParameter(name_param("PROFILE", "Design profile (empty = the only one)", "design_profiles", optional=True))
        self.addParameter(QgsProcessingParameterEnum("AXIS", "Axis order of the file", options=AXIS, defaultValue=0))
        standards = list_section_standards()
        self._standards = [s[0] for s in standards]
        self.addParameter(QgsProcessingParameterEnum("STANDARD", "Section standard", options=[s[1] for s in standards],
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterFile("STANDARD_FILE", "Or a standard file of your own (JSON)", optional=True,
                                                     fileFilter="JSON (*.json)"))
        self.addParameter(QgsProcessingParameterString("TEMPLATE", "Template name (empty = the standard's first; listed in "
                                                       "the guide and the Section Editor)", defaultValue="", optional=True))
        self.addParameter(number_param("SPEED", "Design speed", 80.0, 5.0, 200.0, decimals=0))
        self.addParameter(QgsProcessingParameterEnum("SUPERELEVATION", "Superelevation", options=SUPER, defaultValue=0))
        self.addParameter(name_param("GROUND_SURFACE", "Existing ground: surface in the same LandXML (empty = none or DEM)",
                                     "surfaces", optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", "Or existing ground: DEM in the file's CRS", optional=True))
        self.addParameter(number_param("INTERVAL", "Section interval", 20.0, 0.5, 1000.0, decimals=2))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Section project", fileFilter="JSON (*.json)"))

    def processAlgorithm(self, p, c, fb):
        path = self.parameterAsFile(p, "INPUT", c)
        axis = self.parameterAsEnum(p, "AXIS", c)
        if axis == 0:
            raise QgsProcessingException("Choose the axis order of the file (it is never guessed).")
        own = self.parameterAsFile(p, "STANDARD_FILE", c)
        standard = own or list_section_standards()[self.parameterAsEnum(p, "STANDARD", c)][0]
        std = load_section_standard(standard)
        template = self.parameterAsString(p, "TEMPLATE", c).strip() or None
        if template and template not in std.templates():
            raise QgsProcessingException(f"'{template}' is not a template of {std.name}: {', '.join(std.templates())}.")
        dem = self.parameterAsRasterLayer(p, "DEM", c)
        surface = self.parameterAsString(p, "GROUND_SURFACE", c).strip()
        if dem is not None and surface:
            raise QgsProcessingException("Choose the ground as a surface or as a DEM, not both.")
        ground = ({"type": "dem", "path": dem.source()} if dem is not None
                  else {"type": "tin", "path": path, "surface": surface} if surface else {"type": "none"})
        project = cb.new_project(path, self.parameterAsString(p, "ALIGNMENT", c).strip(),
                                 self.parameterAsString(p, "PROFILE", c).strip(), standard, template, axis == 1,
                                 self.parameterAsDouble(p, "SPEED", c), ground)
        if own:
            project["standard"] = own
        project["superelevation"]["source"] = SUPER_KEYS[self.parameterAsEnum(p, "SUPERELEVATION", c)]
        project["stations"]["interval"] = self.parameterAsDouble(p, "INTERVAL", c)
        try:
            context = cb.prepare(project)
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error
        for w in context["warnings"]:
            fb.pushWarning(w)
        out = self.parameterAsFileOutput(p, "OUTPUT", c)
        cb.save_project(project, out)
        fb.pushInfo(f"Project for '{project['alignment']}' with {', '.join(project['assemblies'])} ({std.name}).")
        return {"OUTPUT": out}


class BuildCorridorAlgorithm(ProjectSettingsAlgorithm):
    def createInstance(self):
        return BuildCorridorAlgorithm()

    def name(self):
        return "landxml_build_corridor"

    def displayName(self):
        return "Build Corridor from Section Project (LandXML, Quantities, Bill of Quantities)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return ("Builds the corridor of a section project (made in the Corridor Section Editor or with 'Create Corridor "
                "Section Project') and writes a <b>new</b> LandXML: the source alignment copied unchanged with its "
                "CrossSects (ground, finished surface, a closed shape for every pavement course, kerb and lining, the "
                "datum), the finished and datum TIN surfaces, and feature lines by code in the Civil 3D form (lines, "
                "three-point curves, elevation points). The source file is never edited.\n\n"
                "Also: an HTML report (regions, superelevation, widening, quantities, advisory departures from the "
                "standard) and a bill of quantities in Excel from your item list and rates (none are assumed; without "
                "a list, every computed quantity is listed for you to price). A second project builds a variant and "
                "compares the quantities and costs side by side.\n\n"
                "Item list: CSV or JSON with columns item, description, unit, rate, quantity - the quantity is a key "
                "from the Quantities sheet (several joined with +; a key ending in * sums every key starting with it).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("PROJECT", "Section project", fileFilter="Section project (*.json)"))
        self.addParameter(QgsProcessingParameterFile("VARIANT", "Variant project to compare (optional)", optional=True,
                                                     fileFilter="Section project (*.json)"))
        self.addParameter(QgsProcessingParameterFile("ITEMS", "Bill of quantities item list with rates (CSV or JSON)",
                                                     optional=True, fileFilter="Items (*.csv *.json)"))
        self.addParameter(QgsProcessingParameterBoolean("FEATURE_LINES", "Write feature lines", defaultValue=True))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Corridor LandXML", fileFilter="LandXML (*.xml)"))
        self.addParameter(QgsProcessingParameterFileDestination("REPORT", "Report", fileFilter="HTML (*.html)", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination("BOQ", "Bill of quantities", fileFilter="Excel (*.xlsx)",
                                                                optional=True))
        for key, label in (("CUT", "Cut volume"), ("FILL", "Fill volume"), ("SECTIONS", "Sections built")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def _build(self, path, fb, feature_lines=True):
        try:
            project = cb.load_project(path)
            project.setdefault("outputs", {})["feature_lines"] = feature_lines
            result = cb.build_corridor(project, progress=lambda v, m: fb.setProgress(v * 0.8))
        except (OSError, ValueError) as error:
            raise QgsProcessingException(f"{os.path.basename(path)}: {error}") from error
        for w in result.warnings:
            fb.pushWarning(w)
        for check in result.checks:
            fb.pushInfo(f"Advisory: {check.get('assembly', '')} {check['side']}: {check['message']}")
        return project, result

    def processAlgorithm(self, p, c, fb):
        project_path = self.parameterAsFile(p, "PROJECT", c)
        project, result = self._build(project_path, fb, self.parameterAsBoolean(p, "FEATURE_LINES", c))
        out = self.parameterAsFileOutput(p, "OUTPUT", c)
        if os.path.abspath(out) == os.path.abspath(project["landxml"]):
            raise QgsProcessingException("The source LandXML is never overwritten; choose a new output file.")
        cb.write_corridor_landxml(result, out)
        q = cq.quantities(result)
        items_path = self.parameterAsFile(p, "ITEMS", c)
        items = cq.read_items(items_path) if items_path else None
        comparison = None
        variant = self.parameterAsFile(p, "VARIANT", c)
        if variant:
            _vp, vresult = self._build(variant, fb)
            comparison = cq.compare(q, cq.quantities(vresult),
                                    (os.path.splitext(os.path.basename(project_path))[0],
                                     os.path.splitext(os.path.basename(variant))[0]), items)
        output = {"OUTPUT": out, "CUT": q["totals"]["cut"], "FILL": q["totals"]["fill"], "SECTIONS": len(result.sections)}
        report = self.parameterAsFileOutput(p, "REPORT", c)
        if report:
            with open(report, "w", encoding="utf-8") as stream:
                stream.write(cq.report_html(result, q, title=f"{project['alignment']} corridor", comparison=comparison))
            output["REPORT"] = report
        boq = self.parameterAsFileOutput(p, "BOQ", c)
        if boq:
            cq.write_boq(boq, q, items, comparison)
            output["BOQ"] = boq
        fb.pushInfo(f"{len(result.sections)} sections; cut {q['totals']['cut']:,.0f}, fill {q['totals']['fill']:,.0f}; "
                    f"written to {out}.")
        return output


class ExportRoadsAlgorithm(ProjectSettingsAlgorithm):
    """Lines (with optional levels) to LandXML alignments and profiles, through the writer's PI fitting."""

    def createInstance(self):
        return ExportRoadsAlgorithm()

    def name(self):
        return "landxml_export_roads"

    def displayName(self):
        return "Export Roads to LandXML (Lines to Alignments and Profiles)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return ("Writes each line as a LandXML alignment for Civil 3D, the Profile Viewer, the Section Editor and the 3D "
                "scene. Every vertex is an intersection point (PI); interior PIs get a circular curve of the "
                "<i>radius</i> field (or the default) and clothoid spirals of the <i>spiral_in</i> / <i>spiral_out</i> "
                "fields. A radius that does not fit between its neighbours is reduced until it does, and reported.\n\n"
                "Levels: when the lines have Z, a design profile is written with a PVI at each vertex (vertical curves of "
                "the <i>vc_length</i> field or the default); with a DEM, a ground profile sampled along the alignment. "
                "The name comes from the <i>name</i> field. Coordinates are written in the layer's CRS (EPSG declared) "
                "in the axis order you choose.")

    def initAlgorithm(self, config=None):
        from qgis.core import QgsProcessing, QgsProcessingParameterFeatureSource

        self.addParameter(QgsProcessingParameterFeatureSource("INPUT", "Road lines", [QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(number_param("RADIUS", "Default curve radius", 300.0, 1.0, 1e6, decimals=2))
        self.addParameter(number_param("VC_LENGTH", "Default vertical curve length (Z lines)", 0.0, 0.0, 1e5, decimals=2))
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", "Ground DEM for a ground profile (same CRS)", optional=True))
        self.addParameter(number_param("STEP", "Ground profile spacing", 10.0, 0.5, 1000.0, decimals=2))
        self.addParameter(QgsProcessingParameterEnum("AXIS", "Axis order to write", options=AXIS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "LandXML", fileFilter="LandXML (*.xml)"))
        self.addOutput(QgsProcessingOutputNumber("COUNT", "Alignments written"))

    def processAlgorithm(self, p, c, fb):
        import math

        from qgis.core import QgsPointXY, QgsWkbTypes

        from .landxml.plan_design import PlanDesign
        from .landxml.writer import GroundProfile, VerticalProfile, write_landxml

        axis = self.parameterAsEnum(p, "AXIS", c)
        if axis == 0:
            raise QgsProcessingException("Choose the axis order to write (Civil 3D writes northing first).")
        source = self.parameterAsSource(p, "INPUT", c)
        crs = source.sourceCrs()
        if crs.isGeographic():
            raise QgsProcessingException("The lines must be in a projected CRS (metres or feet).")
        from qgis.core import QgsUnitTypes

        unit = "foot" if "Feet" in QgsUnitTypes.toString(crs.mapUnits()).title() or "Foot" in QgsUnitTypes.toString(
            crs.mapUnits()).title() else "meter"
        dem = self.parameterAsRasterLayer(p, "DEM", c)
        step = self.parameterAsDouble(p, "STEP", c)
        default_r = self.parameterAsDouble(p, "RADIUS", c)
        default_vc = self.parameterAsDouble(p, "VC_LENGTH", c)
        names = {n.lower(): n for n in source.fields().names()}

        def field(feature, key, default=None):
            name = names.get(key)
            if not name:
                return default
            value = feature[name]
            return default if value in (None, "") or str(value) == "NULL" else value

        out, used = [], set()
        for k, feature in enumerate(source.getFeatures()):
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            parts = list(geom.constParts())
            if len(parts) > 1:
                fb.pushWarning(f"Feature {feature.id()}: only the first part of a multi-line is used.")
            vertices = list(parts[0].vertices())
            xy = [(v.x(), v.y()) for v in vertices]
            name = str(field(feature, "name", f"Road {k + 1}"))
            base, n = name, 2
            while name in used:
                name = f"{base} ({n})"
                n += 1
            used.add(name)
            radius = float(field(feature, "radius", default_r))
            design = PlanDesign.from_polyline(xy, radius, name)
            spin, spout = float(field(feature, "spiral_in", 0) or 0), float(field(feature, "spiral_out", 0) or 0)
            if spin or spout:
                for cv in design.curves:
                    if cv:
                        cv["spiral_in"], cv["spiral_out"] = spin, spout
                design.fit_radii()
            if design.alignment is None:
                fb.pushWarning(f"{name}: {design.error or 'could not be fitted'}; skipped.")
                continue
            shrunk = [cv["radius"] for cv in design.curves if cv and cv["radius"] < radius - 1e-6]
            if shrunk:
                fb.pushWarning(f"{name}: {len(shrunk)} curve(s) reduced below {radius:g} to fit (smallest {min(shrunk):.1f}).")
            profiles = []
            if QgsWkbTypes.hasZ(geom.wkbType()):
                pts = design.alignment.points(1.0)
                stations = []
                for v in vertices:
                    best = min(pts, key=lambda q: (q[0] - v.x()) ** 2 + (q[1] - v.y()) ** 2)
                    stations.append((best[2], v.z()))
                stations[0] = (design.alignment.start_station, stations[0][1])
                stations[-1] = (design.alignment.start_station + design.alignment.length, stations[-1][1])
                vc = float(field(feature, "vc_length", default_vc) or 0)
                pvis = []
                for i, (s, z) in enumerate(stations):
                    if pvis and s <= pvis[-1][0] + 1.0:
                        continue
                    pvis.append([s, z, vc if 0 < i < len(stations) - 1 else 0.0])
                for i in range(1, len(pvis) - 1):                 # shrink curves that would overlap
                    room = min(pvis[i][0] - pvis[i - 1][0], pvis[i + 1][0] - pvis[i][0])
                    pvis[i][2] = min(pvis[i][2], room)
                try:
                    profiles.append(VerticalProfile(f"{name} FRL", [tuple(v) for v in pvis]))
                except ValueError as error:
                    fb.pushWarning(f"{name}: design profile not written ({error}).")
            if dem is not None:
                provider = dem.dataProvider()
                ground = []
                for x, y, s in design.alignment.points(step):
                    value, ok = provider.sample(QgsPointXY(x, y), 1)
                    if ok and value is not None and math.isfinite(value):
                        ground.append((s, float(value)))
                if len(ground) >= 2:
                    profiles.append(GroundProfile(f"{name} ground", ground))
            out.append((design.alignment, profiles))
            fb.pushInfo(f"{name}: {design.alignment.length:.2f} long, {len(design.pis)} PIs, "
                        f"{len(profiles)} profile(s).")
        if not out:
            raise QgsProcessingException("No line could be written.")
        epsg = int(crs.authid().split(":")[1]) if crs.authid().upper().startswith("EPSG:") else None
        path = self.parameterAsFileOutput(p, "OUTPUT", c)
        write_landxml(path, out, linear_unit=unit, epsg=epsg, northing_first=axis == 1, group="Roads")
        return {"OUTPUT": path, "COUNT": len(out)}


class ExportDesignStageAlgorithm(ProjectSettingsAlgorithm):
    """A concept at a chosen stage, written for Civil 3D or OpenRoads Designer."""

    def createInstance(self):
        return ExportDesignStageAlgorithm()

    def name(self):
        return "landxml_export_design_stage"

    def displayName(self):
        return "Export Design for Civil 3D or OpenRoads (by Stage)"

    def group(self):
        return GROUP

    def groupId(self):
        return GROUP_ID

    def shortHelpString(self):
        return ("Writes the design of a section project as a LandXML for further work in Civil 3D or OpenRoads Designer, "
                "at the stage you need:\n"
                "<b>Horizontal alignment only</b> - to start the geometry there;\n"
                "<b>Alignment and profiles</b> - the design profile and the ground along the centreline;\n"
                "<b>Surfaces and feature lines</b> - the finished and datum TINs and the edges by code;\n"
                "<b>Everything</b> - also the cross-sections (CrossSects with ground, finished surface, course shapes).\n\n"
                "For <b>Civil 3D</b> the feature lines are PlanFeatures with three-point curves and elevation points (the form "
                "Civil 3D imports as feature lines); for <b>OpenRoads Designer</b> they are breaklines of the finished "
                "surface and plain PlanFeatures. 'Include the ground' adds the existing ground over the corridor "
                "footprint. The source LandXML is never changed; coordinates keep the file's axis order and CRS.")

    def initAlgorithm(self, config=None):
        from .landxml.corridor_build import STAGE_LABELS, STAGES

        self.addParameter(QgsProcessingParameterFile("PROJECT", "Section project", fileFilter="Section project (*.json)"))
        self.addParameter(QgsProcessingParameterEnum("TARGET", "For", options=["Civil 3D", "OpenRoads Designer"],
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("STAGE", "Stage", options=[STAGE_LABELS[s] for s in STAGES],
                                                     defaultValue=3))
        self.addParameter(QgsProcessingParameterBoolean("GROUND", "Include the ground over the corridor footprint",
                                                        defaultValue=False))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "LandXML", fileFilter="LandXML (*.xml)"))

    def processAlgorithm(self, p, c, fb):
        from .landxml.corridor_build import STAGES, TARGETS

        project = cb.load_project(self.parameterAsFile(p, "PROJECT", c))
        out = self.parameterAsFileOutput(p, "OUTPUT", c)
        if os.path.abspath(out) == os.path.abspath(project["landxml"]):
            raise QgsProcessingException("The source LandXML is never overwritten; choose a new output file.")
        try:
            result = cb.build_corridor(project, progress=lambda v, m: fb.setProgress(v * 0.8))
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error
        target, stage = TARGETS[self.parameterAsEnum(p, "TARGET", c)], STAGES[self.parameterAsEnum(p, "STAGE", c)]
        cb.write_corridor_landxml(result, out, self.parameterAsBoolean(p, "GROUND", c), target, stage)
        fb.pushInfo(f"Wrote the {stage} stage for {'Civil 3D' if target == 'civil3d' else 'OpenRoads Designer'}: {out}")
        return {"OUTPUT": out}
