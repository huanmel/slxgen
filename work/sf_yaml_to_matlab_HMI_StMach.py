"""
Generate a MATLAB script that recreates the HMI_StMach Stateflow chart from its sf.yaml export.

The generated .m script uses the Stateflow API (Stateflow.State, Stateflow.Transition, etc.)
to build the chart programmatically. Run the output .m file in MATLAB to recreate the chart.

Usage:
  python work/sf_yaml_to_matlab_HMI_StMach.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))
from slx2txt import sf_yaml_to_matlab


YAML_PATH   = Path(r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\drivers\hmidrvrdp187\models\HMIDrvrDp187_sub_reports\HMI_StMach_sf.yaml')
OUTPUT_PATH = YAML_PATH.parent / 'HMI_StMach_sf.m'

# %%
script = sf_yaml_to_matlab(YAML_PATH, output_path=OUTPUT_PATH)
print(f"Written: {OUTPUT_PATH}")
print(f"Lines:   {script.count(chr(10))}")
print()
print(script)
# %%
