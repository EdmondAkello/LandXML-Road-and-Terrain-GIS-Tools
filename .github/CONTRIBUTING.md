# Contributing to LandXML Road & Terrain GIS Tools

Bug reports, sample files, questions and code are all welcome.

## Reporting a problem

Open an [issue](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/issues) with:
- the QGIS version and operating system;
- the plugin version;
- the tool and its parameters;
- the software that wrote the LandXML file (Civil 3D, OpenRoads, 12d, etc.) and its version;
- what you expected and what happened, with the Processing log.

A LandXML file that reproduces the problem helps most. If the design is confidential, a cut-down or synthetic file with the same structure is enough. Please don't attach files you are not free to share.

## Changing the code

1. Open an issue first for anything beyond a small fix.
2. Fork the repository and work on a branch (`fix/...`, `feat/...`, `docs/...`).
3. Open a pull request against `main` that refers to the issue. CI runs the parser and earthworks tests and Bandit, and both must pass.

### Ground rules

- **Never guess coordinates.** The CRS, the vertical datum and the northing/easting order are stated by the user or read from the file, not inferred.
- **Keep units distinct.** Stations, lengths, grades and speeds keep their units through the code. Conversions are explicit.
- **Parse defensively.** XML goes through `safe_xml.py`, and the parser stays namespace-tolerant so that files from different vendors read the same way.
- **Design criteria live in `criteria/`**, not in code, so that other standards can be added as data.
- **Every change ships with tests** in `tests/` (synthetic fixtures in `tests/fixtures/`), plus updates to the README, the user guide and CHANGELOG.md.

### Running the tests

You need Python with NumPy and GDAL (the Python that ships with QGIS works).

```bash
python3 -m unittest -v tests/test_landxml.py tests/test_earthworks.py   # as CI runs them
python3 -m unittest -v tests/test_qgis_processing.py tests/test_prepare.py tests/test_gui.py  # need QGIS
bandit -q -r . -x ./tests,./dist
```

### AI-assisted contributions

If you are contributing from outside the project and an AI tool wrote a substantial part of your change, mention it in the pull request and name the tool. You remain responsible for the change: you have read it, tested it and can explain it.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Maintainer

The plugin is maintained by Edmond Akello. Decisions on scope and design are made in issues and pull requests, in public.
