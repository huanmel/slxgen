"""
Explore ELK layout option variants on any sf.yaml chart.

Generates a summary table of container sizes for each variant (Python-only, fast).
Optionally writes .m scripts for visual inspection in MATLAB.
Optionally saves results to a CSV log for future comparison across models.

Usage:
  python work/test_elk_variants.py                          # summary table for default YAML
  python work/test_elk_variants.py path/to/chart_sf.yaml   # summary for a specific chart
  python work/test_elk_variants.py --write                  # also write .m files
  python work/test_elk_variants.py --log results.csv        # append results to CSV

The VARIANTS list below is the research record. Slug names starting with 'v' + two-digit
number let you cross-reference with MATLAB PNG exports. Findings from the HMI_StMach
exploration (2026-05):
  Best overall: v34 (LINEAR_SEGMENTS + CENTER + SPLINES + spacing 50/60 + labelw=150)
  Key insight:  _LABEL_MAX_WIDTH_PX controls container width. 150 is the sweet spot for
                charts with long transition guards (40-80 chars). Lower -> right-clip.
                Higher -> massive left whitespace in containers with feedback arcs.
"""

import csv
import sys
from pathlib import Path
import yaml

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from slxgen.elk_layout import sf_to_elk_json, elk_layout, elk_to_stateflow_layout

# Default YAML — override by passing a path on the command line
YAML_PATH   = ROOT / 'work/HMI_example/HMIDrvrDp187_sub_reports/HMI_StMach_sf.yaml'
OUT_DIR     = ROOT / 'work/temp/elk_variants'

# ---------------------------------------------------------------------------
# Variants — each entry is (label, description, elk_options override dict)
# Slugs must start with a letter (MATLAB identifier rule).
# ---------------------------------------------------------------------------
VARIANTS = [
    (
        'v00_baseline',
        'Baseline (BRANDES_KOEPF + LONGEST_PATH + BALANCED + ORTHOGONAL, DOWN)',
        {},
    ),
    # --- nodePlacement.strategy ---
    (
        'v01_placement_LINEAR_SEGMENTS',
        'nodePlacement.strategy=LINEAR_SEGMENTS - straightens long node chains',
        {'elk.layered.nodePlacement.strategy': 'LINEAR_SEGMENTS'},
    ),
    (
        'v02_placement_NETWORK_SIMPLEX',
        'nodePlacement.strategy=NETWORK_SIMPLEX - minimises total edge length',
        {'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX'},
    ),
    (
        'v03_placement_SIMPLE',
        'nodePlacement.strategy=SIMPLE - fast greedy, less optimal',
        {'elk.layered.nodePlacement.strategy': 'SIMPLE'},
    ),
    # --- bk.fixedAlignment ---
    (
        'v04_align_NONE',
        'bk.fixedAlignment=NONE - no alignment correction',
        {'elk.layered.nodePlacement.bk.fixedAlignment': 'NONE'},
    ),
    (
        'v05_align_LEFTUP',
        'bk.fixedAlignment=LEFTUP - nodes pull toward top-left',
        {'elk.layered.nodePlacement.bk.fixedAlignment': 'LEFTUP'},
    ),
    (
        'v06_align_RIGHTUP',
        'bk.fixedAlignment=RIGHTUP - nodes pull toward top-right',
        {'elk.layered.nodePlacement.bk.fixedAlignment': 'RIGHTUP'},
    ),
    # --- layering.strategy ---
    (
        'v07_layering_NETWORK_SIMPLEX',
        'layering.strategy=NETWORK_SIMPLEX - globally optimal (default ELK)',
        {'elk.layered.layering.strategy': 'NETWORK_SIMPLEX'},
    ),
    (
        'v08_layering_COFFMAN_GRAHAM',
        'layering.strategy=COFFMAN_GRAHAM - width-limited BFS layering',
        {'elk.layered.layering.strategy': 'COFFMAN_GRAHAM'},
    ),
    # --- edgeRouting ---
    (
        'v09_routing_POLYLINE',
        'edgeRouting=POLYLINE - straight diagonal segments, no 90-deg bends',
        {'elk.edgeRouting': 'POLYLINE'},
    ),
    (
        'v10_routing_SPLINES',
        'edgeRouting=SPLINES - smooth Bezier curves (closest to native Stateflow)',
        {'elk.edgeRouting': 'SPLINES'},
    ),
    # --- direction ---
    (
        'v11_direction_RIGHT',
        'elk.direction=RIGHT - horizontal flow (left->right)',
        {'elk.direction': 'RIGHT'},
    ),
    # --- spacing ---
    (
        'v12_spacing_tight',
        'Tight spacing (nodeNode=40, betweenLayers=50)',
        {
            'elk.spacing.nodeNode':                      '40',
            'elk.layered.spacing.nodeNodeBetweenLayers': '50',
        },
    ),
    (
        'v13_spacing_wide',
        'Wide spacing (nodeNode=120, betweenLayers=160)',
        {
            'elk.spacing.nodeNode':                      '120',
            'elk.layered.spacing.nodeNodeBetweenLayers': '160',
        },
    ),
    # --- combined ---
    (
        'v14_splines_right',
        'SPLINES + RIGHT direction - smooth arcs, horizontal flow',
        {'elk.edgeRouting': 'SPLINES', 'elk.direction': 'RIGHT'},
    ),
    (
        'v15_linear_splines',
        'LINEAR_SEGMENTS + SPLINES - straight chains, smooth arcs',
        {
            'elk.layered.nodePlacement.strategy': 'LINEAR_SEGMENTS',
            'elk.edgeRouting': 'SPLINES',
        },
    ),
    # --- cycleBreaking.strategy ---
    (
        'v16_cycle_DEPTH_FIRST',
        'cycleBreaking.strategy=DEPTH_FIRST - DFS-based back-edge reversal',
        {'elk.layered.cycleBreaking.strategy': 'DEPTH_FIRST'},
    ),
    (
        'v17_cycle_MODEL_ORDER',
        'cycleBreaking.strategy=MODEL_ORDER - respects YAML declaration order',
        {'elk.layered.cycleBreaking.strategy': 'MODEL_ORDER'},
    ),
    # --- nodePlacement.alignment (within BRANDES_KOEPF) ---
    (
        'v18_align_RIGHT',
        'bk.fixedAlignment=RIGHT - nodes align to right candidate',
        {'elk.layered.nodePlacement.bk.fixedAlignment': 'RIGHT'},
    ),
    (
        'v19_align_LEFT',
        'bk.fixedAlignment=LEFT - nodes align to left candidate',
        {'elk.layered.nodePlacement.bk.fixedAlignment': 'LEFT'},
    ),
    # --- crossingMinimisation ---
    (
        'v20_crossing_LAYER_SWEEP',
        'crossingMinimisation.strategy=LAYER_SWEEP - standard sweep (default)',
        {'elk.layered.crossingMinimisation.strategy': 'LAYER_SWEEP'},
    ),
    # --- compaction after SPLINES ---
    (
        'v21_linear_tight',
        'LINEAR_SEGMENTS + tight spacing - compact chains',
        {
            'elk.layered.nodePlacement.strategy': 'LINEAR_SEGMENTS',
            'elk.spacing.nodeNode':                      '50',
            'elk.layered.spacing.nodeNodeBetweenLayers': '60',
        },
    ),
    # --- Advanced Alignment & Straightening ---
    (
        'v22_favor_straight',
        'favorStraightEdges=true - forces nodes with direct transitions to line up',
        {'elk.layered.nodePlacement.favorStraightEdges': 'true'},
    ),
    (
        'v23_universal_align_CENTER',
        'alignment=CENTER - centers all node boxes on a clean shared axis',
        {'elk.layered.nodePlacement.alignment': 'CENTER'},
    ),
    (
        'v24_universal_align_STRETCH',
        'alignment=STRETCH - forces uniform state box sizes for perfect grids',
        {'elk.layered.nodePlacement.alignment': 'STRETCH'},
    ),
    # --- Aggressive Crossing Minimization ---
    (
        'v25_force_node_ordering',
        'forceNodeOrdering=true - aggressively re-orders nodes to stop line crossings',
        {'elk.layered.crossingMinimisation.forceNodeOrdering': 'true'},
    ),
    # --- Mega-Combo: The Stateflow Cleanroom ---
    (
        'v26_state_machine_optimized',
        'LINEAR_SEGMENTS + CENTER + favorStraight + SPLINES + DEPTH_FIRST cycle break',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.layered.nodePlacement.favorStraightEdges': 'true',
            'elk.edgeRouting':                              'SPLINES',
            'elk.layered.cycleBreaking.strategy':           'DEPTH_FIRST',
        },
    ),
    # --- v21 base + new options ---
    (
        'v27_linear_tight_straight',
        'v21 + favorStraightEdges + CENTER alignment',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.layered.nodePlacement.favorStraightEdges': 'true',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
        },
    ),
    (
        'v28_linear_tight_splines',
        'v21 + SPLINES + CENTER + favorStraight - best compact+smooth combo',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.layered.nodePlacement.favorStraightEdges': 'true',
            'elk.edgeRouting':                              'SPLINES',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
        },
    ),
    # --- Label-width hint sweep (fixes OFF container overflow) ---
    # Lower value = less horizontal space reserved for arc labels
    (
        'v29_labelw_100',
        'max_label_width=100 - minimal arc space reservation (tighter container)',
        {'__max_label_width__': 100},
    ),
    (
        'v30_labelw_50',
        'max_label_width=50 - very tight arc label space',
        {'__max_label_width__': 50},
    ),
    (
        'v31_labelw_0',
        'max_label_width=0 - zero label width hint (ELK ignores label geometry)',
        {'__max_label_width__': 0},
    ),
    # --- User-suggested container + label fix combo ---
    (
        'v32_container_fix',
        'LINEAR_SEGMENTS + SPLINES + edgeLabels.placement=CENTER + labelw=100',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.layered.nodePlacement.favorStraightEdges': 'true',
            'elk.edgeRouting':                              'SPLINES',
            'elk.edgeLabels.placement':                     'CENTER',
            'elk.spacing.nodeNode':                         '60',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '80',
            '__max_label_width__': 100,
        },
    ),
    (
        'v33_container_fix_tight',
        'v32 with tight spacing + labelw=50 - most compact container test',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.edgeRouting':                              'SPLINES',
            'elk.edgeLabels.placement':                     'CENTER',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
            '__max_label_width__': 50,
        },
    ),
    # --- Sweet-spot label width sweep ---
    (
        'v34_labelw_150',
        'LINEAR_SEGMENTS + SPLINES + tight + labelw=150',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.edgeRouting':                              'SPLINES',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
            '__max_label_width__': 150,
        },
    ),
    (
        'v35_labelw_200',
        'LINEAR_SEGMENTS + SPLINES + tight + labelw=200',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.edgeRouting':                              'SPLINES',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
            '__max_label_width__': 200,
        },
    ),
    (
        'v36_labelw_250',
        'LINEAR_SEGMENTS + SPLINES + tight + labelw=250',
        {
            'elk.layered.nodePlacement.strategy':           'LINEAR_SEGMENTS',
            'elk.layered.nodePlacement.alignment':          'CENTER',
            'elk.edgeRouting':                              'SPLINES',
            'elk.spacing.nodeNode':                         '50',
            'elk.layered.spacing.nodeNodeBetweenLayers':    '60',
            '__max_label_width__': 250,
        },
    ),
    # --- Label substitution (Step 4 from proposals.md) ---
    # Default is label_substitution=True (no label dims given to ELK, routes geometrically).
    # v37 disables it to compare against the _LABEL_MAX_WIDTH_PX=150 approach.
    (
        'v37_no_label_sub',
        'label_substitution=False - pass real label sizes to ELK (uses _LABEL_MAX_WIDTH_PX=150)',
        {'__label_substitution__': False},
    ),
]

# ---------------------------------------------------------------------------

def _split_opts(opts: dict):
    """Separate __special__ keys from real ELK layout options."""
    max_lw    = opts.pop('__max_label_width__',    None)
    label_sub = opts.pop('__label_substitution__', None)
    return opts, max_lw, label_sub


def run_variant(chart_dict: dict, opts: dict) -> dict:
    """Return positions dict for a given set of ELK overrides."""
    opts = dict(opts)  # don't mutate the original
    opts, max_lw, label_sub = _split_opts(opts)
    kw: dict = {}
    if max_lw is not None:
        kw['max_label_width'] = max_lw
    if label_sub is not None:
        kw['label_substitution'] = label_sub
    elk_in  = sf_to_elk_json(chart_dict, layout_options=opts, **kw)
    elk_out = elk_layout(elk_in)
    positions, _ = elk_to_stateflow_layout(elk_out)
    return positions


def fmt_box(pos):
    x, y, w, h = pos
    return f'{w}x{h} @({x},{y})'


def run_all(chart_dict: dict) -> list[dict]:
    """Run all variants; return list of result dicts."""
    rows = []
    for slug, desc, opts in VARIANTS:
        try:
            pos = run_variant(chart_dict, opts)
            rows.append({'slug': slug, 'desc': desc, 'positions': pos, 'error': None})
        except Exception as e:
            rows.append({'slug': slug, 'desc': desc, 'positions': {}, 'error': str(e)})
    return rows


def print_summary(rows: list[dict], top_keys: list[str] | None = None):
    """Print a table. top_keys: state paths to show (auto-detected if None)."""
    if top_keys is None:
        # collect all root-level state keys that appear across variants
        seen: set[str] = set()
        for r in rows:
            seen.update(k for k in r['positions'] if '.' not in k)
        top_keys = sorted(seen)[:3]  # show up to 3

    cols = top_keys or ['NORMAL', 'NORMAL.ON', 'NORMAL.OFF']
    header = f"{'Variant':<35}" + ''.join(f' {c:>20}' for c in cols)
    print(header)
    print('-' * len(header))
    for r in rows:
        if r['error']:
            print(f"{r['slug']:<35}  ERROR: {r['error']}")
        else:
            vals = ''.join(f' {fmt_box(r["positions"].get(c, (0,0,0,0))):>20}' for c in cols)
            print(f"{r['slug']:<35}{vals}")
    print()
    print('Description key:')
    for r in rows:
        print(f"  {r['slug']}: {r['desc']}")


def write_log(rows: list[dict], log_path: Path, model_name: str):
    """Append results to a CSV log file for cross-model comparison."""
    import csv
    fieldnames = ['model', 'slug', 'desc'] + [
        f'{k}_{dim}' for k in ('NORMAL', 'NORMAL.ON', 'NORMAL.OFF')
        for dim in ('w', 'h', 'x', 'y')
    ]
    write_header = not log_path.exists()
    with log_path.open('a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        if write_header:
            w.writeheader()
        for r in rows:
            row = {'model': model_name, 'slug': r['slug'], 'desc': r['desc']}
            for k in ('NORMAL', 'NORMAL.ON', 'NORMAL.OFF'):
                x, y, ww, h = r['positions'].get(k, (0, 0, 0, 0))
                row[f'{k}_w'] = ww; row[f'{k}_h'] = h
                row[f'{k}_x'] = x;  row[f'{k}_y'] = y
            w.writerow(row)
    print(f'Results appended to: {log_path}')


def write_scripts(chart_dict: dict, out_dir: Path, model_prefix: str):
    from slxgen.stateflow import stateflow_dict_to_matlab
    out_dir.mkdir(parents=True, exist_ok=True)
    for slug, desc, opts in VARIANTS:
        try:
            script = stateflow_dict_to_matlab(
                chart_dict,
                model_name=f'{model_prefix}_{slug}',
                export_charts=True,
                elk_options=opts,
            )
            out = out_dir / f'{slug}.m'
            out.write_text(f'% Variant: {desc}\n' + script, encoding='ascii')
            print(f'  Written: {out.name}')
        except Exception as e:
            print(f'  ERROR {slug}: {e}')
    print(f'\nAll .m files written to: {out_dir}')
    print('Run each in MATLAB (from that folder) to compare visual results.')


if __name__ == '__main__':
    args = sys.argv[1:]

    # Parse positional YAML path arg
    yaml_path = YAML_PATH
    remaining = []
    for a in args:
        if not a.startswith('--') and Path(a).suffix in ('.yaml', '.yml'):
            yaml_path = Path(a)
        else:
            remaining.append(a)
    args = remaining

    chart_dict = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    model_prefix = yaml_path.stem.removesuffix('_sf')

    rows = run_all(chart_dict)
    print_summary(rows)

    if '--write' in args:
        print()
        write_scripts(chart_dict, OUT_DIR, model_prefix)

    log_idx = next((i for i, a in enumerate(args) if a == '--log'), None)
    if log_idx is not None and log_idx + 1 < len(args):
        write_log(rows, Path(args[log_idx + 1]), model_prefix)
