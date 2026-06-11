"""Fan Mode Control — PlantUML-first workflow example.

Demonstrates docs/workflow.md §1.3:

  Step 1  fan_ctrl_draft.puml   hand-drawn prototype; open in VS Code (Alt+D)
          — agree on topology, routing nodes, and action code —

  Step 2  fan_ctrl_sf.yaml      full specification: variables + actions added
          — run with PUML_ONLY=True to generate fan_ctrl_gen.puml —

  Step 3  compare draft vs. generated
          Open both .puml files side-by-side to verify the YAML captures
          every state, transition, and action from the original draft.

  Step 4  PUML_ONLY=False       generate enum .m files + run_pipeline() → .slx

MATLAB setup (one-time):
  >> matlab.engine.shareEngine('slxgen')
"""
from pathlib import Path
from slxgen import run_pipeline, sf_yaml_to_puml

HERE = Path(__file__).parent
YAML = HERE / 'fan_ctrl_sf.yaml'

# True  → re-export YAML → fan_ctrl_gen.puml, then compare with fan_ctrl_draft.puml
# False → generate enum .m files + run full pipeline → Stateflow .slx
PUML_ONLY = False

if PUML_ONLY:
    out = HERE / 'fan_ctrl_gen.puml'
    sf_yaml_to_puml(YAML, output_path=out)
    print(f'Generated: {out}')
    print(f'Compare with: {HERE / "fan_ctrl_draft.puml"}')
    print('Open both in VS Code and check:')
    print('  - all states present and nested correctly')
    print('  - entry actions match the draft en: annotations')
    print('  - no transitions missing')
else:
    run_pipeline(
        YAML,
        model_name='FanCtrl',
        gen_enums=True,
        gen_sldd=True,
        dump_sir=True,
        run_matlab=True,
        session_name='slxgen',
        open_desktop=False,
        lint=True,
        adaptive_leaf_width=True,
        adaptive_spacing=True,
        verbose=True,
    )
