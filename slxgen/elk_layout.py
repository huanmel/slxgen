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


_SINK_KEYWORDS       = ('FAULT', 'ERROR')     # keyword-based auto-detection for sink role
_SINK_ROLE_ALIASES: frozenset = frozenset({'sink', 'fault', 'error'})  # accepted role: values
_INIT_KEYWORDS       = ('INIT',)


def _state_role(name: str, body: dict,
                path: str = '',
                auto_sinks: 'frozenset[str]' = frozenset()) -> str:
    """Infer layout role for a state from its name, body, and optional auto-sink set.

    Canonical roles returned:
      'sink'   — exception / collector states; right-column partition + LAST layer
      'init'   — default (entry) state; FIRST layer constraint
      'normal' — everything else

    Explicit YAML ``role:`` accepts: 'sink' (canonical) or legacy aliases 'fault'/'error'.
    Keyword detection on the state name ('FAULT', 'ERROR') is a fallback heuristic.
    auto_sinks: set of fully-qualified dotted paths pre-computed by topological analysis;
                any path in this set is treated as a sink regardless of name or annotation.
    """
    explicit = body.get('role', '').lower()
    if explicit in _SINK_ROLE_ALIASES:
        return 'sink'
    if explicit in ('init', 'normal'):
        return explicit
    if path and path in auto_sinks:
        return 'sink'
    upper = name.upper()
    if any(kw in upper for kw in _SINK_KEYWORDS):
        return 'sink'
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
                   direction: str = 'DOWN',
                   auto_sinks: 'frozenset[str]' = frozenset(),
                   fixed_sizes: 'dict | None' = None) -> dict:
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
    _fixed = fixed_sizes or {}
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
        # _orig_idx allows per-subchart runs to preserve original YAML transition indices
        edge_id = f"EDGE||{src}||{dst}||{tr.get('_orig_idx', order)}"
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
            'elk.layered.cycleBreaking.strategy':           'GREEDY_MODEL_ORDER',
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

        if full_path in _fixed:
            # Pre-computed fixed size from a per-subchart ELK run — treat as a leaf.
            node['width'], node['height'] = _fixed[full_path]
            return node

        if not children_dict:
            w, h = _sf_state_size(body)
            node['width'] = w
            node['height'] = h
        else:
            state_type     = body.get('state_type', 'OR_STATE')
            is_and         = state_type == 'AND_STATE' or body.get('type') == 'AND'
            node_dir       = 'RIGHT' if is_and else direction
            node['layoutOptions'] = _compound_options(node_dir, body)

            child_sinks = _find_sink_states(children_dict, transitions, full_path)
            init_name   = next((n for n in children_dict if children_dict[n].get('default')), None)
            dominant    = _find_dominant_path_edges(children_dict, full_path, child_sinks)

            child_roles = {
                cname: _state_role(cname, cbody,
                                   path=f'{full_path}.{cname}' if full_path else cname,
                                   auto_sinks=auto_sinks)
                for cname, cbody in children_dict.items()
            }
            has_sink = any(r == 'sink' for r in child_roles.values())
            if has_sink:
                node['layoutOptions']['elk.partitioning.activate'] = 'true'

            children_nodes = []
            for cname, cbody in children_dict.items():
                child_node = build_node(cname, cbody, full_path)
                lo = child_node.setdefault('layoutOptions', {})
                role = child_roles[cname]
                if role == 'sink':
                    lo['elk.partitioning.partition'] = '1'
                    lo['elk.layered.layerConstraint'] = 'LAST'
                else:
                    if has_sink:
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

    # Apply FIRST/LAST layer constraints to root-level states — the same logic used
    # for compound children inside build_node, which was missing at the root level.
    root_init      = next((n for n in states_dict if states_dict[n].get('default')), None)
    root_auto_sinks = _find_sink_states(states_dict, transitions, '')
    root_roles = {
        name: _state_role(name, body, path=name, auto_sinks=auto_sinks)
        for name, body in states_dict.items()
    }
    has_root_sink = any(r == 'sink' for r in root_roles.values())
    for child_node in root_children:
        cname = child_node['id']
        lo = child_node.setdefault('layoutOptions', {})
        role = root_roles[cname]
        if role == 'sink':
            lo['elk.partitioning.partition'] = '1'
            lo['elk.layered.layerConstraint'] = 'LAST'
        else:
            if has_root_sink:
                lo['elk.partitioning.partition'] = '0'
            if cname in root_auto_sinks:
                lo['elk.layered.layerConstraint'] = 'LAST'
            elif cname == root_init:
                lo['elk.layered.layerConstraint'] = 'FIRST'

    root_opts = {
        'elk.algorithm':                                'layered',
        'elk.direction':                                direction,
        'elk.hierarchyHandling':                        'SEPARATE_CHILDREN',
        'elk.spacing.nodeNode':                         '20',
        'elk.layered.spacing.nodeNodeBetweenLayers':    '20',
        'elk.layered.layering.strategy':                'LONGEST_PATH',
        'elk.layered.cycleBreaking.strategy':           'GREEDY_MODEL_ORDER',
        'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
        'elk.layered.nodePlacement.alignment':          'CENTER',
        'elk.edgeRouting':                              'SPLINES',
    }
    root_opts.update(overrides)
    if has_root_sink:
        root_opts['elk.partitioning.activate'] = 'true'

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
                             sink_bus_junctions: bool = False,
                             sink_placement: str = 'none',
                             auto_sinks: 'frozenset[str]' = frozenset()) -> Tuple[dict, dict, dict]:
    """Extract Stateflow layout from ELK result.

    chart_dict: the original chart dict (with 'states' key) used to resolve explicit
                role: annotations.  When provided, explicit role annotations override
                keyword-based sink detection in _place_sink_states().
    sink_placement: where to reposition sink states after ELK runs.
                    'none' (default) — no repositioning, use pure ELK output.
                    'right'|'left'|'top'|'bottom' — move sinks to that side of normal children.
                    'auto' — pick right/bottom based on normal-children aspect ratio.
    auto_sinks: optional set of fully-qualified dotted paths to treat as sink states,
                computed from topological analysis (see _compute_auto_sinks in stateflow.py).

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
                state_roles[path] = _state_role(name, body, path=path, auto_sinks=auto_sinks)
                _collect_roles(body.get('states', {}), path)
        _collect_roles(chart_dict.get('states', {}), '')

    if sink_placement != 'none':
        _place_sink_states(positions, state_roles, sink_placement)
        _recompute_sink_edge_routing(edge_routing, positions, state_roles, sink_placement)

    sink_junctions = (
        _compute_sink_junctions(
            positions, state_roles,
            chart_dict.get('transitions', []) if chart_dict else [])
        if sink_bus_junctions else {}
    )

    return positions, edge_routing, sink_junctions


_SINK_PLACEMENT_OCLOCKS = {
    'right':  (3.0, 9.0),   # src exits right, dst enters left
    'left':   (9.0, 3.0),   # src exits left,  dst enters right
    'bottom': (6.0, 12.0),  # src exits bottom, dst enters top
    'top':    (12.0, 6.0),  # src exits top,    dst enters bottom
}


def _recompute_sink_edge_routing(edge_routing: dict, positions: dict,
                                  state_roles: dict,
                                  placement: str = 'right') -> None:
    """Recompute edge routing for transitions into sink states after repositioning.

    _place_sink_states() moves sink states *after* ELK runs, making ELK's original
    MidPoint and OClock stale.  This replaces them with a straight route derived from
    the final (post-move) positions and the chosen placement direction.
    """
    # Resolve 'auto' to a concrete direction using the same heuristic as _place_sink_states.
    # We use the first sink state's position relative to its siblings as a proxy.
    eff_placement = placement
    if placement == 'auto':
        eff_placement = 'right'  # fallback; auto was already resolved in _place_sink_states

    src_oc, dst_oc = _SINK_PLACEMENT_OCLOCKS.get(eff_placement, (3.0, 9.0))

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
        if state_roles.get(dst_path) != 'sink':
            continue
        if src_path not in positions or dst_path not in positions:
            continue
        sx, sy, sw, sh = positions[src_path]
        dx, dy, dw, dh = positions[dst_path]
        lca = _lca_of(src_path, dst_path)
        lca_x, lca_y = positions.get(lca, (0, 0, 0, 0))[:2] if lca else (0, 0)

        er['src_oclock'] = src_oc
        er['dst_oclock'] = dst_oc
        if eff_placement in ('right', 'left'):
            er['mid_x'] = int((sx + sw + dx) // 2 - lca_x)
            er['mid_y'] = int(((sy + sh // 2) + (dy + dh // 2)) // 2 - lca_y)
        else:  # top / bottom
            er['mid_x'] = int(((sx + sw // 2) + (dx + dw // 2)) // 2 - lca_x)
            er['mid_y'] = int((sy + sh + dy) // 2 - lca_y)


def _place_sink_states(positions: dict,
                        state_roles: 'Dict[str, str] | None' = None,
                        placement: str = 'right') -> None:
    """Reposition sink states within their compound parent according to placement direction.

    Operates in-place on the global positions dict.  For each compound node
    that has both normal and sink children, sinks are moved out of the ELK-computed
    position and placed in a dedicated zone:

      right  — column to the right of normal bbox, sinks stacked vertically
      left   — column to the left of normal bbox, sinks stacked vertically
      bottom — row below normal bbox, sinks stacked horizontally
      top    — row above normal bbox, sinks stacked horizontally
      auto   — 'right' when normal bbox is taller than wide, else 'bottom'

    elk.hierarchyHandling=INCLUDE_CHILDREN prevents ELK partitioning from working
    at the compound level, so placement is enforced here instead.
    """
    _GAP = 60  # px gap between normal bbox and sink zone

    # Build {parent_path: [child_path, ...]} from the dotted names
    parent_children: Dict[str, list] = {}
    for path in positions:
        if '.' in path:
            parent = path.rsplit('.', 1)[0]
            parent_children.setdefault(parent, []).append(path)

    for parent_path, child_paths in parent_children.items():
        if parent_path not in positions:
            continue

        def _is_sink(path: str) -> bool:
            if state_roles and path in state_roles:
                return state_roles[path] == 'sink'
            return any(kw in path.rsplit('.', 1)[-1].upper() for kw in _SINK_KEYWORDS)

        sink_paths   = [p for p in child_paths if _is_sink(p)]
        normal_paths = [p for p in child_paths if p not in sink_paths]
        if not sink_paths or not normal_paths:
            continue

        px, py, pw, ph = positions[parent_path]

        # Bounding box of normal children (global coords)
        nl = min(positions[p][0]                    for p in normal_paths)  # left
        nr = max(positions[p][0] + positions[p][2]  for p in normal_paths)  # right
        nt = min(positions[p][1]                    for p in normal_paths)  # top
        nb = max(positions[p][1] + positions[p][3]  for p in normal_paths)  # bottom

        # Resolve 'auto' before branching
        eff_placement = placement
        if placement == 'auto':
            eff_placement = 'right' if (nb - nt) >= (nr - nl) else 'bottom'

        if eff_placement in ('right', 'left'):
            # Stack sinks vertically, centred on normal children's vertical midpoint
            sinks_sorted = sorted(sink_paths, key=lambda p: positions[p][1])
            total_h = sum(positions[p][3] for p in sinks_sorted)
            gaps_h  = 20 * (len(sinks_sorted) - 1)
            cy = (nt + nb) // 2
            sy = cy - (total_h + gaps_h) // 2
            sy = max(py + 20, sy)  # don't overlap compound header

            if eff_placement == 'right':
                sx = nr + _GAP
            else:
                max_sw = max(positions[p][2] for p in sinks_sorted)
                sx = nl - _GAP - max_sw

            for sp in sinks_sorted:
                _, _, sw, sh = positions[sp]
                positions[sp] = (sx, sy, sw, sh)
                sy += sh + 20

            # Resize parent to fit content
            sink_r = sx + max(positions[p][2] for p in sink_paths)
            sink_b = max(positions[p][1] + positions[p][3] for p in sink_paths)
            new_pw = max(pw, sink_r - px + 20)
            new_ph = max(nb, sink_b) - py + 20
            positions[parent_path] = (px, py, new_pw, new_ph)

        else:  # 'bottom' or 'top'
            # Stack sinks horizontally, centred on normal children's horizontal midpoint
            sinks_sorted = sorted(sink_paths, key=lambda p: positions[p][0])
            total_w = sum(positions[p][2] for p in sinks_sorted)
            gaps_w  = 20 * (len(sinks_sorted) - 1)
            cx = (nl + nr) // 2
            sx = cx - (total_w + gaps_w) // 2
            sx = max(px + 20, sx)

            if eff_placement == 'bottom':
                sy = nb + _GAP
            else:
                max_sh = max(positions[p][3] for p in sinks_sorted)
                sy = nt - _GAP - max_sh

            for sp in sinks_sorted:
                _, _, sw, sh = positions[sp]
                positions[sp] = (sx, sy, sw, sh)
                sx += sw + 20

            # Resize parent to fit content
            sink_r = max(positions[p][0] + positions[p][2] for p in sink_paths)
            sink_b = max(positions[p][1] + positions[p][3] for p in sink_paths)
            new_pw = max(nr, sink_r) - px + 20
            new_ph = max(ph, sink_b - py + 20)
            positions[parent_path] = (px, py, new_pw, new_ph)


_SINK_SPINE_OFFSET = 25  # px left of fault state's left edge for junction spine


def _compute_sink_junctions(positions: dict, state_roles: dict,
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
        if state_roles.get(dst) == 'sink' and state_roles.get(src) != 'sink':
            if src in positions and dst in positions:
                fault_targets.setdefault(dst, []).append(src)

    result: dict = {}
    for fault_path, src_list in fault_targets.items():
        # Deduplicate sources while preserving order
        seen: set = set()
        unique_srcs = [s for s in src_list if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

        fx, fy, _, fh = positions[fault_path]
        spine_x  = fx - _SINK_SPINE_OFFSET
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


# ---------------------------------------------------------------------------
# Bottom-up per-subchart layout
# ---------------------------------------------------------------------------

def _navigate_to(chart_dict: dict, dotted_path: str) -> dict:
    """Return the state body dict at dotted_path within chart_dict."""
    body = chart_dict
    for segment in dotted_path.split('.'):
        body = body.get('states', {}).get(segment, {})
    return body


def elk_layout_bottomup(
    chart_dict: dict,
    layout_options: 'dict | None' = None,
    max_label_width: int = _LABEL_MAX_WIDTH_PX,
    label_substitution: bool = True,
    direction: str = 'DOWN',
    auto_sinks: 'frozenset[str]' = frozenset(),
    sink_bus_junctions: bool = False,
    sink_placement: str = 'none',
    subchart_leaf_size: 'tuple | None' = None,
) -> Tuple[dict, dict, dict]:
    """Run ELK bottom-up: per-subchart passes first, then chart root.

    Each subchart is laid out independently (ELK sees only its own children),
    and its bounding box is passed as a fixed size to the parent-level run.
    This prevents chart-level topology from distorting subchart internal layout.

    Returns (positions, edge_routing, sink_junctions) with chart-global
    accumulated positions — same format as elk_to_stateflow_layout(), so
    _sf_states_to_matlab_lines() works unchanged.
    """
    all_transitions = chart_dict.get('transitions', [])

    # --- 1. Collect all subchart paths ---
    subchart_set: set = set()

    def _find_subcharts(states: dict, prefix: str) -> None:
        for name, body in states.items():
            path = f'{prefix}.{name}' if prefix else name
            if body.get('subchart'):
                subchart_set.add(path)
            _find_subcharts(body.get('states', {}), path)

    _find_subcharts(chart_dict.get('states', {}), '')

    if not subchart_set:
        # No subcharts — single-pass is equivalent
        elk_json = sf_to_elk_json(chart_dict, layout_options=layout_options,
                                   max_label_width=max_label_width,
                                   label_substitution=label_substitution,
                                   direction=direction, auto_sinks=auto_sinks)
        return elk_to_stateflow_layout(
            elk_layout(elk_json), chart_dict,
            sink_bus_junctions=sink_bus_junctions,
            sink_placement=sink_placement, auto_sinks=auto_sinks,
        )

    # Deepest subcharts first
    sorted_subcharts = sorted(subchart_set, key=lambda p: p.count('.'), reverse=True)

    all_positions: Dict[str, Tuple[int, int, int, int]] = {}
    all_edge_routing: dict = {}
    fixed_sizes: Dict[str, Tuple[int, int]] = {}

    # --- 2. Per-subchart ELK runs ---
    for sc_path in sorted_subcharts:
        sc_body = _navigate_to(chart_dict, sc_path)
        sc_states = sc_body.get('states', {})
        if not sc_states:
            continue

        sc_prefix_dot = sc_path + '.'

        def _rel(path: str, _pfx: str = sc_prefix_dot) -> str:
            return path[len(_pfx):] if path.startswith(_pfx) else path

        # Transitions strictly inside this subchart (both endpoints under sc_path + '.')
        sc_transitions = [
            {**tr,
             'from': _rel(tr.get('from', '')),
             'to':   _rel(tr.get('to', '')),
             '_orig_idx': orig_idx}
            for orig_idx, tr in enumerate(all_transitions)
            if (tr.get('from', '').startswith(sc_prefix_dot)
                and tr.get('to', '').startswith(sc_prefix_dot))
        ]

        # fixed_sizes keys relative to this subchart
        sc_fixed = {
            path[len(sc_prefix_dot):]: sz
            for path, sz in fixed_sizes.items()
            if path.startswith(sc_prefix_dot)
        }
        sc_auto_sinks = frozenset(
            path[len(sc_prefix_dot):] for path in auto_sinks
            if path.startswith(sc_prefix_dot)
        )

        elk_json = sf_to_elk_json(
            {'states': sc_states, 'transitions': sc_transitions},
            layout_options=layout_options, max_label_width=max_label_width,
            label_substitution=label_substitution, direction=direction,
            auto_sinks=sc_auto_sinks, fixed_sizes=sc_fixed,
        )
        # Apply the subchart's own header height as top padding on the root
        # graph so child states land below the header label area.  Only set if
        # the caller hasn't already specified elk.padding in layout_options.
        if 'elk.padding' not in (layout_options or {}):
            sc_header_h = _compound_header_h(sc_body)
            elk_json.setdefault('layoutOptions', {})['elk.padding'] = (
                f'[top={sc_header_h + 20},right=20,bottom=20,left=20]'
            )
        elk_result = elk_layout(elk_json)

        # Accumulated positions (subchart-root = 0,0; subchart-relative)
        run_pos: Dict[str, Tuple[int, int, int, int]] = {}

        def _collect_run(node: dict, ox: float, oy: float,
                         _rp: dict = run_pos) -> None:
            nid = node.get('id', '')
            nx, ny = ox + node.get('x', 0.0), oy + node.get('y', 0.0)
            if nid and nid != 'root':
                _rp[nid] = (int(nx), int(ny),
                            int(node.get('width', 0)), int(node.get('height', 0)))
            for child in node.get('children', []):
                _collect_run(child, nx, ny, _rp)

        _collect_run(elk_result, 0.0, 0.0)

        # Store subchart-relative — will be offset to chart-global in step 4
        for rel_path, pos in run_pos.items():
            all_positions[f'{sc_path}.{rel_path}'] = pos

        # Edge routing (LCA-relative mid; OClock uses subchart-relative run_pos)
        def _collect_run_er(node: dict, ox: float, oy: float,
                            _rp: dict = run_pos,
                            _scp: str = sc_path) -> None:
            nx, ny = ox + node.get('x', 0.0), oy + node.get('y', 0.0)
            for edge in node.get('edges', []):
                eid = edge.get('id', '')
                if not eid.startswith('EDGE||'):
                    continue
                secs = edge.get('sections', [])
                if not secs:
                    continue
                sec = secs[0]
                parts = eid.split('||')
                src_rel = parts[1] if len(parts) > 1 else ''
                dst_rel = parts[2] if len(parts) > 2 else ''
                idx_s   = parts[3] if len(parts) > 3 else '0'
                start = sec.get('startPoint', {'x': 0.0, 'y': 0.0})
                end   = sec.get('endPoint',   {'x': 0.0, 'y': 0.0})
                mid_x = int((start['x'] + end['x']) / 2)
                mid_y = int((start['y'] + end['y']) / 2)
                src_oc, dst_oc = 3.0, 9.0
                if src_rel in _rp:
                    sx, sy, sw, sh = _rp[src_rel]
                    src_oc = _point_to_oclock(start['x'], start['y'],
                                              sx - nx, sy - ny, sw, sh)
                if dst_rel in _rp:
                    dx, dy, dw, dh = _rp[dst_rel]
                    dst_oc = _point_to_oclock(end['x'], end['y'],
                                              dx - nx, dy - ny, dw, dh)
                sf = f'{_scp}.{src_rel}' if src_rel else _scp
                df = f'{_scp}.{dst_rel}' if dst_rel else _scp
                all_edge_routing[f'EDGE||{sf}||{df}||{idx_s}'] = {
                    'mid_x': mid_x, 'mid_y': mid_y,
                    'src_oclock': src_oc, 'dst_oclock': dst_oc,
                }
            for child in node.get('children', []):
                _collect_run_er(child, nx, ny, _rp, _scp)

        _collect_run_er(elk_result, 0.0, 0.0)

        # Fixed size for the parent-level ELK run.
        # By default subcharts are treated as opaque leaf nodes at the parent
        # level — sized from their own label/actions (same as a collapsed
        # Stateflow subchart box), not from internal content.
        # subchart_leaf_size overrides this with an explicit (w, h) tuple.
        if subchart_leaf_size is not None:
            fixed_sizes[sc_path] = subchart_leaf_size
        else:
            from slxgen.stateflow import _sf_state_size  # noqa: PLC0415
            _leaf_body = {k: v for k, v in sc_body.items() if k != 'states'}
            fixed_sizes[sc_path] = _sf_state_size(_leaf_body)

    # --- 3. Chart-root ELK run (subcharts are fixed-size leaves) ---
    root_elk_json = sf_to_elk_json(
        chart_dict, layout_options=layout_options,
        max_label_width=max_label_width, label_substitution=label_substitution,
        direction=direction, auto_sinks=auto_sinks, fixed_sizes=fixed_sizes,
    )
    root_elk_result = elk_layout(root_elk_json)

    chart_global: Dict[str, Tuple[int, int, int, int]] = {}

    def _collect_root(node: dict, ox: float, oy: float) -> None:
        nid = node.get('id', '')
        nx, ny = ox + node.get('x', 0.0), oy + node.get('y', 0.0)
        if nid and nid != 'root':
            pos = (int(nx), int(ny),
                   int(node.get('width', 0)), int(node.get('height', 0)))
            chart_global[nid] = pos
            all_positions[nid] = pos
        # Don't recurse into subcharts (their internals come from per-subchart runs)
        if nid not in subchart_set:
            for child in node.get('children', []):
                _collect_root(child, nx, ny)

    _collect_root(root_elk_result, 0.0, 0.0)

    # Edge routing for chart-level transitions
    def _collect_root_er(node: dict, ox: float, oy: float) -> None:
        nx, ny = ox + node.get('x', 0.0), oy + node.get('y', 0.0)
        nid = node.get('id', '')
        for edge in node.get('edges', []):
            eid = edge.get('id', '')
            if not eid.startswith('EDGE||'):
                continue
            secs = edge.get('sections', [])
            if not secs:
                continue
            sec = secs[0]
            parts = eid.split('||')
            src_p = parts[1] if len(parts) > 1 else ''
            dst_p = parts[2] if len(parts) > 2 else ''
            start = sec.get('startPoint', {'x': 0.0, 'y': 0.0})
            end   = sec.get('endPoint',   {'x': 0.0, 'y': 0.0})
            mid_x = int((start['x'] + end['x']) / 2)
            mid_y = int((start['y'] + end['y']) / 2)
            src_oc, dst_oc = 3.0, 9.0
            if src_p in chart_global:
                sx, sy, sw, sh = chart_global[src_p]
                src_oc = _point_to_oclock(start['x'], start['y'],
                                          sx - nx, sy - ny, sw, sh)
            if dst_p in chart_global:
                dx, dy, dw, dh = chart_global[dst_p]
                dst_oc = _point_to_oclock(end['x'], end['y'],
                                          dx - nx, dy - ny, dw, dh)
            all_edge_routing[eid] = {
                'mid_x': mid_x, 'mid_y': mid_y,
                'src_oclock': src_oc, 'dst_oclock': dst_oc,
            }
        if nid not in subchart_set:
            for child in node.get('children', []):
                _collect_root_er(child, nx, ny)

    _collect_root_er(root_elk_result, 0.0, 0.0)

    # --- 4. Convert subchart-relative positions to chart-global ---
    # Process shallowest subcharts first so nested offsets accumulate correctly.
    for sc_path in reversed(sorted_subcharts):
        if sc_path not in all_positions:
            continue
        sc_gx, sc_gy = all_positions[sc_path][:2]
        sc_prefix_dot = sc_path + '.'
        # Deeper subchart prefixes — their children are handled in their own pass
        deeper = tuple(p + '.' for p in subchart_set if p.startswith(sc_prefix_dot))
        for full_path in list(all_positions.keys()):
            if not full_path.startswith(sc_prefix_dot):
                continue
            if deeper and any(full_path.startswith(d) for d in deeper):
                continue
            rx, ry, rw, rh = all_positions[full_path]
            all_positions[full_path] = (sc_gx + rx, sc_gy + ry, rw, rh)

    # --- 5. Sink placement and sink junctions ---
    state_roles: Dict[str, str] = {}

    def _collect_roles(states: dict, prefix: str) -> None:
        for name, body in states.items():
            path = f'{prefix}.{name}' if prefix else name
            state_roles[path] = _state_role(name, body, path=path, auto_sinks=auto_sinks)
            _collect_roles(body.get('states', {}), path)

    _collect_roles(chart_dict.get('states', {}), '')

    if sink_placement != 'none':
        _place_sink_states(all_positions, state_roles, sink_placement)
        _recompute_sink_edge_routing(all_edge_routing, all_positions, state_roles, sink_placement)

    sink_junctions = (
        _compute_sink_junctions(all_positions, state_roles, all_transitions)
        if sink_bus_junctions else {}
    )

    return all_positions, all_edge_routing, sink_junctions
