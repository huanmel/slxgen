# slxgen — Agent Instructions

See [CLAUDE.md](CLAUDE.md) for the full project guide.

## Quick orientation

`slxgen` generates MATLAB/Simulink Stateflow charts from YAML specifications.

**Before writing any YAML**, read the schema summary in `docs/workflow.md §1.2` —
it lists every valid field and includes copy-paste examples for all features.

**Feature-specific docs:**

| Feature | Where |
| ------- | ----- |
| Enum types | `docs/workflow.md §1.4` |
| Connective junctions | `docs/workflow.md §1.5` |
| Descriptions + requirement IDs | `docs/workflow.md §1.6` |
| Parameters / calibration data | `docs/workflow.md §1.7` |
| Design rules (naming, hierarchy) | `docs/stateflow_model_creation_guideline.md` |

**Working example:** `example/model_gen/fan_ctrl_sf.yaml` demonstrates params, enums,
desc/req fields, subchart, history junction, and connective junctions in one model.

**Private data:** `work/dp187_HMI/` is private project data — do not copy or reference it.
