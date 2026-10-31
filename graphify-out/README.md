# Repository architecture snapshot

This Graphify snapshot helps contributors navigate source code, documentation and user-guide figures. It is a map of a particular checkout, not a live view of `main` or validation of engineering calculations.

- **Source commit:** [`a90be42a904485ddcfddea2d3c999beea27eb2ae`](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools/commit/a90be42a904485ddcfddea2d3c999beea27eb2ae), from `codex/39-skip-unavailable-road-outputs`. That branch includes the Complete Road Design output fix; this documentation PR does not include that implementation.
- **Generated:** 2026-10-30 with Graphify 0.9.99 and OpenAI Codex.
- **Corpus:** 112 supported files; 1,586 graph nodes, 3,802 edges and 89 communities.
- **Older documentation:** the bundled PDF describes v1.9.2. It is a separate source from the HTML guide and README.

## Open the graph

Download or clone this repository and open [graph.html](graph.html) in a browser. GitHub displays the HTML source rather than running the interactive graph. No server is required.

- [GRAPH_REPORT.md](GRAPH_REPORT.md): community names, connected nodes, inferred links, questions and limitations.
- [graph.json](graph.json): portable graph data with source files, locations and relationship provenance.
- [GRAPH_HEALTH.md](GRAPH_HEALTH.md): raw extraction diagnostics.

## Read the evidence correctly

`EXTRACTED` relationships reflect static extraction or explicit documentation. `INFERRED` links are hypotheses to check against the named source; `AMBIGUOUS` links require further review. A relationship in the graph does not establish a runtime call or engineering correctness.

The raw extraction health check flagged 379 dangling-endpoint references, 106 collapsed undirected endpoint pairs and 3 self-loops. The exported JSON has unique node IDs and resolving link endpoints, but that does not repair the raw extraction gaps. XML fixture contents, LICENSE, CITATION.cff and .gitignore were unsupported by the detector and were not semantically indexed.

The session tools did not expose actual extraction token usage. Zero-valued local accounting counters were placeholders; the audit report records usage as unavailable.

## Refresh and contribute

Run `/graphify .` from the checkout you intend to describe, using an agent with the Graphify skill installed. Record that source commit, inspect inferred links and health warnings, and validate the exported JSON before replacing this snapshot. New or changed source is not automatically reflected here. Update the source-commit links in this README and GRAPH_REPORT.md together with graph.json's `built_at_commit`.

Only these five shareable files belong in Git. Caches, interpreter paths and extraction sidecars are ignored. The entire `graphify-out/` directory is excluded from plugin release ZIPs. Changes follow the repository's issue, separate branch and PR workflow; versions and release dates stay unchanged.
