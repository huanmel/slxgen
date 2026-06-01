# slxgen — Known Issues

Issues are registered here with status tracking. For planned improvements see
[`docs/roadmap.md`](roadmap.md). For layout algorithm background see
[`docs/algorithms.md`](algorithms.md).

---

## Template

```markdown
## ISS-NNN: Title
**Status:** `open` | `mitigated` | `fixed`
**Severity:** `blocker` | `major` | `minor` | `cosmetic`
**Component:** file or layer affected
**Description:** What goes wrong.
**Reproduction:** Minimal YAML or steps to trigger.
**Root cause:** Why it happens.
**Workaround:** What you can do today.
**Fix:** What needs to change. Link to roadmap item if tracked.
```

---

## ISS-001: Default-transition dot overlaps the state above

**Status:** `fixed`  
**Severity:** `cosmetic`  
**Component:** `stateflow.py`, `elk_layout.py`

**Description:** The default-transition filled dot was rendered on top of the
bottom border of the state immediately above the default child. Additionally,
no `SourceEndPoint` or `MidPoint` was set, so Stateflow auto-placed the dot at
the canvas top-left and routed a long curved arrow to the destination.

**Root cause:** Dot was placed at `dot_y = y_destination - 20`, and ELK placed
siblings with a 20 px gap, so the state above ended exactly at `dot_y`. Without
`SourceEndPoint`/`MidPoint`, Stateflow used its own auto-routing heuristic.

**Fix applied:**

- `stateflow.py:_emit_sf_default_transition` now emits `SourceEndPoint` (20 px
  above destination top, centred horizontally) and `MidPoint` (midpoint of the
  straight vertical line), forcing a clean short arrow.
- `elk_layout.py`: `_NODE_SPACING = 30` (was 20) and `_DEFAULT_TRANS_OFFSET = 20`
  are now named constants — single place to tune chart appearance.
  Gap = 30 px, offset = 20 px → 10 px clearance above and below the dot.

**Note (Option B — deferred):** A more robust fix would compute the midpoint
of the actual gap from sibling positions, making the dot placement independent
of `_NODE_SPACING`. Documented in [`docs/layout_default_transition.md`](layout_default_transition.md).

---

## ISS-002: Default (init) state not placed first at root level

**Status:** `fixed`  
**Severity:** `minor`  
**Component:** `elk_layout.py` — `elk_layout_bottomup`, `sf_to_elk_json`

**Description:** The state with `default: true` at the chart root is not
guaranteed to render at the top. In the HVAC chart, OFF (default) renders at
the bottom while SEMI_OFF and ON are above it.

**Root cause (two parts):**

1. Non-subchart compound states were laid out inline in one monolithic ELK call.
   `elk.layered.layerConstraint = FIRST` on a compound node is consumed by its
   own internal layout pass under `SEPARATE_CHILDREN`, not by the parent layout.
2. `elk.layered.cycleBreaking.strategy = GREEDY_MODEL_ORDER` does not reliably
   place the init state first when it participates in cycles with other states.

**Fix applied:**

- `elk_layout_bottomup` extended to process ALL compound states bottom-up
  (not just subcharts). Each compound gets its own ELK pass; the resulting
  bounding box becomes a fixed-size leaf in the parent run. In the root pass,
  OFF is now a leaf — the FIRST constraint and cycle-breaking work correctly on
  leaf nodes.
- Cross-compound edge endpoints are promoted in `sf_to_elk_json`: if a
  transition source/target is inside a fixed-size compound, the endpoint is
  promoted to the compound boundary so ELK receives valid node references.
- Root-level cycle-breaking strategy changed from `GREEDY_MODEL_ORDER` to
  `DEPTH_FIRST`. With DEPTH_FIRST, the DFS starts from the init state (first in
  model order), and back-edges from descendants to the init state are reversed,
  making it a source in the layered DAG and placing it at the top.

---

## ISS-003: Long transition labels overflow the container box

**Status:** `open` (accepted limitation)  
**Severity:** `minor`  
**Component:** `stateflow.py` — transition label placement

**Description:** Stateflow center-anchors transition labels at `MidPoint`.
When a condition expression is long (e.g. 7 inputs ORed), the label extends
beyond the state bounding box in both directions. No truncation occurs in the
generated chart.

**Reproduction:** Any transition with a long `condition:` string, e.g.:

```yaml
condition: "blowerSpdReq > 0 || auto_press || defrost_press || dist_request
            || recirc_press || ac_press"
```

**Root cause:** Stateflow has no label width cap. The `MidPoint` determines
where the label anchor is, but does not clip the rendered text.

**Workaround:** Keep conditions short. For complex guards, factor into a named
predicate or local boolean computed in a `du:` action.

**Fix:** No fix possible at the generation layer without truncating labels.
The layout linter (`elk_validate()` — roadmap Priority 5) can detect and warn
about labels that exceed a width threshold. See [`docs/roadmap.md`](roadmap.md).

---

## ISS-004: Back-edge transition arcs cut through sibling states

**Status:** `open` (accepted limitation)  
**Severity:** `minor`  
**Component:** `elk_layout.py` — edge routing

**Description:** ELK routes edges through the shortest path between nodes,
which may cross through sibling state boxes. Back-edges (cycles returning to
an earlier state) are particularly affected. Human-drawn charts route these
arcs around the outside via side junctions.

**Reproduction:** Any chart with a cycle, e.g. ON → OFF → ON.

**Root cause:** ELK's SPLINES routing can cross node boxes when the edge
topology creates a short path through them. Generating explicit junction nodes
as waypoints would force the arc around the outside, but junctions are not
currently emitted for non-decision routing.

**Workaround:** Accept the layout and manually adjust in the Stateflow editor
after generation.

**Fix:** Detect back-edges (cycle-breaking edges in the ELK graph) and
generate a routing junction at the chart boundary. High effort — deferred.
See roadmap Priority 5 (layout engine improvements).

---

## ISS-005: Variables with no `type:` field silently accepted

**Status:** `open`  
**Severity:** `minor`  
**Component:** `stateflow_sir.py` — `yaml_to_sir()`

**Description:** A variable declared without a `type:` key (e.g.
`{name: recirc_request}`) is accepted by the parser without error or warning.
At MATLAB codegen time the variable cannot be typed and may produce an
incorrectly formed data declaration.

**Reproduction:**

```yaml
outputs:
  - {name: recirc_request}   # no type field
```

**Root cause:** `yaml_to_sir()` does not require `type:` on variables. The
YAML parser silently produces `None` for the missing key.

**Workaround:** Always specify `type:`. For enum types defined in an SLDD,
use the enum type name as a string (e.g. `type: Recirc_e`).

**Fix:** Add a validator check (WARNING or ERROR) for variables missing `type:`.
Update the schema summary in `docs/workflow.md` to mark `type:` as required.

---

## ISS-006: sfLintChart incorrectly reports MultipleDefaultTrans for non-subchart compounds

**Status:** `fixed`  
**Severity:** `blocker`  
**Component:** `slxgen/matlab/sfLintChart.m`, `slxgen/matlab/slx_lint.m`

**Description:** `sfLintChart` reported `MultipleDefaultTrans` on charts with a
compound state (e.g. OFF containing OFF_IDLE and OFF_WAKE_PENDING) even when
exactly one default transition existed at each level.

**Reproduction:** Any chart with a non-subchart compound state that has a default
child and an inner default transition. Example: HVAC chart, OFF compound.

**Root cause (two bugs):**

1. `sfLintChart.m` used `transition.Path` to determine which container a
   transition belongs to. In Stateflow, **all non-subchart transitions are stored
   at chart level** regardless of the `Stateflow.Transition(parentState)` argument,
   so every transition had `Path = "Model/Chart"`. The checker conflated the inner
   default (OFF → OFF_IDLE) with the chart-level default (chart → OFF), counting
   two chart-level defaults.
2. `buildContainers` checked `any(strcmp(allPaths, s.Path))` where `s.Path` is the
   *parent's* path, not the state's own path — compound states were mis-identified.

**Fix applied:**

- Complete rewrite of `sfLintChart.m`: containment now determined from
  `t.Destination.Path == containerFullPath(c)` (not `t.Path`). `buildContainers`
  uses `[s.Path '/' s.Name]` as the state's own full path. `containerFullPath(c)`
  helper distinguishes Chart vs State path semantics.
- `slx_lint.m` updated to report `[h.Path '/' h.Name]` for State handles so the
  JSON output includes the full path.

---

## ISS-007: State.Position must be chart-absolute for non-subchart compound children

**Status:** `fixed`  
**Severity:** `blocker`  
**Component:** `slxgen/stateflow.py` — `_sf_states_to_matlab_lines`

**Description:** States inside a non-subchart compound (e.g. OFF_IDLE inside OFF)
were generated with parent-relative coordinates. Stateflow placed them outside the
parent's bounding box, re-parenting them to the chart level and corrupting the
visual hierarchy.

**Reproduction:** HVAC chart — OFF_IDLE and OFF_WAKE_PENDING appeared at chart
level instead of inside OFF.

**Root cause:** `_sf_states_to_matlab_lines` subtracted the parent offset (`elif
path_prefix` branch) before emitting `State.Position`. In Stateflow, `Position` is
**chart-absolute** for all non-subchart states at every depth — only direct children
of a subchart use subchart-relative coordinates.

**Fix applied:** Removed the `elif path_prefix` branch. Only states that are direct
children of a subchart (where `_subchart_path` is set) use subchart-relative coords;
all others use raw chart-absolute positions from the ELK layout.

---

## ISS-008: YAML boolean synonyms silently corrupt state names and transition targets

**Status:** `open` (authoring hazard — no runtime detection)  
**Severity:** `major`  
**Component:** YAML authoring + `stateflow_sir.py` — `yaml_to_sir()`

**Description:** PyYAML (`yaml.safe_load`, YAML 1.1) treats certain unquoted
identifiers as boolean values rather than strings. State names or transition
`from:`/`to:` values that match these synonyms are silently converted to Python
`True` or `False`, which then fails state-path resolution at validation time with
a confusing error rather than a clear "state name is reserved" message.

Affected identifiers (any capitalisation):

| Group | Values |
| ----- | ------ |
| True synonyms | `true`, `True`, `TRUE`, `yes`, `Yes`, `YES`, `on`, `On`, `ON` |
| False synonyms | `false`, `False`, `FALSE`, `no`, `No`, `NO`, `off`, `Off`, `OFF` |

**Common Stateflow names that trigger this:** `ON`, `OFF`, `YES`, `NO`.

**Reproduction:**

```yaml
states:
  ON:     # ← PyYAML parses this key as Python True, not the string "ON"
    default: true
  OFF:    # ← parsed as False

transitions:
  - from: ON    # ← True (boolean), not "ON"
    to:   OFF   # ← False (boolean), not "OFF"
    order: '1'
```

The validator then reports `ERROR: transition source 'True' not found` —
confusing because `True` is not what was written.

**Workaround:** Always quote any state name or transition target that matches
a YAML 1.1 boolean synonym:

```yaml
states:
  'ON':             # single or double quotes force string
    default: true
  'OFF':
    default: false

transitions:
  - from: "ON"      # quoted → string
    to:   "OFF"
    order: '1'
```

Quoting the value in `from:`/`to:` is sufficient; quoting the state key in
`states:` is also required.

**Fix:** Add a pre-parse warning in `yaml_to_sir()` that detects state IDs or
transition endpoints that are Python `bool` values and reports them with a
clear message: `ERROR: state name parsed as boolean — quote it in the YAML`.

---

## ISS-009: Within-subchart transition labels overlap intermediate sibling states

**Status:** `open`
**Severity:** `major`
**Component:** `stateflow.py` — `_push_label_outside_states`, `LabelPosition` placement

**Description:** For transitions whose both endpoints are inside the same subchart
(e.g. STANDBY_INIT → FAULT_ACTIVE inside ACTIVE.STANDBY), the generated
`LabelPosition` places labels overlapping the destination state or intermediate
sibling states. In the Stateflow editor the labels appear inside or directly on top
of the FAULT_ACTIVE state box.

**Reproduction:** `example/model_gen/DevCtrl_StMach_sf.yaml` — ACTIVE.STANDBY
subchart contains states STANDBY_INIT, STANDBY_SYNC, STANDBY_IDLE, and FAULT_ACTIVE.
Transitions STANDBY_INIT → FAULT_ACTIVE, STANDBY_SYNC → FAULT_ACTIVE, and
STANDBY_IDLE → FAULT_ACTIVE all have labels that render inside FAULT_ACTIVE.
Screenshot: `example/model_gen/generated/DevCtrl_StMach_DevCtrl_StMach_ACTIVE_STANDBY.png`

**Root cause:** The `positions` dict stores all states at chart-absolute coordinates
(`parent_chart_abs + subchart_relative`). FAULT_ACTIVE has a large subchart-relative y,
so its chart-abs y is also large. `_push_label_outside_states` treats it as an
intermediate state to push through even though it is the transition destination.
The y_ceiling guard is ineffective because src/dst also have large chart-abs bottoms.

**Workaround:** Accept the generated layout and manually adjust labels in the
Stateflow editor after generation.

**Analysis:** See [`docs/iss009_label_placement.md`](iss009_label_placement.md) for
root cause walkthrough, coordinate examples, effort estimate, and recommended fix.

**Fix direction:** Scope `_push_label_outside_states` to only check states within the
LCA subtree. Pass `lca` into the function; filter `positions` to `lca + '.'` prefix;
use the LCA bounding box bottom as `y_ceiling`. See analysis doc — Option B (2–3 h).
Long-term: fixing arc routing (ISS-010) makes the push function nearly obsolete.

---

## ISS-011: Self-transition arc passes through state interior

**Status:** `open`
**Severity:** `cosmetic`
**Component:** `stateflow.py` — fallback self-loop routing

**Description:** When a state has a self-transition and ELK does not supply
routing for the self-edge (ELK typically ignores self-edges), the fallback
places the `MidPoint` only 40 px above the state's top edge with
`SourceOClock = 1` / `DestinationOClock = 11`.  Stateflow draws the arc as a
shallow curve that dips back into the state box rather than a clean loop
sitting entirely above the state.

The canonical Stateflow self-loop exits from the state's top, curves well above
it, and re-enters from the top — the arc midpoint should be roughly **one full
state-height above** the top edge so the loop is visibly external.

**Reproduction:** Any YAML with a self-transition, e.g.
`work/dp187_HMI/hvac_control_mode.yaml` — `CONTROL.AUTO` self-loop on `auto_bp`.

**Root cause:** ELK ignores self-edges and returns no routing for them.
The fallback in `stateflow.py` (`src_path == dst_path` branch) uses a fixed
40 px offset, which is too small relative to a typical leaf state height
(≥ 100 px) and does not account for the label height above the arc.

**Workaround:** Accept the layout.  Logic and connectivity are correct; only the
visual presentation is non-canonical.

**Fix direction:** In the fallback self-loop branch of `stateflow.py`, raise the
MidPoint to `sy - max(sh, 80)` (one full state height above the top edge).
Also nudge `SourceOClock` / `DestinationOClock` symmetrically outward
(e.g. 0.5 / 11.5) so the arc endpoints sit on the upper corners rather than
the upper face, giving the loop enough horizontal spread to clear the label.

---

## ISS-010: Cross-compound transition arcs cut through intermediate state boxes

**Status:** `open`
**Severity:** `major`
**Component:** `elk_layout.py` — edge routing, `stateflow.py` — arc emission

**Description:** Transitions whose source and destination are not direct siblings
(e.g. INIT → FAULT_ACTIVE where CONNECTING and READY lie between them) are drawn as
near-vertical lines that visually cut through the intermediate compound state boxes.

**Reproduction:** `example/model_gen/DevCtrl_StMach_sf.yaml` — ACTIVE.STARTUP
subchart; transitions from INIT/CONNECTING and states inside READY all go straight
down to FAULT_ACTIVE, cutting through the intermediate boxes.
Screenshot: `example/model_gen/generated/DevCtrl_StMach_DevCtrl_StMach_ACTIVE_STARTUP.png`

**Root cause:** ELK's layered (Sugiyama) algorithm assigns states to vertical layers
and routes edges through the spacing gaps between layers. It guarantees no
edge-edge crossings, but **does not guarantee that edges avoid the bounding boxes of
nodes in other layers**. INIT (layer 0) and FAULT_ACTIVE (layer 3) are both centred
at the same x column; ELK routes the arc straight down that column through the 50 px
gaps. In ELK's abstract model the arc never enters a node — it passes through empty
inter-layer space. In Stateflow's coordinate space, however, CONNECTING (layer 1) and
READY (layer 2) occupy the same x column, so the straight arc geometrically passes
through their bounding boxes and Stateflow renders it as cutting through them.

This is by design in ELK — the mismatch is between ELK's topological correctness
and Stateflow's geometric rendering. No ELK routing option prevents this for
straight-column topologies; the fix must be post-ELK.

**Workaround:** Accept the layout. The logic is correct; only the visual presentation
is affected.

**Analysis:** See [`docs/iss009_label_placement.md`](iss009_label_placement.md) for
the connection to ISS-009 and the generalised junction-routing fix plan (Option A).

**Fix direction:** Post-ELK detection in `stateflow.py`: after positions and
edge_routing are computed, scan each transition for geometric intersection between the
straight arc (src_center → dst_center) and intermediate peer state boxes. When an
intersection is found, shift `er['mid_x']` left or right far enough to clear the
intersecting state. This forces Stateflow to draw the arc as a side-detour without
adding any junction objects. ~30 lines, no ELK changes. See analysis doc.
