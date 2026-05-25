"""Generate MATLAB scripts for Ex1_StMach_sf.yaml and optionally run them in MATLAB.

Full pipeline, made explicit:

    YAML  →  yaml_to_sir()  →  sir_validate()  →  sf_yaml_to_matlab()  →  .m script
                                     ↓
                             issues printed here
                             (errors abort; warnings continue)
"""
from pathlib import Path
import sys
import io
import contextlib
import yaml
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slxgen import sf_yaml_to_matlab
from slxgen.stateflow_sir import yaml_to_sir, sir_validate

YAML     = Path(__file__).parent / 'Ex1_StMach_sf.yaml'
OUT_DIR  = Path(__file__).parent / 'generated'
OUT_DIR.mkdir(exist_ok=True)

BASE_NAME = 'DevCtrl_StMach'

# ---------------------------------------------------------------------------
# Step 1 — Build SIR and validate
# ---------------------------------------------------------------------------
# yaml_to_sir() normalises the YAML into a flat typed graph.
# sir_validate() catches structural errors before any MATLAB is generated.
# sf_yaml_to_matlab() runs the same pipeline internally; we do it here
# explicitly so validation feedback appears once, clearly, before generation.

chart_dict = yaml.safe_load(YAML.read_text(encoding='utf-8'))
sir = yaml_to_sir(chart_dict)

print(f'SIR  {YAML.name}')
print(f'  States      : {len(sir.states)}')
print(f'  Transitions : {len(sir.transitions)}')
print(f'  Variables   : {len(sir.variables)}')

issues  = sir_validate(sir)
errors  = [m for m in issues if m.startswith('ERROR')]
warnings = [m for m in issues if m.startswith('WARNING')]

if errors:
    print(f'\nValidation ERRORS ({len(errors)}) — aborting:')
    for msg in errors:
        print(f'  {msg}')
    sys.exit(1)

if warnings:
    print(f'\nValidation warnings ({len(warnings)}):')
    for msg in warnings:
        print(f'  {msg}')
else:
    print('  Validation: clean')

print()

# ---------------------------------------------------------------------------
# Step 2 — Generate MATLAB scripts
# ---------------------------------------------------------------------------
# Production variant (default layout). Uncomment the block below to also
# build diagnostic variants for layout comparison.

variants = [
    {
        'suffix':     '',
        'model_name': BASE_NAME,
        'desc':       'ELK arc routing, pure layout (default)',
        'opts':       {},
    },
]

# --- Diagnostic / experimental variants (uncomment to compare) ---
# variants += [
#     {
#         'suffix':     '_ortho',
#         'model_name': BASE_NAME + '_ortho',
#         'desc':       'fault-bus junctions + orthogonal H/V routing',
#         'opts':       {'__fault_bus_junctions__': 'true', '__orthogonal_junctions__': 'true'},
#     },
#     {
#         'suffix':     '_bare',
#         'model_name': BASE_NAME + '_bare',
#         'desc':       'no transition geometry — Stateflow auto-routes (diagnostic)',
#         'opts':       {'__bare_transitions__': 'true'},
#     },
#     {
#         'suffix':     '_sink',
#         'model_name': BASE_NAME + '_sink',
#         'desc':       'post-ELK sink-state right-column repositioning (diagnostic)',
#         'opts':       {'__no_sink_placement__': 'false'},
#     },
# ]

outputs = []
for v in variants:
    output = OUT_DIR / (YAML.stem + v['suffix'] + '.m')
    # Suppress internal SIR stderr — validation already shown above.
    with contextlib.redirect_stderr(io.StringIO()):
        script = sf_yaml_to_matlab(YAML, export_charts=True, output_path=output,
                                   model_name=v['model_name'], elk_options=v['opts'])
    outputs.append(output)
    print(f"[{v['desc']}]")
    print(f"  Written: {output}  ({len(script.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 3 — Run in MATLAB (requires matlabengine / py311_slxgen env)
# ---------------------------------------------------------------------------
# Connects to a running MATLAB desktop session if one is available.
# Run `matlab.engine.shareEngine` in the MATLAB Command Window once to enable sharing.
# Falls back to starting a new engine if no shared session is found.

try:
    import matlab.engine  # type: ignore[import-untyped]
    sessions = matlab.engine.find_matlab()
    if sessions:
        print(f'\nConnecting to existing MATLAB session ({sessions[0]})...')
        eng = matlab.engine.connect_matlab(sessions[0])
        started = False
    else:
        print('\nNo shared MATLAB session found — starting new engine...')
        eng = matlab.engine.start_matlab()
        started = True
    eng.cd(str(OUT_DIR), nargout=0)
    for output in outputs:
        print(f'  Running {output.name} ...')
        eng.run(str(output), nargout=0)
        print(f'  Done.')
    if started:
        eng.quit()
except ImportError:
    print('\nNote: matlab.engine not available — skipping MATLAB execution.')
    print('      Activate py311_slxgen env and re-run to build .slx models.')
except Exception as e:
    print(f'\nMATLAB execution failed: {e}')
