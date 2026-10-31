[graphify] MultiDiGraph edge-collapse diagnostic
input: <in-memory>
input_stage: provided JSON (normal graph.json is post-build)
effective_directed: <direct-call>
nodes: 1509
unverified_code_nodes: 0
raw_edges: 3909
valid_candidate_edges: 3525
missing_endpoint_edges: 0
dangling_endpoint_edges: 379
external_reference_edges: 5
self_loop_edges: 3
exact_duplicate_edges: 0
directed_unique_endpoint_pairs: 3439
directed_same_endpoint_collapsed_edges: 86
undirected_unique_endpoint_pairs: 3419
undirected_same_endpoint_collapsed_edges: 106
same_endpoint_group_count: 80
relation_variant_groups: 68
source_file_variant_groups: 0
source_location_variant_groups: 12
context_variant_groups: 0
post_build_graph_type: Graph
post_build_edges: 3802
producer_suppression_sites: 12
producer_suppression_examples:
  - L1830 seen_ids arity=unknown
  - L2394 seen_ids arity=unknown
  - L2396 seen_doc_refs arity=unknown
  - L3063 seen_ids arity=unknown
  - L3210 seen_ids arity=unknown
  - L4351 seen_keys arity=unknown
  - L4537 seen_keys arity=unknown
  - L8005 seen_ids arity=unknown
examples:
  - github_workflows_tests_unit_tests_steps -> github_workflows_tests_steps_name edges=4 relations=['contains'] locations=['L14', 'L18', 'L21', 'L23'] contexts=['']
  - github_workflows_tests_unit_tests_steps -> github_workflows_tests_steps_run edges=4 relations=['contains'] locations=['L15', 'L20', 'L22', 'L24'] contexts=['']
  - github_workflows_tests_qgis_tests_steps -> github_workflows_tests_steps_name edges=3 relations=['contains'] locations=['L40', 'L45', 'L47'] contexts=['']
  - github_workflows_tests_qgis_tests_steps -> github_workflows_tests_steps_run edges=3 relations=['contains'] locations=['L41', 'L46', 'L48'] contexts=['']
  - github_workflows_tests_bandit_steps -> github_workflows_tests_steps_uses edges=2 relations=['contains'] locations=['L54', 'L55'] contexts=['']
note: normal graph.json is post-build; raw producer loss must be measured earlier.