"""Run the plugin's Processing tools on the scenarios that make_scenarios.py built, and write an index page of what
ran, how long it took, what failed and where the reports and viewers are.

    python tests/scenarios/run_tools.py [--out tests/scenarios/out] [--only golf_hole ...] [--tools landxml_grading_report ...]
    python tests/scenarios/run_tools.py --resume --budget 150     # in steps: skip what passed, stop starting after 150 s

Every run is independent: a tool that fails is recorded with its message and the rest go on. Results are merged per
tool into each scenario's tool_runs.json, so runs can be split up (one scenario or one tool at a time) and resumed.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from make_scenarios import plugin, start_qgis  # noqa: E402

APP = None
EG, DESIGN, CORRIDOR = "Existing ground", "Design", "Corridor"


def shape_runs(record, folder):
    xml = record["landxml"]
    kinds = {s.get("shape") for s in record.get("shapes", [])}
    runs = [
        ("inspect_landxml", dict(INPUT=xml, OUTPUT=f"{folder}/inspect.html")),
        ("landxml_tin_to_geotiff", dict(INPUT=xml, SURFACE=DESIGN, RESOLUTION=2.0, OUTPUT=f"{folder}/design_from_tin.tif")),
        ("landxml_surface_boundary", dict(INPUT=xml, SURFACE=DESIGN, OUTPUT=f"{folder}/design_boundary.gpkg")),
        ("landxml_feature_lines", dict(INPUT=xml, OUTPUT=f"{folder}/feature_lines.gpkg")),
        ("landxml_surface_cut_fill", dict(INPUT=xml, BASE_SURFACE=EG, COMPARE_SURFACE=DESIGN, RESOLUTION=2.0,
                                          OUTPUT=f"{folder}/cut_fill.tif", REPORT=f"{folder}/cut_fill.html")),
        ("landxml_grading_quantities", dict(INPUT=xml, VOLUME_SURFACE=DESIGN, EXISTING_SURFACE=EG,
                                            REPORT=f"{folder}/grading_quantities.html",
                                            FEATURE_LINES=f"{folder}/grading_quantities_lines.xml",
                                            BANDS_CSV=f"{folder}/grading_bands.csv")),
        ("landxml_earthwork_balance", dict(INPUT=xml, VOLUME_SURFACES=f"{DESIGN} | {EG}",
                                           REPORT=f"{folder}/earthwork_balance.html",
                                           TABLE_CSV=f"{folder}/earthwork_balance.csv")),
        ("landxml_platform_levels", dict(INPUT=xml, DESIGN_SURFACE=DESIGN, OUTPUT=f"{folder}/platforms.gpkg",
                                         REPORT=f"{folder}/platforms.html")),
        ("landxml_grading_edges", dict(INPUT=xml, DESIGN_SURFACE=DESIGN, EXISTING_SURFACE=EG,
                                       OUTPUT=f"{folder}/edges.gpkg", REPORT=f"{folder}/edges.html",
                                       SCHEDULE_CSV=f"{folder}/edges.csv")),
        ("landxml_surface_style", dict(INPUT=xml, SURFACE=DESIGN, OUTPUT=f"{folder}/slope_classes.gpkg")),
        ("landxml_grading_report", dict(INPUT=xml, DESIGN_SURFACE=DESIGN, EXISTING_SURFACE=EG,
                                        TITLE=record["name"].replace("_", " ").title(), REPORT=f"{folder}/grading_report.html")),
        ("landxml_export_scene", dict(INPUT=xml, DESIGN_SURFACE=DESIGN, EXISTING_SURFACE=EG,
                                      OUTPUT_FOLDER=f"{folder}/scene_site")),
        ("landxml_3d_scene", dict(INPUT=xml, SURFACES=f"{EG};{DESIGN}", OUTPUT_FOLDER=f"{folder}/scene")),
        ("landxml_dem_to_tin", dict(DEM=record["ground"], TRIANGULATION=0, TOLERANCE=0.25, MAX_TRIANGLES=0, VERTICAL_UNIT=0,
                                    OUTPUT=f"{folder}/ground_tin.xml")),
    ]
    if kinds & {"basin", "pit"}:
        runs.append(("landxml_basin_storage", dict(INPUT=xml, BASIN_SURFACE=DESIGN, REPORT=f"{folder}/basin.html",
                                                   TABLE_CSV=f"{folder}/basin.csv")))
    return runs


def road_runs(record, folder):
    xml, name = record["landxml"], record["alignment"]
    prepared = f"{folder}/road_prepared.xml"
    speed = float(record.get("design_speed", 60))
    return [
        ("inspect_landxml", dict(INPUT=xml, OUTPUT=f"{folder}/inspect.html")),
        ("landxml_alignments_to_vector", dict(INPUT=xml, OUTPUT=f"{folder}/alignment.gpkg")),
        ("landxml_3d_centerlines", dict(INPUT=xml, OUTPUT=f"{folder}/centerline_3d.gpkg")),
        ("landxml_station_points", dict(INPUT=xml, INTERVAL=20.0, OUTPUT=f"{folder}/stations.gpkg")),
        ("landxml_profiles_to_vector", dict(INPUT=xml, OUTPUT=f"{folder}/profiles.gpkg")),
        ("landxml_tin_to_geotiff", dict(INPUT=xml, SURFACE=CORRIDOR, RESOLUTION=1.0, OUTPUT=f"{folder}/corridor.tif")),
        ("landxml_surface_cut_fill", dict(INPUT=xml, BASE_SURFACE=EG, COMPARE_SURFACE=CORRIDOR, RESOLUTION=1.0,
                                          OUTPUT=f"{folder}/cut_fill.tif", REPORT=f"{folder}/cut_fill.html")),
        ("prepare_landxml", dict(INPUT=xml, ALIGNMENT=name, EXISTING_SURFACE=EG, PROPOSED_SURFACE=CORRIDOR,
                                 SECTION_INTERVAL=20.0, LEFT_WIDTH=50.0, RIGHT_WIDTH=50.0, CONFIRM_MATCHING=True,
                                 OUTPUT=prepared)),
        ("landxml_cross_sections", dict(INPUT=prepared, OUTPUT=f"{folder}/cross_sections.gpkg",
                                        POINTS=f"{folder}/cross_section_points.gpkg")),
        ("landxml_profile_cut_fill", dict(INPUT=xml, DESIGN_PROFILE="Design FRL", GROUND_PROFILE=EG,
                                          OUTPUT=f"{folder}/profile_cut_fill.gpkg", REPORT=f"{folder}/profile_cut_fill.html")),
        ("landxml_corridor_quantities", dict(INPUT=prepared, GROUND_SURFACE=EG, DESIGN_SURFACE=CORRIDOR,
                                             OUTPUT=f"{folder}/corridor_sections.gpkg",
                                             REPORT=f"{folder}/corridor_quantities.html")),
        ("landxml_cross_section_sheets", dict(INPUT=prepared, GROUND_SURFACE=EG, DESIGN_SURFACE=CORRIDOR, INTERVAL=100.0,
                                              OUTPUT=f"{folder}/cross_section_sheets.pdf",
                                              REPORT=f"{folder}/cross_sections.html")),
        ("landxml_design_report", dict(INPUT=xml, DESIGN_SPEED=speed, OUTPUT=f"{folder}/design_report.html")),
        ("landxml_design_review", dict(INPUT=prepared, DESIGN_SPEED=speed, GROUND_SURFACE=EG, DESIGN_SURFACE=CORRIDOR,
                                       OUTPUT=f"{folder}/review.gpkg", REPORT=f"{folder}/design_review.html")),
        ("landxml_complete_road_design", dict(INPUT=xml, SURFACE=CORRIDOR, RESOLUTION=2.0, LOAD_OUTPUTS=False,
                                              OUTPUT_DIR=f"{folder}/road_package")),
        ("landxml_3d_scene", dict(INPUT=xml, SURFACES=f"{EG};{CORRIDOR}", OUTPUT_FOLDER=f"{folder}/scene")),
    ]


def dem_runs(record, folder):
    return []                                  # make_scenarios.py ran the DEM to TIN comparison


def layer_for(value):
    """Raster paths as layers (the Processing parameters take layers when called directly)."""
    from qgis.core import QgsRasterLayer
    if isinstance(value, str) and value.lower().endswith(".tif") and Path(value).exists():
        return QgsRasterLayer(value, Path(value).stem)
    return value


def run(algorithms, tool, params, crs=None):
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback

    class Feedback(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.lines = []

        def pushInfo(self, text):
            self.lines.append(text)

        def pushWarning(self, text):
            self.lines.append("WARNING: " + text)

        def reportError(self, text, fatal=False):
            self.lines.append("ERROR: " + text)

    algorithm = algorithms[tool].createInstance()
    algorithm.initAlgorithm()
    if "DEM" in params:
        params = dict(params, DEM=layer_for(params["DEM"]))
    names = {d.name() for d in algorithm.parameterDefinitions()}
    if "OUTPUT_CRS" in names and crs:                  # chosen explicitly, as a user does: the tools never infer it
        params = dict(params, OUTPUT_CRS=crs, METHOD=params.get("METHOD", 1))   # 1 = swap: the files are northing first
    if "VERTICAL_UNIT" in names and "DEM" not in params:
        params.setdefault("VERTICAL_UNIT", 0)          # as declared in the file (the scenarios declare it)
    for key, value in params.items():
        if key.startswith("OUTPUT_FOLDER") or key == "OUTPUT_DIR" or key == "PNG_FOLDER":
            if key == "OUTPUT_DIR" and Path(value).exists():      # the road package refuses to overwrite: our own
                import shutil                                      # earlier output, so clear it first
                shutil.rmtree(value)
            Path(value).mkdir(parents=True, exist_ok=True)
    feedback, started = Feedback(), time.time()
    try:
        result = algorithm.processAlgorithm(params, QgsProcessingContext(), feedback)
        status, message = "ok", ""
    except Exception as error:
        result, status, message = {}, "failed", f"{type(error).__name__}: {error}\n{traceback.format_exc(limit=4)}"
    warnings = [line for line in feedback.lines if line.startswith(("WARNING", "ERROR"))]
    numbers = {k: round(v, 3) if isinstance(v, float) else v for k, v in (result or {}).items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"tool": tool, "status": status, "seconds": round(time.time() - started, 2), "message": message,
            "warnings": warnings[:10], "numbers": numbers,
            "files": sorted({str(v) for v in (result or {}).values() if isinstance(v, str) and _is_file(v)})}


def run_in_scratch(algorithms, tool, params, crs, folder, scratch):
    """``run`` with the outputs (paths in ``folder``) written under ``scratch`` and copied back afterwards; inputs stay
    where they are."""
    import shutil
    local = scratch / folder.name
    local.mkdir(parents=True, exist_ok=True)
    inputs = {"INPUT", "DEM", "EXISTING_FILE", "INPUT_B", "PROPOSED_FILE", "GROUND"}
    moved = {}
    for key, value in params.items():
        if key not in inputs and isinstance(value, str) and value.startswith(str(folder)):
            target = str(local / Path(value).relative_to(folder))
            moved[target] = value
            params = dict(params, **{key: target})
    outcome = run(algorithms, tool, params, crs)
    for target, original in moved.items():
        source = Path(target)
        if source.is_dir():
            shutil.copytree(source, original, dirs_exist_ok=True)
        elif source.exists():
            Path(original).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, original)
    for sibling in local.glob("*"):        # side files a tool writes next to its output (e.g. .aux.xml, .qml)
        destination = folder / sibling.name
        if sibling.is_file() and not destination.exists():
            shutil.copyfile(sibling, destination)
    outcome["files"] = sorted({moved.get(f, f) for f in outcome["files"]})
    return outcome


def _is_file(value):
    try:
        return len(value) < 400 and "\n" not in value and Path(value).exists()
    except OSError:
        return False


def index_page(out, results):
    rows = []
    for record in results:
        rows.append(f"<h2>{html.escape(record['name'])}</h2><p>{html.escape(record.get('site', ''))} · "
                    f"centre {record.get('centre')} · {html.escape(record.get('status', ''))}</p>")
        rows.append("<table><tr><th>Tool</th><th>Result</th><th>Time (s)</th><th>Numbers</th><th>Files</th><th>Notes</th></tr>")
        for r in record.get("runs", []):
            files = " ".join(f'<a href="{html.escape(Path(f).relative_to(out).as_posix())}">{html.escape(Path(f).name)}</a>'
                             for f in r["files"] if Path(f).suffix.lower() in (".html", ".pdf", ".csv", ".xml")
                             and str(f).startswith(str(out)))
            for f in r["files"]:
                index = Path(f) / "index.html"
                if index.exists():
                    files += f' <a href="{html.escape(index.relative_to(out).as_posix())}">viewer</a>'
            numbers = ", ".join(f"{k} {v:,}" if isinstance(v, (int, float)) else f"{k} {v}" for k, v in r["numbers"].items())
            notes = html.escape((r["message"].splitlines() or [""])[0] if r["message"] else "; ".join(r["warnings"][:3]))
            rows.append(f'<tr class="{r["status"]}"><td>{r["tool"]}</td><td>{r["status"]}</td><td>{r["seconds"]}</td>'
                        f"<td>{html.escape(numbers)}</td><td>{files}</td><td>{notes}</td></tr>")
        rows.append("</table>")
    page = ("<!doctype html><meta charset='utf-8'><title>Scenario tool runs</title><style>body{font-family:system-ui,"
            "sans-serif;margin:24px}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #d5dbe3;"
            "padding:3px 8px;vertical-align:top}th{background:#eef2f6}tr.failed td{background:#fdecec}</style>"
            "<h1>Scenario tool runs</h1>" + "".join(rows))
    (out / "index.html").write_text(page, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(HERE / "out"))
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--tools", nargs="*")
    parser.add_argument("--resume", action="store_true", help="skip tools that already passed")
    parser.add_argument("--budget", type=float, default=0, help="seconds after which no new tool is started (0 = none)")
    parser.add_argument("--scratch", help="write each tool's outputs to this local folder first, then copy them into "
                                          "--out (for an out folder on a slow network or VM mount)")
    args = parser.parse_args(argv)
    started_all = time.time()
    pending = 0
    plugin()
    global APP
    APP = start_qgis()
    from qgis.core import QgsApplication
    from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider as Provider
    provider = Provider()
    QgsApplication.processingRegistry().addProvider(provider)
    algorithms = {a.name(): a for a in provider.algorithms()}
    out = Path(args.out).resolve()
    results = []
    for path in sorted(out.glob("*/scenario.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if args.only and record["name"] not in args.only:
            previous = path.parent / "tool_runs.json"          # keep earlier runs on the index page
            if previous.exists():
                record["runs"] = json.loads(previous.read_text(encoding="utf-8"))
            results.append(record)
            continue
        if record.get("status") != "built":
            results.append(record)
            continue
        folder = path.parent
        runs = {"road": road_runs, "shapes": shape_runs, "dem_tin": dem_runs}[record["kind"]](record, folder)
        saved = folder / "tool_runs.json"
        done = {r["tool"]: r for r in json.loads(saved.read_text(encoding="utf-8"))} if saved.exists() else {}
        for tool, params in runs:
            if args.tools and tool not in args.tools:
                continue
            if args.resume and done.get(tool, {}).get("status") == "ok":
                continue
            if args.budget and time.time() - started_all > args.budget:
                pending += 1
                continue
            if args.scratch:
                outcome = run_in_scratch(algorithms, tool, params, f"EPSG:{record['epsg']}", folder, Path(args.scratch))
            else:
                outcome = run(algorithms, tool, params, f"EPSG:{record['epsg']}")
            done[tool] = outcome
            saved.write_text(json.dumps([done[t] for t, _p in runs if t in done], indent=2), encoding="utf-8")
            print(f"{record['name']:26s} {tool:36s} {outcome['status']:6s} {outcome['seconds']:7.2f}s "
                  f"{(outcome['message'].splitlines() or [''])[0][:110]}", flush=True)
        record["runs"] = [done[t] for t, _p in runs if t in done]
        results.append(record)
    index_page(out, results)
    failed = sum(r["status"] == "failed" for rec in results for r in rec.get("runs", []))
    total = sum(len(rec.get("runs", [])) for rec in results)
    print(f"{total - failed} of {total} tool runs ok; {pending} not started (budget); index: {out / 'index.html'}")


if __name__ == "__main__":
    main()
