# Local architecture graphs

Graphify is an optional contributor navigation aid. Generated graphs are local
artifacts: all of `graphify-out/` is ignored by Git and excluded from plugin
release ZIPs. Do not add graph links to the README or user guide.

## Refresh and contribute

1. Check out the source you intend to describe, preferably a clean `main`, and
   record `git rev-parse HEAD`. Record any uncommitted changes separately.
2. Run `/graphify .` from the repository root using an agent with the Graphify
   skill installed. Record the source commit, generation date and Graphify
   version alongside the local output; do not describe an older graph as current.
3. Inspect `GRAPH_REPORT.md` and any extraction health warnings. Check inferred
   links against their named source files. Validate that the exported JSON has
   unique node IDs and resolving link endpoints, and review outputs for private
   data and machine paths before sharing them.
4. Open `graphify-out/graph.html` locally. The HTML may load vis-network from
   unpkg, so check its external dependencies before sharing or offline use.

`EXTRACTED` relationships reflect static extraction or explicit documentation.
`INFERRED` links are hypotheses; `AMBIGUOUS` links require further review. Graph
relationships are not proof of runtime behavior or engineering correctness.
Record health limitations and unavailable token accounting honestly. Regenerate
when the source changes; the graph does not update automatically.

Keep generated HTML, JSON, reports, caches and sidecars out of commits. If a
shared copy is wanted, coordinate a GitHub Actions artifact built from a known
`main` commit or a wiki copy refreshed at release time. No shared publishing
workflow is configured here. Documentation changes follow the repository's
issue, feature branch and PR workflow; versions and release dates stay unchanged.
