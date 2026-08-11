"""Regenerate the detection-versus-exposure trade-off figure.

Reads results/results_stratified_model.json (the output of
`make eval-stratified`) and writes figure1_tradeoff.pdf. Detection is
aggregated macro F1; exposure is the mean per-record runtime AgentLeak score.
One curve per cohort, three width points per curve, plus the Baseline ceiling as a
horizontal reference.

The default input used to be results/results.json, which was the superseded
2026-08-02 run and is now archived as results/results_published_v1.json. That
run used the legacy allocator, which realised two plausibility and zero
consistency defects per cell, so its macro F1 axis was three-quarters constants.
Point this script at the archived file explicitly if the old figure is wanted.

Usage: .venv/bin/python scripts/make_figure.py [results.json] [out.pdf]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

from dqa.naming import BASELINE, confined

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
WIDTH_ORDER = ("minimal", "intermediate", "full")
COHORT_STYLE = {
    "synthea": {"color": "#1a6f8b", "marker": "o", "label": "Synthea"},
    "mimic-iv-demo": {"color": "#b0413e", "marker": "s", "label": "MIMIC-IV Demo"},
    "eicu-demo": {"color": "#5a7d2a", "marker": "^", "label": "eICU-CRD Demo"},
}


def main() -> int:
    default_results = ROOT / "results" / "results_stratified_model.json"
    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_results
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "results" / "figure1_tradeoff.pdf"

    data = json.loads(results_path.read_text())

    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    for cohort in data["cohorts"]:
        name = cohort["dataset"]
        style = COHORT_STYLE.get(name, {"color": "grey", "marker": "x", "label": name})
        xs, ys = [], []
        for width in WIDTH_ORDER:
            system = cohort["systems"][confined(width)]
            xs.append(system["agentleak_mean"])
            ys.append(system["scores"]["macro_f1"])
        ax.plot(xs, ys, linestyle="-", linewidth=1.2, **style)
        for x, y, width in zip(xs, ys, WIDTH_ORDER):
            ax.annotate(width, (x, y), textcoords="offset points", xytext=(5, 4), fontsize=7)
        a1 = cohort["systems"][BASELINE]["scores"]["macro_f1"]
        ax.axhline(a1, color=style["color"], linestyle=":", linewidth=0.8, alpha=0.6)

    ax.set_xlabel("AgentLeak normalised exposure (lower is better)")
    ax.set_ylabel("Aggregated detection F1 (higher is better)")
    ax.set_title("Detection versus inter-agent exposure across privilege widths")
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(out_path)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
