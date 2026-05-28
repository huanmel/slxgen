# ISS-009 / ISS-010 Analysis: Arc routing and label placement

These two issues are closely connected — ISS-009 (labels inside states) exists largely
because ISS-010 (arcs cutting through states) was not solved first. Fixing arc routing
would also make `_push_label_outside_states` mostly redundant.

---

## Visual problem (ISS-010 — arcs through states)

In `DevCtrl_StMach_DevCtrl_StMach_ACTIVE_STARTUP.png`, transitions like
INIT → FAULT_ACTIVE are drawn as vertical lines cutting through CONNECTING and READY:

```text
 ┌── INIT ──────────────────────────────┐
 └───────────────────────────────────────┘
           │ ← arc cuts through here
 ┌── CONNECTING ────────────────────────┐
 └───────────────────────────────────────┘
           │
 ┌── READY ─────────────────────────────┐
 │  ┌─ LINK_MON ─┐   ┌─ DATA_CTRL ─┐   │
 │  └────────────┘   └─────────────┘   │
 └───────────────────────────────────────┘
           │
 ┌── FAULT_ACTIVE ──────────────────────┐
```

---

## Root cause of ISS-010: we discard ELK's bendPoints

**ELK does solve this correctly.** It returns a routed path with `bendPoints` — a
left-side detour that avoids intermediate states. We throw that data away.

Code in `elk_layout.py` lines 465–475 (same pattern appears in three places):

```python
sec   = sections[0]
start = sec.get('startPoint', {'x': 0, 'y': 0})
end   = sec.get('endPoint',   {'x': 0, 'y': 0})
# Straight midpoint — ignores bendPoints entirely
mid_x = int((start['x'] + end['x']) / 2)
mid_y = int((start['y'] + end['y']) / 2)
```

For INIT → FAULT_ACTIVE, ELK actually returns something like:

```json
{
  "startPoint":  {"x": 200, "y": 150},
  "bendPoints":  [{"x": 40, "y": 150}, {"x": 40, "y": 850}],
  "endPoint":    {"x": 200, "y": 950}
}
```

We compute `mid_x = (200+200)/2 = 200`, `mid_y = (150+950)/2 = 550` — the straight-line
centre, which lands inside CONNECTING or READY.

The correct midpoint from the path is `(40, 550)` — well outside all intermediate
states. Setting Stateflow's `MidPoint` to `(40, 550)` would cause the arc to bend
left and avoid all intermediate boxes.

---

## ELK routing options that affect bendPoint count

See `notes/To control or eliminate unnecessary bend.md` for a full reference. Summary
of options relevant to our situation:

| Option | Value | Effect |
| ------ | ----- | ------ |
| `org.eclipse.elk.layered.unnecessaryBendpoints` | `false` (default) | Already active — ELK prunes straight-through bends. Bends we see are genuinely necessary for routing around nodes. |
| `org.eclipse.elk.edgeRouting` | `ORTHOGONAL` | Constrains each edge to exactly 3 segments (exit-sideways + vertical-run + enter-sideways) → always exactly 2 bendpoints per detour. Predictable but changes all arcs to right-angle staircases. |
| `org.eclipse.elk.edgeRouting` | `STRAIGHT` | Zero bendpoints — direct straight lines. Arcs cut through everything; no routing at all. |
| `org.eclipse.elk.layered.nodePlacement.bk.edgeStraightening` | `ALL` | Nudges node placement to reduce bends without changing routing style. May reduce multi-bend paths to single-bend. |

**ORTHOGONAL as an experiment:** switching to ORTHOGONAL would give exactly 2 bendpoints
for every detour arc, with a well-defined MidPoint (midpoint of the vertical segment
between bend0 and bend1). The cost is that every transition in every chart would become
a right-angle staircase arc rather than a smooth curve. Worth testing on a real chart
before committing.

**Current setting** (SPLINES): smooth curves, variable bendPoints. The `_path_midpoint`
fix (below) works correctly for SPLINES output and requires no global layout change.

---

## Why Stateflow junctions are NOT the right fix

Stateflow `Junction` objects are **semantic model elements**, not visual waypoints:

- They appear as visible dots in the chart.
- They participate in execution order evaluation.
- They change how Stateflow evaluates transition conditions.

Adding junctions purely for visual routing purposes would corrupt the model semantics.
The fault-bus junction pattern is appropriate only because those junctions represent a
real decision fan-in structure, not arbitrary routing detours.

---

## What Stateflow can express: one MidPoint per transition

Stateflow's `MidPoint [x y]` is a **single control point** — the arc is a smooth
Bezier-like curve from source attachment → MidPoint → destination attachment.

Mapping ELK's polyline path (which may have 0, 1 or 2+ bendPoints) to one MidPoint:

| ELK bendPoints | Best Stateflow MidPoint         | Quality                                                |
| -------------- | ------------------------------- | ------------------------------------------------------ |
| 0              | geometric midpoint of start→end | exact (straight edge)                                  |
| 1              | the single bendPoint            | near-exact: arc bends at that corner                   |
| 2              | midpoint of bend0→bend1 segment | approximate: captures the detour direction             |
| 3+             | midpoint of the full path       | rough: complex paths can't be one-control-point curves |

For the dominant case (INIT→FAULT_ACTIVE style long back-edges), ELK produces
exactly **1 bendPoint** for the side-detour. Using that point directly as `MidPoint`
faithfully reproduces the ELK intent without any junction objects.

---

## Experimental result: bendPoints fix implemented but no visible improvement

`_path_midpoint` was implemented in `elk_layout.py` (replacing the straight-line
midpoint at all three collection sites). Two runs were compared in MATLAB:
`DevCtrl_splines` (SPLINES, default) and `DevCtrl_ortho` (ORTHOGONAL).

**Finding: both charts look identical, ISS-010 arc routing unchanged.**

Root cause of the null result: for this chart's linear vertical column topology,
ELK places INIT, CONNECTING, READY, and FAULT_ACTIVE all in the same x-column.
The INIT→FAULT_ACTIVE long edge is a perfectly straight vertical line — ELK creates
dummy nodes in intermediate layers at the same x coordinate. `sections[0].bendPoints`
is **empty**. `_path_midpoint` falls back to `(start + end) / 2` — identical to
before.

ELK does not consider intermediate peer nodes as geometric obstacles for same-column
routing. In its abstract layered graph, CONNECTING and READY are in different layers
(not blocking the route); the visual intersection is a Stateflow rendering artefact.

**The bendPoints fix is still correct** — it will help for charts where ELK actually
produces bendPoints (non-column layouts, wide nodes forcing detours, complex crossing
avoidance). But it does not help for straight-column topologies.

**What actually fixes ISS-010 for straight-column layouts:** post-ELK detection in
`stateflow.py` — after positions are known, find transitions whose straight-line arc
intersects an intermediate peer state box, and apply a forced horizontal MidPoint
offset to push the arc to the side. ELK cannot do this automatically.

---

## Fix plan for ISS-010: post-ELK horizontal offset detection

After `elk_layout_bottomup` returns `positions` and `edge_routing`, scan transitions:

1. For each transition `src → dst`, compute the bounding box of the straight arc
   (min/max x and y between src_center and dst_center).
2. Find peer states (same LCA, not ancestor/descendant of src or dst) whose bounding
   boxes intersect that arc bounding box.
3. If any intersect: shift `er['mid_x']` left or right by enough to clear the widest
   intersecting state (`intersection_state_x - margin` or `+ state_w + margin`).

This is pure post-processing in `stateflow.py`, ~30 lines. It does not change ELK
configuration or the `_path_midpoint` implementation.

---

## Fix plan for ISS-010 (original): use ELK bendPoints to compute MidPoint

*(Implemented — no visual effect for straight-column topologies. Correct for complex
topologies. Keep the implementation.)*

This is the "sagitta/bulge" approach described in the ELK notes: instead of the
straight chord midpoint, use the point of maximum perpendicular offset from the chord —
which for a 1-bendPoint detour is exactly the bendPoint itself.

The implementation in `elk_layout.py`:

**Helper function** (new, `elk_layout.py`):

```python
def _path_midpoint(start: dict, bend_points: list, end: dict) -> tuple:
    """Return the point closest to the geometric midpoint of the routed path.

    0 bends → midpoint of start→end (unchanged behaviour).
    1 bend  → the bend point itself (best single control point for the arc).
    2 bends → midpoint of bend0→bend1 (captures the detour direction).
    3+ bends → midpoint of the full polyline (rough approximation).
    """
    pts = [start] + bend_points + [end]
    if len(bend_points) == 1:
        bp = bend_points[0]
        return int(bp['x']), int(bp['y'])
    # Compute cumulative arc length and return point at 50%
    segs = []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b['x'] - a['x'], b['y'] - a['y']
        segs.append(math.hypot(dx, dy))
    total = sum(segs)
    if total == 0:
        return int((start['x'] + end['x']) / 2), int((start['y'] + end['y']) / 2)
    target = total / 2
    acc = 0.0
    for (a, b), seg in zip(zip(pts, pts[1:]), segs):
        if acc + seg >= target:
            t = (target - acc) / seg if seg > 0 else 0
            return int(a['x'] + t * (b['x'] - a['x'])), int(a['y'] + t * (b['y'] - a['y']))
        acc += seg
    return int(end['x']), int(end['y'])
```

**Change in three places** (lines ~474, ~953, ~1044 in `elk_layout.py`) — replace:

```python
mid_x = int((start['x'] + end['x']) / 2)
mid_y = int((start['y'] + end['y']) / 2)
```

with:

```python
bend_points = sec.get('bendPoints', [])
mid_x, mid_y = _path_midpoint(start, bend_points, end)
```

**Effort: ~1 hour.** No changes to `stateflow.py`. No junction objects. No semantic
changes to any chart.

---

## Fix plan for ISS-009: scope push to LCA

Even after fixing arc routing, `_push_label_outside_states` is still needed for edges
whose arc genuinely passes through a sibling state. But its coordinate-scope bug must
be fixed regardless.

**Change:** pass `lca` into `_push_label_outside_states`; filter `positions` to only
states within the LCA subtree; use LCA bounding box bottom as `y_ceiling`.

```python
def _push_label_outside_states(mid_x, mid_y, positions, src_path='', dst_path='',
                                lca='', margin=15):
    ...
    # Restrict check to states within the LCA scope
    if lca:
        positions = {p: v for p, v in positions.items()
                     if p == lca or p.startswith(lca + '.')}
    # Ceiling: LCA bounding box bottom (not src/dst bottoms)
    if lca and lca in positions:
        _, lca_y, _, lca_h = positions[lca]
        y_ceiling = lca_y + lca_h + margin
    elif src_path in positions and dst_path in positions:
        ...  # existing fallback
```

**Effort: 2–3 hours.** One function change + call site.

---

## Recommended order

1. **ISS-010 MidPoint fix** (~1 h): use ELK bendPoints — minimal, low-risk, big visual gain.
2. **ISS-009 LCA scope** (~2–3 h): fix push function — correct label placement for
   within-subchart transitions.

After both: `_push_label_outside_states` may still help for the remaining cases where
a properly routed arc still passes near a sibling state (can reassess then).

---

## Verification

```powershell
& "C:\Users\ivanm\miniforge3\envs\py311_slxgen\python.exe" -c @'
from slxgen.pipeline import run_pipeline
run_pipeline("example/model_gen/DevCtrl_StMach_sf.yaml", run_matlab=False,
             adaptive_leaf_width=True)
run_pipeline("work/project_ID_HMI/hvac_state.yaml", run_matlab=False,
             adaptive_leaf_width=True)
'@
```

Charts to inspect:

- `DevCtrl_StMach_DevCtrl_StMach_ACTIVE_STARTUP.png` — arc routing through states (ISS-010)
- `DevCtrl_StMach_DevCtrl_StMach_ACTIVE_STANDBY.png` — within-subchart label push (ISS-009)
- `HVAC_State_HVAC_State.png` — regression check
