# API reference

Generated from the source by `sphinx.ext.autodoc`, one page per module (115 modules).
Members without a docstring are listed too, with their signatures. Deprecated names are left
out; the changelog lists them.

```{toctree}
:caption: The package
:maxdepth: 1

jms_kapps
```

```{toctree}
:caption: Shared by both use cases
:maxdepth: 1

common/index
common/api_docs
common/prefixes
common/process
common/services
common/supervision
common/triplestore
```

```{toctree}
:caption: Platforms
:maxdepth: 1

common/platforms/index
common/platforms/posix
common/platforms/windows
```

```{toctree}
:caption: Use case 1
:maxdepth: 1

uc1/index
uc1/__main__
uc1/anomaly_detector
uc1/classify_call
uc1/decision_tree
uc1/effective_values
uc1/frame
uc1/learner
uc1/learner_dashboard
uc1/learner_memory
uc1/learner_process
uc1/page
uc1/planner
uc1/planner_dashboard
uc1/planner_memory
uc1/planner_process
uc1/planner_reads
uc1/replay
uc1/run_core
uc1/scope_log
uc1/screwing_resource
uc1/seed
uc1/timeseries
uc1/vocabulary
```

```{toctree}
:caption: Use case 2
:maxdepth: 1

uc2/index
uc2/__main__
uc2/discovery
uc2/handover_events
uc2/handshake
uc2/history
uc2/module
uc2/module_dashboard
uc2/module_drawing
uc2/module_memory
uc2/process
uc2/routing
uc2/run_core
uc2/seed
uc2/spawn_rate
uc2/strategies
uc2/topology
uc2/transport
uc2/validation_report
uc2/vertical_slice
uc2/vocabulary
uc2/waiting_order
uc2/wms
uc2/wms_dashboard
uc2/wms_memory
uc2/wms_process
```

```{toctree}
:caption: Use case 2: the Launcher
:maxdepth: 1

uc2/launcher/index
uc2/launcher/api
uc2/launcher/backend
uc2/launcher/boxes
uc2/launcher/passthrough
uc2/launcher/runner
```

```{toctree}
:caption: Use case 2: the monitor
:maxdepth: 1

uc2/monitor/index
uc2/monitor/api
uc2/monitor/handovers
uc2/monitor/memory
uc2/monitor/process
uc2/monitor/reads
uc2/monitor/wires
```

```{toctree}
:caption: The measurement harness
:maxdepth: 1

evaluation/index
evaluation/approval
evaluation/baseline
evaluation/bench
evaluation/client_load
evaluation/configuration
evaluation/grid
evaluation/harness
evaluation/heavy_parts
evaluation/instrumentation
evaluation/live_store
evaluation/matcher
evaluation/observation
evaluation/own_transports
evaluation/precision
evaluation/prefill
evaluation/ramp
evaluation/ramp_comparison
evaluation/record
evaluation/record_match
evaluation/replay
evaluation/replay_levels
evaluation/report
evaluation/report_charts
evaluation/report_points
evaluation/report_steps
evaluation/report_summary
evaluation/rounds
evaluation/run_limit
evaluation/run_uc2
evaluation/seed
evaluation/southward
evaluation/spec
evaluation/stats
evaluation/store
evaluation/store_requests
evaluation/summary
evaluation/sweep
evaluation/table
evaluation/watch
evaluation/wrapper
```
