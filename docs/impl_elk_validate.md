# Implementation plan: `elk_validate()`

**Status:** planned — not yet implemented  
**Estimated effort:** ~60–80 LOC, ~1 hour

---

## What it does

`elk_validate()` is a layout linter that runs post-ELK, pre-MATLAB emission.
It operates on data already in memory after `elk_layout_bottomup()` returns —
no MATLAB or `.slx` file required.

Returns `list[str]` with `WARNING:`-prefixed messages, same format as `sir_validate()`.

---

## Signature

```python
# elk_layout.py

def elk_validate(
    positions: dict[str, tuple[int, int, int, int]],
    edge_routing: dict[str, dict],
    chart_dict: dict,
    elk_options: dict | None = None,
) -> list[str]:
    """
    positions    — {dotted.path: (global_x, global_y, w, h)}
    edge_routing — {EDGE||src||dst||idx: {mid_x, mid_y, src_oclock, dst_oclock}}
    chart_dict   — original nested chart dict (states + transitions)
    elk_options  — same dict passed to sf_yaml_to_matlab()
    """
```

---

## Check 1 — Fan-in label overlap (~15 LOC)

**Trigger:** multiple transitions target the same state with `mid_y` values within ±20 px
of each other. Their labels will render on top of each other in Stateflow.

**Data needed:** `edge_routing` (mid_y per edge), edge_id parsed for destination.

**Algorithm:**
```python
from collections import defaultdict

by_dst: dict[str, list[tuple[int, str]]] = defaultdict(list)
for eid, routing in edge_routing.items():
    parts = eid.split('||')          # EDGE || src || dst || idx
    if len(parts) < 3: continue
    dst = parts[2]
    by_dst[dst].append((routing['mid_y'], eid))

for dst, entries in by_dst.items():
    if len(entries) < 2: continue
    entries.sort()
    for i in range(len(entries) - 1):
        y0, eid0 = entries[i]
        y1, eid1 = entries[i + 1]
        if abs(y1 - y0) < 20:
            issues.append(
                f"WARNING: layout: transitions {eid0} and {eid1} fan into '{dst}' "
                f"with overlapping label positions (mid_y diff = {y1 - y0} px)"
                f" → stagger MidPoint.y or use junction bus"
            )
```

---

## Check 2 — Undersized state box (~25 LOC)

**Trigger:** ELK-assigned box height is less than the estimated text height for the
state's action content (entry/during/exit actions).

**Data needed:** `positions` (ELK height), `chart_dict` (action text per state),
`_sf_label_height()` from `stateflow.py` (already lazy-imported in `elk_layout.py`
at line 875 for `_sf_state_size` — same pattern).

**New helper needed: `_get_state_body(chart_dict, dotted_path) -> dict | None`**

```python
def _get_state_body(chart_dict: dict, dotted_path: str) -> dict | None:
    """Walk nested chart_dict['states'] by dotted path. Returns state body or None."""
    parts = dotted_path.split('.')
    node = chart_dict
    for part in parts:
        states = node.get('states', {})
        if part not in states:
            return None
        node = states[part]
    return node
```

**Algorithm:**
```python
from slxgen.stateflow import _sf_label_height  # lazy import, same as _sf_state_size

for path, (x, y, w, h) in positions.items():
    body = _get_state_body(chart_dict, path)
    if body is None:
        continue
    estimated_h = _sf_label_height(body)
    if estimated_h > h:
        issues.append(
            f"WARNING: layout: state '{path}' action text may overflow box "
            f"(estimated {estimated_h} px, ELK assigned {h} px)"
            f" → add 'height: {estimated_h + 20}' override in YAML"
        )
```

---

## Check 3 — Transition label truncation (~10 LOC)

**Trigger:** a transition's label text (condition + action combined) exceeds the
`__max_label_width__` pixel cap, meaning ELK sized the edge using a short placeholder
instead of the actual label. The rendered label may extend beyond its visual boundary.

**Data needed:** `chart_dict['transitions']` (label text), `elk_options` (max width,
label_substitution flag).

**Algorithm:**
```python
max_label_w = int(elk_options.get('__max_label_width__', 150)) if elk_options else 150
label_sub   = str(elk_options.get('__label_substitution__', 'true')).lower() != 'false' \
              if elk_options else True
if not label_sub:
    return  # substitution off — ELK sized from actual labels, no truncation risk

PX_PER_CHAR = 7  # rough monospace estimate
for i, tr in enumerate(chart_dict.get('transitions', [])):
    parts = [tr.get('condition', ''), tr.get('action', '')]
    label = ' '.join(p for p in parts if p)
    if len(label) * PX_PER_CHAR > max_label_w:
        issues.append(
            f"WARNING: layout: transition[{i}] {tr.get('from')} → {tr.get('to')}: "
            f"label was truncated for ELK sizing (label_substitution active, "
            f"estimated {len(label) * PX_PER_CHAR} px > {max_label_w} px cap)"
            f" → label may extend beyond visual boundary"
        )
```

---

## Integration into pipeline

In `stateflow.py`, `sf_yaml_to_matlab()`, after `elk_layout_bottomup()` returns:

```python
# existing call:
positions, edge_routing, junctions = elk_layout_bottomup(chart_dict, ...)

# add (3 lines):
from slxgen.elk_layout import elk_validate
layout_issues = elk_validate(positions, edge_routing, chart_dict, elk_options)
for msg in layout_issues:
    print(f"[layout:{Path(yaml_path).name}] {msg}", file=sys.stderr)
```

No change to return value or callers.

---

## Files to modify

| File | Change |
|------|--------|
| `slxgen/elk_layout.py` | Add `_get_state_body()` helper + `elk_validate()` function |
| `slxgen/stateflow.py` | 3-line call after `elk_layout_bottomup()` in `sf_yaml_to_matlab()` |

No new files required. No changes to public API.

---

## Testing

Run `example/model_gen/gen_Ex1.py` after implementation — check that:
1. No spurious warnings on the existing example (regression baseline)
2. Manually introduce a fan-in scenario (two transitions to same state, same `order`)
   and confirm check 1 fires
3. Add a state with long action text that exceeds its box and confirm check 2 fires
