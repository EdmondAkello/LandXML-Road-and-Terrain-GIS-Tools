# QGIS 4.2.2 / Qt 6 compatibility audit

The initial version 1.5.0 audit ran on a QGIS 4.2.2 macOS runtime. Every plugin Python source at that point was compiled, checked with Ruff (`F,E4,E7,E9`), and imported through QGIS 4.2.2. The provider registered all 11 algorithms; the automated Processing suite exercised vector sinks, profile graph sinks, GeoPackage/OGR, TIN/GeoTIFF, custom report output, and ftUS CRS checks. After the profile and stationing changes, the updated PR branch passed all 31 parser and headless Processing tests with the macOS QGIS 4.2.2 Python runtime. The developer subsequently reported that the QGIS 4 desktop regression workflow completed successfully. The same 31 tests had also passed under Windows QGIS 3.44.3.

| File(s) | Finding and action |
| --- | --- |
| `__init__.py`, `landxml_tin_to_geotiff.py`, `provider.py` | Plugin entry and registry APIs load under QGIS 4.2.2. Provider name updated; Inspect registered. No Qt signals/slots or `exec_()` calls. |
| `params.py` | `QgsProcessingParameterNumber.Type.Double` and `setMetadata` initialized successfully under QGIS 4.2.2. No QGIS 4-only parameter class introduced. |
| `compat.py`, `alignment_algorithm.py`, `road_features.py` | Replaced `QVariant`-typed `QgsField` construction with `qgis.PyQt.QtCore.QMetaType.Type`; QGIS 3.44 documents the same constructor. Existing `QgsWkbTypes.Type` and `QgsProcessing.SourceType` names were verified in QGIS 4.2.2. |
| `processing_algorithm.py`, `complete_export.py`, `road_features.py`, `alignment_algorithm.py` | Removed hard-coded EPSG:32636 output and EPSG:4326 source defaults. Explicit source/output CRS choices and unit consistency checks are shared in `processing_common.py`. GDAL/OGR uses selected CRS WKT, permitting custom CRS output where supported. |
| `core.py` | GDAL/OSR transformations use explicit source/output WKT with traditional GIS axis mapping. GeoTIFF projection accepts WKT. Rasterization and triangle topology were exercised under bundled GDAL 3.13.3. No QgsRaster provider/writer API is used. |
| `safe_xml.py`, `landxml/*.py` | XML interpretation is independent of PyQGIS; root-derived namespace handling replaces the fixed LandXML 1.2 URI. Safe expat parser remains in use; no Qt dependency. |
| `inspect_algorithm.py` | Standard Processing file input and optional JSON destination initialized and executed under QGIS 4.2.2. |

Repository-wide scans found **no direct PyQt5/PyQt6 imports, Qt 5 enum spellings, `QVariant` field constructors, `exec_()` calls, or signal/slot code** in active plugin Python. The limited Qt imports are through `qgis.PyQt`. The plugin uses GDAL/OGR for raster and GeoPackage output, not deprecated PyQGIS raster-writer APIs. Geometry constructors and Processing parameter/sink types were exercised by the tests.

Desktop smoke test: QGIS 4.2.2 discovered and enabled the plugin, ran Inspect LandXML on an OpenRoads terrain export, created a TIN GeoTIFF with an explicitly chosen axis swap and ftUS output CRS, and loaded the result over imagery. The GeoTIFF's CRS, pixel size, NoData and populated elevation values were also checked through GDAL. The visual overlay does not establish survey accuracy or the source's undeclared vertical unit. Project files are not included in this branch.

Windows QGIS 3.44.3 passed 31 parser and headless Processing tests. The suite covers provider unload after QGIS deletes it, partial profile coverage in both 3D centerline outputs, interval-aligned station points, labeled VPC/VPI/VPT profile controls with vertical exaggeration, grades and K, and schematic map profile placement. `processing.runAndLoadResults` loaded labels on both temporary and saved graph outputs. A private OpenRoads road export was exercised through the same tools, including a map profile overlay with an explicitly selected ftUS CRS and X/Y swap, without committing the source file.

Remaining runtime checks: manually inspect the new profile labels, graphs and map overlay under QGIS 3.44, test custom/project CRSs, and compare real outputs with survey control. The developer's QGIS 4 desktop result is reported rather than independently recorded in this repository. Headless and limited desktop execution do not prove survey accuracy or compatibility with every later QGIS 4.x release.

API references: [QGIS 4.2 `QgsField`](https://api.qgis.org/api/4.2/classQgsField.html), [QGIS 3.44 PyQGIS developer cookbook](https://docs.qgis.org/3.44/pdf/en/QGIS-3.44-PyQGISDeveloperCookbook-en.pdf), [QGIS 3.44 `QgsWkbTypes`](https://api.qgis.org/api/3.44/classQgsWkbTypes.html).
