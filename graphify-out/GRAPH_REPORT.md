# Graph Report - LandXML-Road-and-Terrain-GIS-Tools  (2026-10-30)

Source snapshot: [`a90be42a904485ddcfddea2d3c999beea27eb2ae`](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/commit/a90be42a904485ddcfddea2d3c999beea27eb2ae) on `codex/39-skip-unavailable-road-outputs`, not current `main`. See [README.md](README.md) for viewing, provenance and refresh instructions.

## Corpus Check
- 112 files · ~113,792 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: .xml 8, (none) 2, .cff 1)

## Summary
- 1586 nodes · 3802 edges · 89 communities (58 shown, 31 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 354 edges (avg confidence: 0.87)
- Token cost: unavailable. Host agent tools do not expose actual token usage; zero-valued accounting counters are placeholders, not a measurement.

## Community Hubs (Navigation)
- Profile Viewer Rendering
- Safe XML and Speeds
- Station Locator Integration
- Processing Provider Registry
- Older Manual and Screenshots
- QGIS Processing Tests
- Product Documentation
- Processing Algorithm Integration
- LandXML Element Parsing
- Output Names and Styles
- Continuous Integration Checks
- Geometric Design Review
- Coordinate Processing Helpers
- Profile Preparation Tests
- Processing Parameter Widgets
- TIN Parser Validation
- HTML Report Generation
- Design Criteria Interpolation
- Criteria Table Tests
- Styled Road Algorithms
- Alignment Geometry and Profiles
- Section GUI Integration
- Viewer Map Picking
- Report Charts and Labels
- Alignment Stationing
- Engineering Test Modules
- Synthetic Guide Fixtures
- LandXML Preparation
- Section Panel Navigation
- Test Harness Documentation
- Corridor Surface Sections
- Design Report Assembly
- Release ZIP Validation
- GUI Test Infrastructure
- Complete Road Export
- TIN Raster Processing
- LandXML Catalog Discovery
- Profile Earthworks Tests
- Design Speed Review
- Corridor Volume Quantities
- Section Chart Interaction
- Recommended Profile Review
- Project Settings Tests
- Road Feature Sampling
- Earthworks Algorithm Execution
- Vertical Profile Model
- Profile Cut Fill Comparison
- Cross Section Regions
- Section Chart Ranges
- Prepare Algorithm Widgets
- Guide Browser Capture
- Earthworks Map Placement
- Section Side Slopes
- Profile Map Placement
- LandXML Inspection
- Superelevation Lane Slopes
- Terrain Processing Interface
- Recommended LandXML Export
- Cross Section Sheets
- Complete Export Interface
- Surface Earthworks Interface
- Criteria Loader Tests
- Variable Speed Profiles
- Dynamic Name Selection
- Profile Algorithm Interface
- Station Points Interface
- Surface Boundary Interface
- Quantity Report Screenshot
- Review Summary Screenshot
- Profile Viewer Screenshot
- Contribution and Release Policy
- Cut Fill Report Screenshot
- Design Review Dialog
- Gradient Diagram Screenshot
- Alignment Plan Screenshot
- Cross Section Sheet Screenshot
- Layer Result Postprocessing
- Project Settings Dialog
- Terrain Map Screenshot
- Mass Haul Screenshot
- Curvature Diagram Screenshot
- Sections Viewer Screenshot
- Cross Section Tests
- Speed Sections Screenshot
- Quantity Unit Conversions
- Raster Icon Branding
- Vector Icon Branding

## God Nodes (most connected - your core abstractions)
1. `load_document()` - 68 edges
2. `QgisProcessingTests` - 55 edges
3. `ProfileViewerDock` - 48 edges
4. `station_label()` - 45 edges
5. `read_alignments()` - 38 edges
6. `Review` - 36 edges
7. `descendants()` - 35 edges
8. `LandXMLTinToGeoTIFFProvider` - 34 edges
9. `ProfileChart` - 33 edges
10. `local_name()` - 30 edges

## Surprising Connections (you probably didn't know these)
- `Contribution guide` --semantically_similar_to--> `Hardened XML parsing security fixes`  [INFERRED] [semantically similar]
  .github/CONTRIBUTING.md → CHANGELOG.md
- `pick()` --indirect_call--> `alignment()`  [INFERRED]
  landxml/catalog.py → tests/test_prepare.py
- `Preserve source engineering geometry` --semantically_similar_to--> `Preserve terrain and profile coverage gaps`  [INFERRED] [semantically similar]
  HISTORY.md → docs/user-guide/user-guide.html
- `Superelevation Diagram` --semantically_similar_to--> `Road Design Report`  [INFERRED] [semantically similar]
  docs/user-guide/figures/fig_report_superelevation.png → docs/user-guide/LandXML_Road_and_Terrain_GIS_Tools_v1.9.2_User_Guide.pdf
- `Demo Road Design Report Summary` --semantically_similar_to--> `Road Design Report`  [INFERRED] [semantically similar]
  docs/user-guide/figures/fig_report_top.png → docs/user-guide/LandXML_Road_and_Terrain_GIS_Tools_v1.9.2_User_Guide.pdf

## Import Cycles
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> prepare_algorithm.py -> landxml/preparation.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> sections_algorithm.py -> landxml/parser.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> design_report_algorithm.py -> landxml/parser.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> earthworks_algorithm.py -> landxml/parser.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> inspect_algorithm.py -> landxml/parser.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> review_algorithm.py -> landxml/parser.py -> __init__.py`
- 5-file cycle: `__init__.py -> landxml_tin_to_geotiff.py -> provider.py -> road_features.py -> landxml/parser.py -> __init__.py`

## Communities (89 total, 31 thin omitted)

### Community 0 - "Profile Viewer Rendering"
Cohesion: 0.06
Nodes (8): _event_point(), nice_ticks(), ProfileChart, x(), y(), _number(), ProfileViewerDock, write_csv()

### Community 1 - "Safe XML and Speeds"
Cohesion: 0.05
Nodes (24): Hardened XML parsing security fixes, Contributor Covenant 2.1, Contribution guide, Reproducible bug reports, Design task and standard for feature requests, Pull request validation checklist, design_speeds(), Element (+16 more)

### Community 2 - "Station Locator Integration"
Cohesion: 0.05
Nodes (5): StationOffsetDock, classFactory(), LandXMLTinToGeoTIFFPlugin, PluginGuiTests, StationLocatorTests

### Community 3 - "Processing Provider Registry"
Cohesion: 0.05
Nodes (8): DesignReportAlgorithm, CorridorQuantitiesAlgorithm, ProfileCutFillAlgorithm, LandXMLTinToGeoTIFFProvider, DesignReviewAlgorithm, BreaklinesAlgorithm, FeatureLinesAlgorithm, GenericLinesAlgorithm

### Community 4 - "Older Manual and Screenshots"
Cohesion: 0.06
Nodes (46): Outward Cross Slope Convention, Superelevation Diagram, Left Lane Cross Slope, Normal Crown Input, Right Lane Cross Slope, Alignment Report Index, Demo Road Design Report Summary, Road Geometry Summary Cards (+38 more)

### Community 6 - "Product Documentation"
Cohesion: 0.07
Nodes (48): Version 1.10.0 enhancements, Version 1.9.0 enhancements, Corridor quantities internally checked without independent report, Six private Civil 3D validation exports, TIN volume comparison with recorded Civil 3D totals, Observed OpenRoads terrain structure, Undeclared CRS and vertical units remain unknown, Preserve terrain and profile coverage gaps (+40 more)

### Community 7 - "Processing Algorithm Integration"
Cohesion: 0.10
Nodes (9): QGIS 4.2.2 and Qt 6 compatibility audit, Runtime checks do not establish survey accuracy, match_name(), list_standards(), default_pair(), find_profile(), ProjectSettingsAlgorithm, standard_options() (+1 more)

### Community 8 - "LandXML Element Parsing"
Cohesion: 0.10
Nodes (16): child(), descendants(), first_descendant(), local_name(), namespace_map(), detect_vendor(), LineFeature, _points() (+8 more)

### Community 9 - "Output Names and Styles"
Cohesion: 0.08
Nodes (11): _field_name(), _tokens(), _filename_part(), _prepared_metadata(), surface_output_names(), _surface_role(), parse_station(), apply_preset() (+3 more)

### Community 10 - "Continuous Integration Checks"
Cohesion: 0.07
Nodes (33): name, runs-on, steps, PYTHONPATH, QGIS_PREFIX_PATH, QT_QPA_PLATFORM, jobs, bandit (+25 more)

### Community 11 - "Geometric Design Review"
Cohesion: 0.15
Nodes (5): _at(), Review, review_alignment(), round_up(), sight_distance()

### Community 12 - "Coordinate Processing Helpers"
Cohesion: 0.14
Nodes (12): _numeric(), transform_vertices(), cut_fill_raster_style(), attach_post_processor(), BoundsTracker, coordinate_choices(), resolve_name(), attributes() (+4 more)

### Community 13 - "Profile Preparation Tests"
Cohesion: 0.17
Nodes (6): read_alignment_profiles(), TinSampler, alignment(), document(), PreparationTests, surface()

### Community 14 - "Processing Parameter Widgets"
Cohesion: 0.18
Nodes (9): _param_common_local(), _input_param(), _string(), _volume_parameter(), number_param(), coordinate_param(), name_param(), _param_common() (+1 more)

### Community 15 - "TIN Parser Validation"
Cohesion: 0.12
Nodes (4): declared_epsg(), read_tin(), load_document(), ParserTests

### Community 16 - "HTML Report Generation"
Cohesion: 0.18
Nodes (15): quantity_units(), recorded_volume_factor(), _cards(), corridor_report_html(), _document(), _fmt(), _keep_headings(), _numeric_column() (+7 more)

### Community 17 - "Design Criteria Interpolation"
Cohesion: 0.13
Nodes (4): Criteria, at_speed(), interpolate(), _table()

### Community 18 - "Criteria Table Tests"
Cohesion: 0.10
Nodes (5): _load(), MetricTables, _round_up(), _TableChecks, USCustomaryTables

### Community 19 - "Styled Road Algorithms"
Cohesion: 0.08
Nodes (4): LandXMLAlignmentsAlgorithm, Centerline3DAlgorithm, CrossSectionsAlgorithm, StyledAlgorithm

### Community 20 - "Alignment Geometry and Profiles"
Cohesion: 0.16
Nodes (13): horizontal_elements(), _curve_points(), _float_attr(), _line_points(), _radius(), read_alignments(), _spiral_points(), _xy() (+5 more)

### Community 21 - "Section GUI Integration"
Cohesion: 0.15
Nodes (7): _legend(), material_colors(), section_image(), section_svg(), SectionStyle, write_section_sheets(), select_sections()

### Community 22 - "Viewer Map Picking"
Cohesion: 0.11
Nodes (3): alignment_field(), AlignmentPickTool, ProfileViewerTests

### Community 23 - "Report Charts and Labels"
Cohesion: 0.12
Nodes (10): chart_svg(), _anchor(), _axis_ticks(), _decimate(), depth_svg(), line_svg(), profile_svg(), station_label() (+2 more)

### Community 24 - "Alignment Stationing"
Cohesion: 0.12
Nodes (4): plan_svg(), StationedPolyline, _stationed_alignments(), StationingTests

### Community 26 - "Synthetic Guide Fixtures"
Cohesion: 0.15
Nodes (15): at_station(), coord_geom(), cross_slopes(), design_profile(), fmt(), ground(), ground_at(), main() (+7 more)

### Community 27 - "LandXML Preparation"
Cohesion: 0.17
Nodes (13): roadway_surface_refs(), add_node(), _coordinate_metadata(), mark(), metadata(), _pairs(), prepare_landxml(), _profile_for_role() (+5 more)

### Community 30 - "Corridor Surface Sections"
Cohesion: 0.14
Nodes (11): CorridorSection, DesignShape, guess_surfaces(), _pairs(), place_offsets(), placed_section_surfaces(), _profile_name_matches(), read_corridor_sections() (+3 more)

### Community 31 - "Design Report Assembly"
Cohesion: 0.16
Nodes (10): alignment_section(), applicable_speeds(), clip_runs(), clip_series(), curvature_series(), design_report_html(), _fmt_radius(), grade_series() (+2 more)

### Community 32 - "Release ZIP Validation"
Cohesion: 0.21
Nodes (8): ReleaseTests, build(), check(), git_files(), main(), read_metadata(), release_files(), run()

### Community 34 - "Complete Road Export"
Cohesion: 0.18
Nodes (10): _delete_layer(), _fld(), _layer_order(), _ogr_line(), _ogr_srs(), _set_fields(), _write_points(), regular_station_distances() (+2 more)

### Community 35 - "TIN Raster Processing"
Cohesion: 0.20
Nodes (9): bounds(), _crs_from_epsg(), grid_for_bounds(), list_surfaces(), rasterize_tin(), surface_difference(), stage(), write_geotiff() (+1 more)

### Community 36 - "LandXML Catalog Discovery"
Cohesion: 0.18
Nodes (8): _attributes(), Catalog, pick(), _has_hint(), read_catalog(), suggest_terrain_pair(), names_for(), CatalogTests

### Community 37 - "Profile Earthworks Tests"
Cohesion: 0.14
Nodes (5): parse_station_ranges(), template_estimate(), ProfileLine, line(), ProfileTests

### Community 38 - "Design Speed Review"
Cohesion: 0.25
Nodes (4): curve_groups(), speed_function(), DesignSpeedTests, element()

### Community 39 - "Corridor Volume Quantities"
Cohesion: 0.17
Nodes (6): _centre_depth(), corridor_quantities(), design_line(), in_ranges(), _value(), CorridorTests

### Community 41 - "Recommended Profile Review"
Cohesion: 0.19
Nodes (6): _change(), civil3d_profile_text(), finding(), interpolate_dict(), recommended_profile(), ReviewTests

### Community 44 - "Earthworks Algorithm Execution"
Cohesion: 0.28
Nodes (10): categorized_by_kind(), _exclusions(), _placers(), _profile(), _quantity_units(), _resolve(), _resolve_alignment(), _style_output() (+2 more)

### Community 46 - "Profile Cut Fill Comparison"
Cohesion: 0.23
Nodes (6): compare_profiles(), cut_fill_segments(), _interpolate(), _overlaps(), regular_rows(), summarize_segments()

### Community 47 - "Cross Section Regions"
Cohesion: 0.24
Nodes (5): section_areas(), build_section_view(), difference_regions(), _slope_labels(), view_extent()

### Community 48 - "Section Chart Ranges"
Cohesion: 0.18
Nodes (3): auto_range(), draw_section(), _section_z_range()

### Community 50 - "Guide Browser Capture"
Cohesion: 0.20
Nodes (6): { chromium }, path, SHOTS, { chromium }, { execFileSync }, path

### Community 52 - "Section Side Slopes"
Cohesion: 0.40
Nodes (4): _hinge_from_geometry(), SectionLine, side_slopes(), x()

### Community 56 - "Superelevation Lane Slopes"
Cohesion: 0.28
Nodes (4): _float(), lane_slopes(), read_superelevation(), DesignReportTests

### Community 58 - "Recommended LandXML Export"
Cohesion: 0.39
Nodes (4): escape(), quoteattr(), recommended_landxml(), _serialize()

### Community 68 - "Quantity Report Screenshot"
Cohesion: 0.33
Nodes (6): Corridor Material Quantities, Cut, Fill and Net Volume Totals, Demo Road Earthworks Quantity Summary, Ground and Corridor Design Settings, Section Integration Summary, Slope Treatment Totals

### Community 69 - "Review Summary Screenshot"
Cohesion: 0.33
Nodes (6): Finding Category Severity Table, Geometric Design Review Summary, Kenya RDM Volume 1 Part 3 2025, Geometric Review Design Settings, Review Recommendation Scope, Finding Severity and Proposed Changes

### Community 70 - "Profile Viewer Screenshot"
Cohesion: 0.33
Nodes (6): Cut and Fill Segments Table, Depth Difference Panel, Design and Existing Ground Selection, Profile Comparison and VPI Chart, Profile Display and Export Controls, Interactive Profile Viewer

### Community 71 - "Contribution and Release Policy"
Cohesion: 0.40
Nodes (5): Protected main contribution workflow, QGIS publishing requirements, Maintainer coordinated releases, Permanent Zenodo version publication, Unreleased changes

### Community 72 - "Cut Fill Report Screenshot"
Cohesion: 0.60
Nodes (5): Cut and Fill Comparison Summary, Demo Road Cut and Fill Report, Design Minus Ground Depth Plot, Demo Road FRL Design Profile, Demo Road EG Ground Profile

### Community 73 - "Design Review Dialog"
Cohesion: 0.40
Nodes (5): Design Standard and Custom Criteria Selection, Design Speed and Chainage Overrides, Geometric Design Review Dialog, LandXML File and Alignment Input, Profile and Corridor Review Controls

### Community 74 - "Gradient Diagram Screenshot"
Cohesion: 0.40
Nodes (5): Crest Vertical Curve Bands, Grade Percent Segments, Demo Road FRL Gradient Diagram, Sag Vertical Curve Bands, Station Axis

### Community 75 - "Alignment Plan Screenshot"
Cohesion: 0.40
Nodes (5): Line, Curve and Spiral Elements, North Arrow and Meter Scale, Schematic Alignment Plan, Chainage Ticks and Endpoints, Stored Coordinate Display Convention

### Community 76 - "Cross Section Sheet Screenshot"
Cohesion: 0.40
Nodes (5): Corridor Material Layers, Demo Road Cross-Section Sheet, Section Drawing Legend, Cross-Section Cut and Fill Shading, Cross-Section Station Sequence

### Community 79 - "Terrain Map Screenshot"
Cohesion: 0.50
Nodes (4): Road Alignment on Contoured Terrain, Review Finding Markers, Station Labels Along Alignment, Shaded Terrain and Contours

### Community 80 - "Mass Haul Screenshot"
Cohesion: 0.50
Nodes (4): Cumulative Factored Cut Minus Fill, Mass Haul Ordinate Diagram, Station Axis, Zero Balance Reference

### Community 81 - "Curvature Diagram Screenshot"
Cohesion: 0.50
Nodes (4): Horizontal Curvature Diagram, Curve Radius Annotations, Signed Inverse Radius Convention, Station Axis

### Community 82 - "Sections Viewer Screenshot"
Cohesion: 0.67
Nodes (4): Profile Viewer Cross-Sections Tab, Longitudinal Profile Context, Cross-Section Selection Controls, Selected Corridor Cross-Section

### Community 84 - "Speed Sections Screenshot"
Cohesion: 0.67
Nodes (3): Design Speed Sections Table, First Design Speed Section, Second Design Speed Section

## Ambiguous Edges - Review These
- `AASHTO Green Book 2018 US customary` → `Legacy metric only criteria text in guide section 25`  [AMBIGUOUS]
  docs/user-guide/user-guide.html · relation: conceptually_related_to

## Knowledge Gaps
- **95 isolated node(s):** `Contributor Covenant 2.1`, `Reproducible bug reports`, `Design task and standard for feature requests`, `Pull request validation checklist`, `Permanent Zenodo version publication` (+90 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 612 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **31 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
- How does `load_document()` connect LandXML parsing, preparation, profile viewing, and engineering reports?
- Which of the 28 inferred relationships involving `load_document()` need source verification?
- How do `QgisProcessingTests` connect the Processing provider, terrain processing, and parser validation?


## Build limitations

- Graph health warning: 379 dangling-endpoint references, 106 collapsed undirected endpoint pairs, and 3 self-loops in raw extraction; see GRAPH_HEALTH.md. All semantic edge endpoints resolve.
- The v1.9.2 PDF is an older edition; the HTML guide and README are separate sources.
- Code relationships are static extraction, not proof of runtime behavior or engineering correctness.
- XML fixture contents, LICENSE, CITATION.cff, and .gitignore are unsupported by this detector and were not semantically indexed.
- Only the five shareable files in `graphify-out/` are committed; generated internals are ignored and the directory is excluded from plugin release ZIPs.
