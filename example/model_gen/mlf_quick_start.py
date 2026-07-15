"""Quick-start example — generate a Simulink MATLAB Function block from a YAML spec.

This script reads simple_filter_mlf.yaml and produces:
  generated/simple_filter_mlf.m    -- MATLAB build script
  generated/simple_filter_mlf.slx  -- Simulink model (if run_matlab=True)

The generated model contains a single MATLAB Function block (Stateflow.EMChart)
implementing a first-order low-pass filter:

  function y = SimpleFilter(u, Ts)
      persistent x_prev;
      ...
      y = ALPHA * u + (1 - ALPHA) * x_prev;

One-time MATLAB setup (persistent session, <1 s reconnect):
  Open MATLAB, then in the Command Window:
    >> matlab.engine.shareEngine('slxgen')

Usage:
  python example/model_gen/mlf_quick_start.py
"""
from pathlib import Path
from slxgen import run_pipeline

YAML = Path(__file__).parent / 'simple_filter_mlf.yaml'

result = run_pipeline(
    YAML,
    run_matlab=True,          # False -> only write the .m script, no MATLAB needed
    session_name='slxgen',    # shared session name (created if none found)
    open_desktop=False,       # True  -> open full MATLAB GUI when starting fresh engine
    lint=False,               # no Stateflow lint for MATLAB Function blocks
    gen_enums=False,          # no enums in this example
    verbose=True,
)

print()
print('Generated script :', result['script'])
print('Built model      :', result['slx'] or '(skipped — run_matlab=False)')
