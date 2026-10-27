# Contribution workflow

- Open an issue for each feature before starting implementation.
- Submit one pull request per feature, linked to its issue. Keep unrelated features in separate pull requests to make review easier.
- Work on a branch in the fork and target upstream `main`. Open an issue for any other change beyond a small fix.
- Explain what changed and why in the PR, link the issue with `Closes #...`, and report validation results.
- Update `CHANGELOG.md` with each change. Also update the README and user guide to reflect affected behavior.

## Versioning

- Before delivering an updated plugin or release ZIP, bump the version using semantic versioning (`MAJOR.MINOR.PATCH`). Choose the bump from user impact and compatibility, rather than the number of changed lines.
- **Patch** (for example, `1.10.0` → `1.10.1`): compatible bug fixes, small UI corrections, or internal improvements.
- **Minor** (for example, `1.10.0` → `1.11.0`): new features or substantial enhancements that preserve existing supported workflows.
- **Major** (for example, `1.10.0` → `2.0.0`): incompatible changes, such as removing a Processing algorithm, renaming parameters used by saved models, removing output fields used by scripts, or changing established station/offset meanings. Explain the migration in the changelog and user guide.
- Use one version for intermediate edits within the same undelivered release. Once a plugin ZIP has been delivered, give any subsequent runtime update a new version. Documentation-only or contributor-instruction changes do not require a plugin version bump.
- Keep `metadata.txt` (version and changelog), `CITATION.cff` (version and release date), `CHANGELOG.md`, and current README/user-guide version references consistent. Keep historical release references intact, and identify older PDF editions explicitly.
- Rebuild with `python tools/build_release.py`, confirm the ZIP filename and embedded metadata use the new version, and report validation results before delivery.

## Upstream contribution rules

Source: [upstream contribution guide](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/blob/main/.github/CONTRIBUTING.md) and [PR checklist](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/blob/main/.github/pull_request_template.md).

- Never infer the CRS, vertical datum, or northing/easting order. Use values supplied by the user or declared in the file.
- Preserve the units of stations, lengths, grades, and speeds. Make conversions explicit.
- Parse XML through `safe_xml.py` and keep parsing tolerant of vendor namespaces.
- Store design standards in `criteria/` as data rather than hard-coding them.
- Include tests for code changes in `tests/`, using synthetic fixtures in `tests/fixtures/` for new or changed behavior.
- Parser, earthworks, criteria-table tests, and Bandit must pass. Run affected QGIS tests in a QGIS environment; report any unavailable runtime checks.

```sh
python3 -m unittest -v tests/test_landxml.py tests/test_earthworks.py tests/test_criteria_tables.py
python3 -m unittest -v tests/test_qgis_processing.py tests/test_prepare.py tests/test_gui.py
bandit -q -r . -x ./tests,./dist
```

- Tests require NumPy and GDAL; Processing and GUI tests also require QGIS. Use the matching Python environment.
- Bug reports should include QGIS, OS, plugin and exporting-software versions; the tool and parameters; expected and actual behavior; and the Processing log. Share only authorized sample files; use reduced or synthetic reproductions for confidential designs.
- Feature requests should explain the design task, current limitation, desired behavior, and relevant standard or clause.
- For outside contributions substantially written by AI, name the tool in the PR. Read, test, and be able to explain the contribution.
- Follow the upstream [code of conduct](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/blob/main/.github/CODE_OF_CONDUCT.md): be respectful and constructive, keep discussion focused on the work, and protect private information. Discuss scope and design publicly in issues and PRs.

## QGIS publishing

Source: [QGIS publishing guidelines](https://plugins.qgis.org/docs/publish). Requirements and recommendations below apply when preparing releases.

### Requirements

- Provide basic documentation, an English summary, and valid metadata links for usage, public source, issue tracking, and licensing. Keep ZIPs out of source repositories.
- License compatibly with GPLv2 or later; respect dependency and asset licenses.
- Declare external dependencies in About metadata and explain installation.
- Exclude binaries; limit packages to 25 MB.

### Recommendations and packaging checks

- Match release ZIP contents to declared source.
- Include README and LICENSE; omit generated code/help, caches, VCS/hidden directories, and macOS debris from distribution. Organize code; follow PEP8 and Python/QGIS conventions; comment in English.
- Include test data; support Windows, Linux, and macOS. Describe platform, software, account, and geographic restrictions. Explain Windows installation for dependencies absent from OSGeo4W.
- Use appropriate menus, QGIS widgets, and `QgsNetworkAccessManager` for proxy-aware networking.
- Keep titles stable across QGIS upgrades; avoid redundant “plugin” wording in names/folders.
- Check duplication and collaboration before creating plugins; prefer established QGIS scaffolding templates.
