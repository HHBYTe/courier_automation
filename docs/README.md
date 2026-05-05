# Documentation

| Doc | What's in it |
|---|---|
| [architecture.md](architecture.md) | Design, the three-layer pipeline, module-by-module walkthrough, tools and why each was picked, key decisions with rationale. |
| [workflow.md](workflow.md) | Day-to-day developer and operator workflows: setup, testing, ingest, picking new fixtures, refreshing the golden snapshot, adding a new courier. |
| [status.md](status.md) | Snapshot of what's done, what's tested, what's untested against production, known gaps, and the roadmap. |
| [drift_handling.md](drift_handling.md) | The three classes of drift, what each detection layer catches today, and the future LLM-triage strategy for residual cases. |

## Other reference material in the project root

- [01_data_exploration.md](../01_data_exploration.md) — courier-by-courier survey of raw invoice formats and consolidation schemas. The source of truth for "what does Seur's xlsx look like".
- [02_step1_plan.md](../02_step1_plan.md) — the original Step 1 plan: scope, sequencing across 11 carriers, normalised schema sketch.
- [README.md](../README.md) — top-level project README with quick-start commands.
