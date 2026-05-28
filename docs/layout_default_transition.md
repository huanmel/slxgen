# Layout: Default-Transition Dot Placement

## Issue — chart-level default dot overlaps the adjacent state above

**Symptom (HVAC chart):** The chart-level default transition dot for OFF is rendered
on top of ON's bottom border (visible as the filled circle intersecting the ON box).

**Root cause:** The dot is placed at `y_destination - 20`, where `y_destination = 216`
(OFF's top edge).  That gives `dot_y = 196`.  ELK places sibling states with
`elk.spacing.nodeNode = 20`, so ON's bottom edge is exactly `y_ON + h_ON = 112 + 84 = 196`.
The fixed 20 px offset equals the node gap, landing the dot directly on the state above.

---

## Layout algorithm — step by step

### Step 1  Parse YAML → SIR dict  (`pipeline.py`, `stateflow_sir.py`)

```
hvac_state.yaml
    → yaml.safe_load()
    → sir_validate()       # checks required fields, path resolution
    → SIR dict             # { name, states:{…}, transitions:[…], inputs:[…], outputs:[…] }
```

- `states` is a nested dict: each key is a state name, value holds `en`/`du`/`ex` actions,
  child `states`, `default`, `type` (AND), `subchart`, `role` hints.
- `transitions` is a flat list with `from`, `to`, `condition`, `order`.

---

### Step 2  Estimate leaf state sizes  (`stateflow.py:_sf_state_size`)

For each **leaf** state (no children):

```
label_lines = 1 (name)
            + 1 (keyword "en:") + count(action lines)   for each of en/du/ex
height = _SF_HEADER_H  +  label_lines × _SF_LINE_H
width  = _SF_LEAF_W = 150 px   (fixed)
```

Constants (stateflow.py):
| Name | Value | Purpose |
| ---- | ----- | ------- |
| `_SF_LEAF_W` | 150 px | leaf node width |
| `_SF_HEADER_H` | 22 px | title-bar overhead |
| `_SF_LINE_H` | 16 px | pixels per label line |

These sizes feed ELK so it can accurately size parent (compound) states.

---

### Step 3  Build ELK graph JSON  (`elk_layout.py:sf_to_elk_json`)

`build_node(name, body, path_prefix)` recurses through the SIR tree:

**Leaf node:**
```json
{ "id": "OFF.OFF_IDLE", "width": 150, "height": 80 }
```

**Compound node:** ELK computes the size from children.  Padding is set via:
```python
top_pad = _compound_header_h(body)      # height of en/du/ex header text
if top_pad <= _COMPOUND_HEADER_MIN_H:   # 30 px
    top_pad += _DEFAULT_TRANSITION_PAD  # 40 px extra (room for default-dot)
elk.padding = [top=top_pad, right=20, bottom=20, left=20]
```

`_compound_header_h` counts the name line plus any `en:`/`du:`/`ex:` action lines,
each at 16 px/line, minimum 30 px.

**Edges:** only non-default transitions become ELK edges
(`EDGE||src_path||dst_path||idx`).  Default transitions have no source state
and are **not** modelled as edges — handled separately in Step 6.

Layer hints applied to child nodes:
- `elk.layered.layerConstraint = FIRST` → default (init) child placed first (top)
- `elk.layered.layerConstraint = LAST`  → sink/fault states placed last
- `elk.partitioning.partition = 1`      → sink states in right column

---

### Step 4  Run ELK  (`elk_layout.py:run_elk`)

Calls the ELK Layered algorithm via a Node.js subprocess (`elk_runner.js`).

Key ELK options at every level:
| Option | Value | Effect |
| ------ | ----- | ------ |
| `elk.algorithm` | `layered` | hierarchical layered layout |
| `elk.direction` | `DOWN` | vertical main axis |
| `elk.layered.nodePlacement.strategy` | `LINEAR_SEGMENTS` | stable column alignment |
| `elk.edgeRouting` | `SPLINES` | curved transition arrows |
| `elk.spacing.nodeNode` | `20` | 20 px gap between sibling states |
| `elk.layered.spacing.nodeNodeBetweenLayers` | `20` | 20 px gap between layers |

ELK output: each node gets `x`, `y`, `width`, `height` (all relative to its parent),
plus `sections[0].startPoint` / `endPoint` / `bendPoints` for each edge.

---

### Step 5  Extract chart-absolute positions  (`elk_layout.py:elk_to_stateflow_layout`)

```python
def collect_positions(node, offset_x, offset_y):
    x = offset_x + node['x']
    y = offset_y + node['y']
    positions[node['id']] = (int(x), int(y), int(w), int(h))
    for child in node.get('children', []):
        collect_positions(child, x, y)   # accumulate parent offset

collect_positions(elk_result, 0, 0)
```

Result: `positions` dict, key = SIR dotted path (`"OFF"`, `"OFF.OFF_IDLE"`),
value = `(x, y, w, h)` in **chart-absolute** pixels.

Subcharts run ELK separately (one pass per subchart, bottom-up), then convert
subchart-relative → chart-global by adding the subchart's chart-absolute offset.

Edge routing is also extracted: `mid_x`, `mid_y`, `src_oclock`, `dst_oclock`
from `sections[0].startPoint`/`endPoint` in the LCA-local coordinate space.

---

### Step 6  Emit MATLAB code  (`stateflow.py:_sf_states_to_matlab_lines`)

DFS walk of the state tree.  For each state:

1. `Stateflow.State(parent_var)` with `.Position = [x y w h]`
   - Non-subchart states at **any** depth: chart-absolute coords.
   - Direct children of a subchart: subchart-relative `[x-sc_x, y-sc_y, w, h]`.

2. If this state is the **default child**, emit a default transition:
   ```python
   dot_x = x + w // 2            # centre of destination state (chart-absolute)
   dot_y = max(y - 20, 0)        # 20 px above destination top  ← issue here
   mid_y = (dot_y + y) // 2
   t_N.SourceEndPoint = [dot_x, dot_y]
   t_N.MidPoint       = [dot_x, mid_y]   # forces straight vertical arrow
   t_N.DestinationOClock = 0              # enters at top-centre
   ```

3. Regular transitions: `MidPoint`, `SourceOClock`, `DestinationOClock` from ELK routing.
   Labeled transitions override `MidPoint.x = 10` to keep labels near the left margin.

---

---

## Issue 2 — Default (init) state not placed first at root level

**Symptom (HVAC):** OFF (`default: true`) renders at the bottom of the chart
(y = 216) while SEMI_OFF (y = 12) and ON (y = 112) are above it.

**Root cause:** `elk_layout.py:sf_to_elk_json` applies `elk.layered.layerConstraint = FIRST`
to the default child only when building a **compound** node (inside `build_node`,
lines ~234–248).  For root-level states the equivalent loop is never run:

```python
# line 261 — no FIRST/LAST constraints applied to these nodes:
root_children = [build_node(name, body, '') for name, body in states_dict.items()]
```

ELK's cycle-breaker (`GREEDY_MODEL_ORDER`) then sees ON→OFF and SEMI_OFF→OFF as
forward edges and places OFF as a target/sink at the bottom.

**Attempted fix:** after building `root_children`, apply the same FIRST/LAST constraint
logic used for compound children (this code is now in `elk_layout.py:sf_to_elk_json`,
lines ~261–285). However, the fix **does not change ELK's output** for compound root
nodes.

**Why the fix doesn't work:** With `elk.hierarchyHandling = SEPARATE_CHILDREN`, ELK
runs each compound node's internal layout independently and then treats the node as an
opaque box in the parent layout.  The `elk.layered.layerConstraint` property on a
compound node is consumed by the node's OWN internal layout pass, not by the parent
layout that determines the node's position.  Leaf nodes at root level work correctly;
compound nodes do not.

**Alternative approaches to investigate:**

- Add a virtual "priority" edge from a dummy source node to the init state, forcing
  ELK to treat it as a source regardless of cycle structure.
- Change `elk.layered.cycleBreaking.strategy` from `GREEDY_MODEL_ORDER` to
  `DEPTH_FIRST` or `INTERACTIVE` and measure impact on ordering.
- Assign the init state a fixed `y` position via `elk.position` after the first ELK
  pass and re-run only edge routing (two-pass layout).
- Sort root children by `default: true` first before building the ELK JSON so that
  GREEDY_MODEL_ORDER at least considers the init state as model-order 0 and reverses
  all back-edges pointing to it.

**Status:** open — needs experimentation with ELK options.

---

## Proposed solution

### Root cause (precise)

`dot_y = y - 20` uses the same value (20 px) as ELK's `elk.spacing.nodeNode`.
The state immediately above the default child ends exactly 20 px above it, so the
dot lands on its bottom edge.

### Option A — halve the offset (minimal change)

```python
dot_y = max(y - 10, 0)   # half of ELK node gap → dot stays inside the 20 px gap
mid_y = (dot_y + y) // 2
```

Result for HVAC:  dot at `(107, 206)` — centred in the 20 px gap between ON (bottom=196)
and OFF (top=216).

**Trade-off:** simple, but assumes ELK node spacing stays at 20 px.  If spacing changes
the dot would still be 10 px above the destination rather than centred in the gap.

### Option B — compute midpoint of the actual gap (robust)

Pass sibling positions to the emission point and find the state immediately above:

```python
# In _sf_states_to_matlab_lines, when building dst_coord:
siblings_y_bottoms = [
    pos[1] + pos[3]
    for sp, pos in positions.items()
    if sp != full_path
       and sp.rsplit('.', 1)[0] == path_prefix   # same parent
       and pos[1] + pos[3] <= dst_abs[1]         # above the default child
]
gap_top = max(siblings_y_bottoms) if siblings_y_bottoms else (
    positions[path_prefix][1] if path_prefix in positions else 0
)
dot_y_abs = (gap_top + dst_abs[1]) // 2
```

Then pass `dot_y_abs` directly instead of computing `y - 20`.

Result for HVAC:  dot at `(107, 206)` regardless of ELK spacing setting.

**Trade-off:** requires `positions` and `path_prefix` at the call site (both available).
Handles varying gaps (e.g. subchart children with different header sizes).

**Recommended:** Option B for long-term correctness; Option A as a quick patch.
