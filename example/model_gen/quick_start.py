"""Minimal pipeline example — validate, generate, build, and lint in one call.

Recommended one-time MATLAB setup:
  Open MATLAB, then in the Command Window:
    >> matlab.engine.shareEngine('slxgen')

  After that every run connects in <1 s and you can watch execution live.
  The session survives Python restarts.

  To start MATLAB from Python with the desktop GUI visible, pass:
    open_desktop=True   (ignored when connecting to an existing session)

Usage:
  python example/model_gen/quick_start.py
"""
from pathlib import Path
from slxgen import run_pipeline

YAML = Path(__file__).parent / 'Ex1_StMach_sf.yaml'

run_pipeline(
    YAML,
    model_name='DevCtrl_StMach',
    dump_sir=True,       # write intermediate SIR JSON for debugging
    run_matlab=True,        # False  → only write the .m script, no MATLAB needed
    session_name='slxgen',  # shared session name (created if no session found)
    open_desktop=False,     # True   → open full MATLAB GUI when starting new engine
    lint=True,
)
