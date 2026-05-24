# Layout Study & Project Roadmap

## 1. What slxgen does today

| Capability | Status |
|---|---|
| Parse SLX → YAML | Working |
| YAML → MATLAB .m (chart recreation) | Working with ELK layout |
| Round-trip fidelity | Good for hierarchy and transitions; layout differs from original |
| Generate from spec (YAML written by hand / LLM) | Working but untested in practice |

The intended workflow going forward:

```
Requirements
    ↓ (LLM / manual)
YAML spec (states, transitions, actions)
    ↓  slxgen
MATLAB .m script
    ↓  run in MATLAB
Stateflow chart
    ↓  manual layout polish if needed
Final model
```

---

## 2. Stateflow layout — patterns from example study

### 2.1 Chart types available in Stateflow

Stateflow offers two block types:

| Type | Best for | Limitations |
|---|---|---|
| **State Transition Chart** | Hierarchical, parallel, junction-heavy machines | Layout is manual or auto (messy for complex charts) |
| **State Transition Table (STT)** | Simple flat machines (linear chains, few states) | No hierarchy, no AND states, no junctions |

The STT format (row = state, column = transition condition) is very readable for simple FSMs — similar to a truth table. A converted STT → Chart shows the equivalent chart structure: a vertical chain with side-routing junctions for back-edges.

**Implication for slxgen**: For flat machines with ≤ ~8 states and simple transitions, the STT format could be offered as an alternative output. For hierarchical machines it is not an option.

### 2.2 Layout conventions in human-drawn charts

From studying examples (`stateflow_examples/`):

| Observation | Pattern |
|---|---|
| AND (parallel) states | Always **horizontal** (side by side) |
| Sequential OR states | Usually **vertical** (top to bottom), main flow as a straight chain |
| Fault / Error states | Visually isolated — bottom-right or right column, rarely in the main chain |
| Off/disabled state | Often a full-width bar at the **top**, before the main compound below |
| Back-edge transitions (cycles) | Large arcs routed around the **outside** using side junctions, not cutting through other states |
| Hierarchy depth | Rarely more than 3 levels |
| Transition labels | Short; long conditions are typically split or use named events |
| Junctions | Used heavily as routing aids for shared entry/exit points |

### 2.3 What ELK layout handles well today

- States do not overlap
- Transitions stay inside their container (LCA) boundaries
- Fault states (name contains FAULT/ERROR, or explicit `role: fault` in YAML) are repositioned to the right of the normal state column
- AND_STATE compounds always lay out horizontally (RIGHT direction)
- Dominant path edges get higher ELK priority (straighter routing)
- Label stagger: transitions sharing the same visual lane are nudged vertically to avoid y-overlap

### 2.4 Known limitations (accepted for now)

| Issue | Root cause | Mitigation |
|---|---|---|
| Long transition labels overflow container | Stateflow center-anchors labels at MidPoint — no fix possible | Use short conditions; consider label substitution |
| Back-edge arcs cut through states | ELK routes edges, not around them via side junctions | Would require generating explicit junction nodes |
| Cycle-breaking transitions look asymmetric | ELK doesn't know Stateflow's convention | Could detect back-edges and force top/bottom OClocks |
| ELK partitioning ignored | `INCLUDE_CHILDREN` flattens hierarchy, ignores compound-node options | Post-ELK repositioning used as workaround |

---

## 3. Role system

States can be assigned a layout role explicitly in the YAML or inferred automatically.

### 3.1 Automatic detection

| Rule | Role assigned |
|---|---|
| Name contains `FAULT` or `ERROR` | `fault` |
| Name contains `INIT` or `default: true` | `init` |
| Everything else | `normal` |

### 3.2 Explicit override in YAML

```yaml
states:
  COMM_DEGRADED:
    role: fault      # repositioned to right column even without FAULT in name
    en: isFault=true;
  STARTUP:
    role: init       # gets FIRST layer constraint in ELK
    en: initialize();
```

### 3.3 Effect of each role

| Role | ELK layer constraint | Post-ELK repositioning |
|---|---|---|
| `fault` | `LAST` (bottom of layer stack) | Moved to right of normal bbox, vertically centred |
| `init` | `FIRST` (top of layer stack) | None |
| `normal` | None | None |

### 3.4 Direction parameter

The main layout axis can be set per chart:

```python
# Vertical chain (default) — works well for hierarchical machines
stateflow_dict_to_matlab(chart)

# Horizontal chain — matches human convention for many flat machines
stateflow_dict_to_matlab(chart, elk_options={'__direction__': 'RIGHT'})
```

---

## 4. Roadmap

### Priority 1 — Use it in practice (immediate)

Apply slxgen on a real requirements → model workflow:
- Take a set of requirements
- Ask LLM to generate YAML spec
- Run `stateflow_dict_to_matlab()` → open in MATLAB
- Identify what breaks, what's missing, what's confusing
- Iterate

This will surface the most impactful improvements.

### Priority 2 — YAML spec coverage (when gaps found in Priority 1)

Likely missing features:
- Junctions (decision nodes for shared transition routing)
- History states (`H`)
- Inner transitions
- `during` (du) action in transitions
- Named events / messages

### Priority 3 — Layout improvements (low priority, high effort)

Only worth doing if they block practical use:
- Back-edge arcs via explicit junction nodes (would fix cycle-cutting-through-states)
- State Transition Table output for flat FSMs
- Better label placement (requires upstream shortening)

### What NOT to do

- Spend more time tuning ELK parameter combinations — current quality is "good enough to use"
- Pixel-perfect recreation of human-drawn layouts — impossible without full semantic understanding
- Auto-detect all layout patterns from topology alone — diminishing returns past what's already done

---

## 5. Stateflow examples reference

| File | Key pattern illustrated |
|---|---|
| `ConvertSTTToChartExample_01.png` | State Transition Table (tabular format) |
| `ConvertSTTToChartExample_02.png` | Same FSM as chart — vertical chain with side junctions for back-edges |
| `FaultDetectionControlLogicInAnAircraftControlSystemExample_02.png` | AND state with 4 parallel subcharts, each with Passive→Active→Standby→Off→Isolated |
| `ModelingAFitnessWatcherExample_02.png` | AND state: 4 horizontal columns, each with own vertical flow |
| `battery-mgmt-variant.png` | FaultDetected at bottom-right; junctions used for complex routing |
| `ModelingAPowerWindowControllerExample_03.png` | Stop/Move top-level; EmergencyDown center-detached; Mode sub-compound with Initializing→Manual/Auto |
| `HierarchyGetStartedExample_02.png` | Off (full-width bar at top) → On (compound below with Stream and Radio side by side) |
| `refactor-charts-2.png` | Awake → Safe/Danger/Asleep with nested Searching/Hunting compounds |
