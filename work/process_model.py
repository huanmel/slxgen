"""
Process a Simulink model and all its referenced sub-models, generating LLM-suitable outputs.

Outputs (written to <ModelName>_reports/ folder next to the main .slx):
  <name>_full.json      - complete parsed model
  <name>_slim.json      - filtered, human/LLM-readable JSON
  <name>_slim.min.json  - minified slim JSON (smallest for LLM context)
  <name>_report.txt     - structured text report (best for LLM review)
  <name>_arch.md        - Markdown with hierarchical Mermaid diagrams

Usage:
  python notebooks/process_model.py
"""

import sys
import yaml
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
from slxgen import process_model_tree


PROJ_ROOT   = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\apps\tcuapp0'
MODEL_PATH  = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\apps\tcuapp0\models\TcuApp0.slx'
# PROJ_ROOT   = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\drivers\hmidrvrdp187'
# MODEL_PATH  = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\drivers\hmidrvrdp187\models\HMIDrvrDp187_sub.slx'
OUTPUT_DIR  = Path(MODEL_PATH).parent / (Path(MODEL_PATH).stem + '_reports')
FILTERS_YML = Path(__file__).parent.parent / 'data' / 'slx_filters_default.yml'

# Output file types to generate per model. Comment out what you don't need.
# Available: 'report.txt', 'arch.md', 'slim.json', 'slim.min.json', 'full.json', 'sf.yaml'
OUTPUTS = [
    'report.txt',    # flat text — primary LLM input (signal flow, block params)
    'sf.yaml',       # stateflow charts as portable nested YAML (one file per chart)
    # 'arch.md',       # Mermaid diagrams — architecture overview
    # 'slim.min.json', # minified JSON — useful for programmatic LLM embedding
    # 'slim.json',   # formatted JSON — for post-processing in code
    # 'full.json',   # complete unfiltered model — debug only
]

# Parse referenced Simulink libraries (SourceBlock refs like 'TmsCtlr_lib/Scale2MaxPrc')
# and save their reports into a 'libraries/' sub-folder under OUTPUT_DIR.
PARSE_LIBRARIES = True

FILTERS = yaml.safe_load(FILTERS_YML.read_text())


# %%
OUTPUT_DIR.mkdir(exist_ok=True)
print(f"Output folder: {OUTPUT_DIR}")
results = process_model_tree(MODEL_PATH, FILTERS, PROJ_ROOT, output_dir=str(OUTPUT_DIR),
                             outputs=OUTPUTS, parse_libraries=PARSE_LIBRARIES)
print(f"\nDone. {len(results)} model(s) processed -> {OUTPUT_DIR}")
# %%
