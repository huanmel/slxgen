"""ELK-based layout for Stateflow charts.

Converts a chart_dict (sf.yaml structure) to ELK JSON, runs the ELK layout engine
via a Node.js subprocess, and converts the result back to Stateflow position/routing
dicts usable by stateflow_dict_to_matlab().
"""

import json
import math
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

_RUNNER = Path(__file__).parent / 'elk_runner.js'

# Assumed characters per line when estimating multi-line label height
_LABEL_CHARS_PER_LINE = 55
_LABEL_LINE_PX        = 22   # pixels per text line
_LABEL_MAX_WIDTH_PX   = 150  # cap — used only when label_substitution=False

_COMPOUND_HEADER_LINE_H = 16   # px per text line in compound state header (matches stateflow._SF_LABEL_LINE_H)
_COMPOUND_HEADER_MIN_H  = 30   # minimum top padding (state name only)
_DEFAULT_TRANSITION_PAD = 40   # extra top padding so default-transition dot fits inside container


def _label_size(text: str, max_width: int = _LABEL_MAX_WIDTH_PX) -> Tuple[int, int]:
    """Estimate (width_px, height_px) for a transition label string."""
    w = min(len(text) * 7, max_width)
    lines = math.ceil(len(text) / _LABEL_CHARS_PER_LINE)
    h = max(1, lines) * _LABEL_LINE_PX
    return w, h


_FAULT_KEYWORDS = ('FAULT', 'ERROR')
_INIT_KEYWORDS  = ('INIT',)


def _state_role(name: str, body: dict) -> str:
    """Infer layout role for a state from its name and body.

    Roles:
      'fault'  — error / exception states; placed in a dedicated right-column partition
      'init'   — default (entry) state; ELK FIRST layer constraint
      'normal' — everything else

    Explicit override: set ``role: fault|init|normal`` in the state body (sf.yaml).
    """
    explicit = body.get('role', '').lower()
    if explicit in ('fault', 'init', 'normal'):
        return explicit
    upper = name.upper()
    if any(kw in upper for kw in _FAULT_KEYWORDS):
        return 'fault'
    if body.get('default') or any(kw in upper for kw in _INIT_KEYWORDS):
        return 'init'
    return 'normal'


def _compound_header_h(body: dict) -> int:
    """Pixel height of the header strip for a compound (parent) state.

    Accounts for en/du/ex action text displayed in the header, not just the state name.
    """
    lines = 1  # state name
    for kw in ('en', 'du', 'ex'):
        text = body.get(kw, '').strip()
        if text:
            lines += 1                      # keyword line ("en:")
            lines += len(text.split('\n'))  # code lines
    return max(_COMPOUND_HEADER_MIN_H, lines * _COMPOUND_HEADER_LINE_H + 8)


def sf_to_elk_json(chart_dict: dict, layout_options: 'dict | None' = None,
                   max_label_width: int = _LABEL_MAX_WIDTH_PX,
                   label_substitution: bool = True,
                   direction: str = 'DOWN') -> dict:
    """Build an ELK graph JSON from a chart_dict (sf.yaml structure).

    chart_dict must have 'states' and optionally 'transitions' keys.
    Default transitions (no 'from') are skipped — handled by Stateflow default arrow logic.
    layout_options: optional dict of ELK option key→value pairs that override the defaults
                    for the root graph and all compound nodes.  Example:
                    {'elk.edgeRouting': 'ORTHOGONAL'}
    label_substitution: when True (default), edges carry no label dimensions — ELK routes
                    purely geometrically and does not bend arcs to accommodate label text width.
                    MidPoints are still computed from ELK sections; Stateflow renders the actual
                    label at that position.  Set False to pass real label sizes to ELK.
    direction: main layout direction — 'DOWN' (default, vertical chain) or 'RIGHT' (horizontal
                    chain, matches how human engineers typically draw flat state machines).
                    AND_STATE (parallel) compound nodes always use 'RIGHT' regardless.
                    Can be further overridden per-node via layout_options.
    """
    # Lazy imports to avoid circular dependency (stateflow.py imports elk_layout.py)
    from slxgen.stateflow import _sf_state_size, _lca_path, _find_sink_states, _direct_child_name  # noqa: PLC0415

    overrides = layout_options or {}
    states_dict = chart_dict.get('states', {})
    transitions = chart_dict.get('transitions', [])

    # Group non-default transitions by LCA path ('' = chart root)
    lca_edges: Dict[str, list] = {}
    for order, tr in enumerate(transitions):
        src = tr.get('from', '')
        dst = tr.get('to', '')
        if not src:
            continue  # default transition — skip
        lca = _lca_path(src, dst)
        edge_id = f'EDGE||{src}||{dst}||{order}'
        label_parts: List[str] = []
        if tr.get('trigger'):
            label_parts.append(tr['trigger'])
        if tr.get('condition'):
            label_parts.append(f"[{tr['condition']}]")
        if tr.get('action'):
            label_parts.append(f"{{{tr['action']}}}")
        label_text = ' '.join(label_parts)
        edge: dict = {
            'id': edge_id,
            'sources': [src],
            'targets': [dst],
        }
        if label_text and not label_substitution:
            lw, lh = _label_size(label_text, max_label_width)
            edge['labels'] = [{'text': label_text, 'width': lw, 'height': lh}]
        lca_edges.setdefault(lca, []).append(edge)

    def _compound_options(node_direction: str, body: dict) -> dict:
        top_pad = _compound_header_h(body)
        # States with only a name (no action labels) have a minimal header; add extra room
        # so the default-transition source dot has space above the first child state.
        if top_pad <= _COMPOUND_HEADER_MIN_H:
            top_pad += _DEFAULT_TRANSITION_PAD
        opts = {
            'elk.padding':                                  f'[top={top_pad},right=20,bottom=20,left=20]',
            'elk.direction':                                node_direction,
            'elk.spacing.nodeNode':                         '20',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '20',
            'elk.layered.layering.strategy':                'LONGEST_PATH',
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.edgeRouting':                              'SPLINES',
        }
        opts.update(overrides)
        return opts

    def _find_dominant_path_edges(children_dict: dict, path_prefix: str,
                                   sinks: set) -> set:
        """Return {(src_local, dst_local)} pairs on the dominant path.

        Dominant path = greedy walk from the default (init) state, always following
        the lowest-execution-order transition to a non-sink, non-visited sibling.
        These edges get priority=2 so ELK routes them straighter.
        """
        normal = set(children_dict.keys()) - sinks
        adj: Dict[str, list] = {n: [] for n in normal}
        for t in transitions:
            src = _direct_child_name(t.get('from', ''), path_prefix)
            dst = _direct_child_name(t.get('to', ''), path_prefix)
            if src in normal and dst in normal:
                order = int(t.get('order') or 99)
                adj[src].append((order, dst))
        for n in adj:
            adj[n].sort()

        start = next((n for n in children_dict if children_dict[n].get('default') and n not in sinks), None)
        if not start:
            start = next(iter(normal), None)
        if not start:
            return set()

        path_edges: set = set()
        visited = {start}
        current = start
        while True:
            nexts = [(o, d) for o, d in adj.get(current, []) if d not in visited]
            if not nexts:
                break
            _, nxt = nexts[0]
            path_edges.add((current, nxt))
            visited.add(nxt)
            current = nxt
        return path_edges

    def build_node(name: str, body: dict, path_prefix: str) -> dict:
        full_path = f'{path_prefix}.{name}' if path_prefix else name
        children_dict = body.get('states', {})
        node: dict = {'id': full_path}

        if not children_dict:
            w, h = _sf_state_size(body)
            node['width'] = w
            node['height'] = h
        else:
            state_type     = body.get('state_type', 'OR_STATE')
            node_dir       = 'RIGHT' if state_type == 'AND_STATE' else direction
            node['layoutOptions'] = _compound_options(node_dir, body)

            child_sinks = _find_sink_states(children_dict, transitions, full_path)
            init_name   = next((n for n in children_dict if children_dict[n].get('default')), None)
            dominant    = _find_dominant_path_edges(children_dict, full_path, child_sinks)

            child_roles = {cname: _state_role(cname, cbody) for cname, cbody in children_dict.items()}
            has_fault   = any(r == 'fault' for r in child_roles.values())
            if has_fault:
                node['layoutOptions']['elk.partitioning.activate'] = 'true'

            children_nodes = []
            for cname, cbody in children_dict.items():
                child_node = build_node(cname, cbody, full_path)
                lo = child_node.setdefault('layoutOptions', {})
                role = child_roles[cname]
                if role == 'fault':
                    lo['elk.partitioning.partition'] = '1'
                    lo['elk.layered.layerConstraint'] = 'LAST'
                else:
                    if has_fault:
                        lo['elk.partitioning.partition'] = '0'
                    if cname in child_sinks:
                        lo['elk.layered.layerConstraint'] = 'LAST'
                    elif cname == init_name:
                        lo['elk.layered.layerConstraint'] = 'FIRST'
                children_nodes.append(child_node)
            node['children'] = children_nodes

            if full_path in lca_edges:
                for edge in lca_edges[full_path]:
                    parts = edge['id'].split('||')
                    s = _direct_child_name(parts[1] if len(parts) > 1 else '', full_path)
                    d = _direct_child_name(parts[2] if len(parts) > 2 else '', full_path)
                    if (s, d) in dominant:
                        edge['priority'] = 2
                node['edges'] = lca_edges[full_path]
        return node

    root_children = [build_node(name, body, '') for name, body in states_dict.items()]

    root_opts = {
        'elk.algorithm':                                'layered',
        'elk.direction':                                direction,
        'elk.hierarchyHandling':                        'INCLUDE_CHILDREN',
        'elk.spacing.nodeNode':                         '20',
        'elk.layered.spacing.nodeNodeBetweenLayers':    '20',
        'elk.layered.layering.strategy':                'LONGEST_PATH',
        'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
        'elk.layered.nodePlacement.alignment':          'CENTER',
        'elk.edgeRouting':                              'SPLINES',
    }
    root_opts.update(overrides)

    return {
        'id': 'root',
        'layoutOptions': root_opts,
        'children': root_children,
        'edges': lca_edges.get('', []),
    }


def elk_layout(elk_json: dict) -> dict:
    """Run ELK layout via Node.js subprocess. Returns the positioned ELK graph."""
    result = subprocess.run(
        ['node', str(_RUNNER)],
        input=json.dumps(elk_json),
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _point_to_oclock(px: float, py: float,
                     nx: float, ny: float, nw: float, nh: float) -> float:
    """Convert a point on a node boundary to a Stateflow OClock float (0–12).

    0 = top-center, 3 = right-center, 6 = bottom-center, 9 = left-center, clockwise.
    The point is snapped to the nearest edge before arc-distance is computed.
    All coordinates are in the same (LCA-local) space.
    """
    if nw <= 0 or nh <= 0:
        return 3.0
    x, y = px - nx, py - ny
    P = 2.0 * (nw + nh)

    d_top, d_bottom = abs(y), abs(y - nh)
    d_right, d_left = abs(x - nw), abs(x)
    d_min = min(d_top, d_bottom, d_right, d_left)

    if d_min == d_top:           # top edge: clockwise right of center, counter-clockwise left
        x = max(0.0, min(nw, x))
        arc = (x - nw / 2) if x >= nw / 2 else P - (nw / 2 - x)
    elif d_min == d_right:       # right edge
        arc = nw / 2 + max(0.0, min(nh, y))
    elif d_min == d_bottom:      # bottom edge (right-to-left when clockwise)
        arc = nw / 2 + nh + (nw - max(0.0, min(nw, x)))
    else:                        # left edge (bottom-to-top when clockwise)
        arc = nw / 2 + nh + nw + (nh - max(0.0, min(nh, y)))

    return round((arc / P) * 12, 2)


def elk_to_stateflow_layout(elk_result: dict,
                             chart_dict: 'dict | None' = None,
                             fault_bus_junctions: bool = False,
                             skip_sink_placement: bool = True) -> Tuple[dict, dict, dict]:
    """Extract Stateflow layout from ELK result.

    chart_dict: the original chart dict (with 'states' key) used to resolve explicit
                role: annotations.  When provided, explicit ``role: fault`` in the YAML
                overrides keyword-based fault detection in _place_sink_states_right().

    Returns:
      positions    — {dotted.path: (global_x, global_y, w, h)}
      edge_routing — {edge_id: {mid_x, mid_y, src_oclock, dst_oclock}}
                     coordinates are already in LCA-relative space (Stateflow MidPoint ready).
    """
    positions: Dict[str, Tuple[int, int, int, int]] = {}

    def collect_positions(node: dict, offset_x: float, offset_y: float) -> None:
        node_id = node.get('id', '')
        x = offset_x + node.get('x', 0)
        y = offset_y + node.get('y', 0)
        w = node.get('width', 0)
        h = node.get('height', 0)
        if node_id and node_id != 'root':
            positions[node_id] = (int(x), int(y), int(w), int(h))
        for child in node.get('children', []):
            collect_positions(child, x, y)

    collect_positions(elk_result, 0, 0)

    edge_routing: dict = {}

    def collect_edges(node: dict, parent_global_x: float, parent_global_y: float) -> None:
        node_x = parent_global_x + node.get('x', 0)
        node_y = parent_global_y + node.get('y', 0)

        for edge in node.get('edges', []):
            edge_id = edge.get('id', '')
            if not edge_id.startswith('EDGE||'):
                continue
            sections = edge.get('sections', [])
            if not sections:
                continue
            sec   = sections[0]
            parts = edge_id.split('||')
            src_path = parts[1] if len(parts) > 1 else ''
            dst_path = parts[2] if len(parts) > 2 else ''

            start = sec.get('startPoint', {'x': 0, 'y': 0})
            end   = sec.get('endPoint',   {'x': 0, 'y': 0})

            # Straight midpoint (LCA-relative, Stateflow MidPoint-ready).
            mid_x = int((start['x'] + end['x']) / 2)
            mid_y = int((start['y'] + end['y']) / 2)

            # Precise float OClock derived from exact ELK boundary attachment point.
            src_oclock = 3.0
            dst_oclock = 9.0
            if src_path in positions:
                sx, sy, sw, sh = positions[src_path]
                src_oclock = _point_to_oclock(start['x'], start['y'],
                                              sx - node_x, sy - node_y, sw, sh)
            if dst_path in positions:
                dx, dy, dw, dh = positions[dst_path]
                dst_oclock = _point_to_oclock(end['x'], end['y'],
                                              dx - node_x, dy - node_y, dw, dh)

            edge_routing[edge_id] = {
                'mid_x':      mid_x,
                'mid_y':      mid_y,
                'src_oclock': src_oclock,
                'dst_oclock': dst_oclock,
            }

        for child in node.get('children', []):
            collect_edges(child, node_x, node_y)

    collect_edges(elk_result, 0, 0)

    # Build {full_dotted_path: role} from explicit YAML annotations, if available
    state_roles: Dict[str, str] = {}
    if chart_dict:
        def _collect_roles(states: dict, prefix: str) -> None:
            for name, body in states.items():
                path = f'{prefix}.{name}' if prefix else name
                state_roles[path] = _state_role(name, body)
                _collect_roles(body.get('states', {}), path)
        _collect_roles(chart_dict.get('states', {}), '')

    if not skip_sink_placement:
        _place_sink_states_right(positions, state_roles)
        _recompute_sink_edge_routing(edge_routing, positions, state_roles)

    fault_junctions = (
        _compute_fault_junctions(
            positions, state_roles,
            chart_dict.get('transitions', []) if chart_dict else [])
        if fault_bus_junctions else {}
    )

    return positions, edge_routing, fault_junctions


def _recompute_sink_edge_routing(edge_routing: dict, positions: dict,
                                  state_roles: dict) -> None:
    """Recompute edge routing for transitions into sink/fault states after repositioning.

    _place_sink_states_right() moves fault states to a right column *after* ELK runs,
    making ELK's original MidPoint and OClock stale.  This replaces them with a direct
    right→left horizontal route derived from the final (post-move) positions.
    """
    def _lca_of(a: str, b: str) -> str:
        a_parts = a.split('.') if a else []
        b_parts = b.split('.') if b else []
        common = []
        for p, q in zip(a_parts, b_parts):
            if p == q:
                common.append(p)
            else:
                break
        return '.'.join(common)

    for edge_id, er in edge_routing.items():
        parts = edge_id.split('||')
        if len(parts) < 3:
            continue
        src_path, dst_path = parts[1], parts[2]
        if state_roles.get(dst_path) != 'fault':
            continue
        if src_path not in positions or dst_path not in positions:
            continue
        sx, sy, sw, sh = positions[src_path]
        dx, dy, _, dh = positions[dst_path]
        lca = _lca_of(src_path, dst_path)
        lca_x, lca_y = positions.get(lca, (0, 0, 0, 0))[:2] if lca else (0, 0)

        er['src_oclock'] = 3.0
        er['dst_oclock'] = 9.0
        er['mid_x'] = int((sx + sw + dx) // 2 - lca_x)
        er['mid_y'] = int(((sy + sh // 2) + (dy + dh // 2)) // 2 - lca_y)


def _place_sink_states_right(positions: dict,
                               state_roles: 'Dict[str, str] | None' = None) -> None:
    """Move sink states (fault/error role) to a right-column zone within their compound parent.

    Operates in-place on the global positions dict.  For each compound node
    that has both normal and sink children:
      - normal children keep their positions
      - sink children are stacked vertically to the right of the normal bbox,
        centred on the normal children's vertical midpoint
      - the parent's width is expanded to fit

    elk.hierarchyHandling=INCLUDE_CHILDREN prevents ELK partitioning from
    working at the compound level, so we enforce the placement here.
    """
    _SINK_ZONE_GAP = 60  # horizontal gap between normal bbox and sink column

    # Build {parent_path: [child_path, ...]} from the dotted names
    parent_children: Dict[str, list] = {}
    for path in positions:
        if '.' in path:
            parent = path.rsplit('.', 1)[0]
            parent_children.setdefault(parent, []).append(path)

    for parent_path, child_paths in parent_children.items():
        if parent_path not in positions:
            continue

        def _is_fault(path: str) -> bool:
            if state_roles and path in state_roles:
                return state_roles[path] == 'fault'
            return any(kw in path.rsplit('.', 1)[-1].upper() for kw in _FAULT_KEYWORDS)

        fault_paths  = [p for p in child_paths if _is_fault(p)]
        normal_paths = [p for p in child_paths if p not in fault_paths]
        if not fault_paths or not normal_paths:
            continue

        px, py, pw, _ = positions[parent_path]

        # Bounding box of normal children (global coords)
        nr = max(positions[p][0] + positions[p][2] for p in normal_paths)  # right edge
        nt = min(positions[p][1]                    for p in normal_paths)  # top
        nb = max(positions[p][1] + positions[p][3]  for p in normal_paths)  # bottom

        fault_x = nr + _SINK_ZONE_GAP
        # Stack sink states vertically, centred on normal children
        fault_paths_sorted = sorted(fault_paths, key=lambda p: positions[p][1])
        total_h = sum(positions[p][3] for p in fault_paths_sorted)
        gaps_h  = 20 * (len(fault_paths_sorted) - 1)
        cy = (nt + nb) // 2
        fy = cy - (total_h + gaps_h) // 2
        fy = max(py + 20, fy)  # don't overlap compound header

        for fp in fault_paths_sorted:
            _, _, fw, fh = positions[fp]
            positions[fp] = (fault_x, fy, fw, fh)
            fy += fh + 20

        # Expand parent width; trim height to actual content bounding box.
        # ELK sized the height to fit FAULT_ACTIVE at the bottom; after moving
        # it right the lower portion is empty — trim it.
        fault_right   = fault_x + max(positions[p][2] for p in fault_paths)
        fault_bottom  = max(positions[p][1] + positions[p][3] for p in fault_paths)
        content_bottom = max(nb, fault_bottom)
        new_pw = max(pw, fault_right - px + 20)
        new_ph = content_bottom - py + 20   # trim to content; always >= actual content
        positions[parent_path] = (px, py, new_pw, new_ph)


_FAULT_SPINE_OFFSET = 25  # px left of fault state's left edge for junction spine


def _compute_fault_junctions(positions: dict, state_roles: dict,
                              transitions: list) -> dict:
    """Build a fault-bus junction descriptor for each fault state that has normal-state sources.

    Returns {fault_path: bus} where bus = {
        'parent':    str,           # immediate parent path of the fault state
        'spine_x':  int,            # global x of all junction nodes on the spine
        'entries':  list of {'src': str, 'jy': int},  # sorted ascending by jy
        'gateway_y': int or None,   # global y of gateway junction; None if an entry is close enough
    }

    The caller (stateflow.py) uses this to:
      - emit one Stateflow.Junction per entry + one optional gateway junction
      - chain junctions vertically (no label)
      - gateway → fault_state horizontally (DestinationOClock 9)
      - source → entry_junction (with condition label, SourceOClock 3) instead of source → fault
    """
    _GATEWAY_MERGE_TOL = 10  # px: if gateway_y is within this of an entry, reuse that entry

    fault_targets: Dict[str, list] = {}  # {fault_path: [src_path, ...]}
    for tr in transitions:
        src = tr.get('from', '')
        dst = tr.get('to', '')
        if not src or not dst:
            continue
        if state_roles.get(dst) == 'fault' and state_roles.get(src) != 'fault':
            if src in positions and dst in positions:
                fault_targets.setdefault(dst, []).append(src)

    result: dict = {}
    for fault_path, src_list in fault_targets.items():
        # Deduplicate sources while preserving order
        seen: set = set()
        unique_srcs = [s for s in src_list if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

        fx, fy, _, fh = positions[fault_path]
        spine_x  = fx - _FAULT_SPINE_OFFSET
        gateway_y = fy + fh // 2
        parent   = fault_path.rsplit('.', 1)[0] if '.' in fault_path else ''

        # Use the near edge of each source state rather than its center, so the entry
        # junction sits outside the source box rather than overlapping it.  We need the
        # approximate gateway y first to decide which edge is "near".
        # Keep _EDGE_GAP small: with nodeNodeBetweenLayers=20 the inter-state gap is only
        # 20 px, so 15 px would place junctions only 5 px (< radius=5) from the next state.
        _EDGE_GAP = 8  # px outside the source box so the junction clears the border
        def _entry_jy(src: str) -> int:
            _, sy, _, sh = positions[src]
            src_center_y = sy + sh // 2
            if src_center_y < gateway_y:
                return sy + sh + _EDGE_GAP   # source above gateway → just below bottom edge
            else:
                return sy - _EDGE_GAP        # source below gateway → just above top edge

        entries = sorted(
            [{'src': s, 'jy': _entry_jy(s)} for s in unique_srcs],
            key=lambda e: e['jy'],
        )

        # Only build a bus when there are 2+ sources — for a single source a direct
        # transition is cleaner and the junction nodes add no value.
        if len(entries) < 2:
            continue

        # If gateway_y is very close to an existing entry, reuse that entry as the gateway
        # (avoids a zero-length junction-to-junction connector).
        # gateway_entry_idx: index into entries[] of the entry that IS the gateway, or None.
        gateway_entry_idx: 'int | None' = None
        for i, e in enumerate(entries):
            if abs(e['jy'] - gateway_y) <= _GATEWAY_MERGE_TOL:
                gateway_entry_idx = i
                break

        result[fault_path] = {
            'parent':            parent,
            'spine_x':           spine_x,
            'entries':           entries,
            'gateway_y':         None if gateway_entry_idx is not None else gateway_y,
            'gateway_entry_idx': gateway_entry_idx,
        }

    return result
