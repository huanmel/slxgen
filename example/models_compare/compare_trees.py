"""
Compare two Simulink model trees and generate a diff report for LLM analysis.

Processes both model trees (following ReferencedSubsystem links), then produces
a Markdown comparison report showing added/removed models and block-level diffs.

Output: <NameA>_vs_<NameB>_diff.md written to DIFF_DIR.

Usage:
  python notebooks/compare_trees.py
"""

import sys
import yaml
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
from slxgen import process_model_tree, compare_model_trees


PROJ_ROOT   = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp190_ox\dp190_ox_csw\.deps\comps\apps\tcuapp0'
FILTERS_YML = Path(__file__).parent.parent / 'data' / 'slx_filters_default.yml'

MODEL_A = r'model1.slx'
MODEL_B = r'model2.slx'

# Output folder for the diff report (created if missing)
DIFF_DIR = Path(MODEL_A).parent / 'diff_reports'

FILTERS = yaml.safe_load(FILTERS_YML.read_text())


# %%
DIFF_DIR.mkdir(exist_ok=True)

name_a = Path(MODEL_A).stem
name_b = Path(MODEL_B).stem

print(f"Processing tree A: {name_a}")
results_a = process_model_tree(MODEL_A, FILTERS, PROJ_ROOT, save=False)

print(f"\nProcessing tree B: {name_b}")
results_b = process_model_tree(MODEL_B, FILTERS, PROJ_ROOT, save=False)

diff_path = DIFF_DIR / f'{name_a}_vs_{name_b}_diff.md'
compare_model_trees(results_a, name_a, results_b, name_b, output_path=str(diff_path))
print(f"\nDone. Diff report: {diff_path}")
# %%
