# Documentation

| Doc | What's in it |
|---|---|
| [architecture.md](architecture.md) | Design, the layered pipeline, module-by-module walkthrough, tools and why each was picked, key decisions with rationale. |
| [pipeline.md](pipeline.md) | The `pipeline` command and the `unified.build` combine step, end to end — the duplicate guard, the canonical schema, kept/refunds/rejections, exit codes. |
| [automation.md](automation.md) | The collector: the n8n Cloud workflow, the local Task Scheduler runner, setup steps, env vars, and the operator workflow. |
| [workflow.md](workflow.md) | Day-to-day developer and operator workflows: setup, testing, ingest, pipeline, collector, picking new fixtures, refreshing the golden snapshot, adding a new courier. |
| [status.md](status.md) | Snapshot of what's done, what's tested, what's untested against production, known gaps, and the roadmap. |
| [drift_handling.md](drift_handling.md) | The classes of drift, what each detection layer catches today, and the future LLM-triage strategy for residual cases. |
| [power_bi.md](power_bi.md) | Repointing the per-courier Power BI datasets from the master xlsx to the parquet substrate. |

The top-level [README.md](../README.md) has the quick-start commands and
the project layout.
