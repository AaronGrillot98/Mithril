"""
Mithril benchmark.

Evaluates the default detection pipeline against a balanced corpus of attack +
benign prompts (see `scripts/benchmark_data.jsonl`). Reports a confusion matrix,
per-class precision/recall/F1, per-category breakdown, and latency.

Usage:
    python scripts/benchmark.py
    python scripts/benchmark.py --threshold 0.8
    python scripts/benchmark.py --data scripts/benchmark_data.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# Make `mithril` importable when invoked from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mithril.detectors import default_pipeline  # noqa: E402


def load_corpus(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


def run(corpus: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    pipeline = default_pipeline(threshold=threshold)

    tp = fp = tn = fn = 0
    latencies: list[float] = []
    misses_attack: list[dict[str, Any]] = []
    false_alarms: list[dict[str, Any]] = []
    by_cat: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "detected": 0}
    )

    for row in corpus:
        text = row["text"]
        label = row["label"]
        category = row.get("category", "uncategorized")

        t0 = time.perf_counter()
        result = pipeline.scan(text)
        latencies.append((time.perf_counter() - t0) * 1000)  # ms

        predicted_attack = result.blocked
        actually_attack = label == "attack"

        by_cat[category]["total"] += 1
        if predicted_attack:
            by_cat[category]["detected"] += 1

        if predicted_attack and actually_attack:
            tp += 1
        elif predicted_attack and not actually_attack:
            fp += 1
            false_alarms.append({"text": text[:120], "category": category, "score": result.score})
        elif not predicted_attack and actually_attack:
            fn += 1
            misses_attack.append({"text": text[:120], "category": category})
        else:
            tn += 1

    def safe_div(a: float, b: float) -> float:
        return a / b if b else 0.0

    precision_attack = safe_div(tp, tp + fp)
    recall_attack = safe_div(tp, tp + fn)
    f1_attack = safe_div(2 * precision_attack * recall_attack, precision_attack + recall_attack)

    precision_benign = safe_div(tn, tn + fn)
    recall_benign = safe_div(tn, tn + fp)
    f1_benign = safe_div(2 * precision_benign * recall_benign, precision_benign + recall_benign)

    total = tp + fp + tn + fn
    accuracy = safe_div(tp + tn, total)

    return {
        "threshold": threshold,
        "totals": {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "total": total},
        "attack": {
            "precision": precision_attack,
            "recall": recall_attack,
            "f1": f1_attack,
            "support": tp + fn,
        },
        "benign": {
            "precision": precision_benign,
            "recall": recall_benign,
            "f1": f1_benign,
            "support": tn + fp,
        },
        "accuracy": accuracy,
        "latency_ms": {
            "min": min(latencies),
            "median": statistics.median(latencies),
            "p95": sorted(latencies)[int(len(latencies) * 0.95)],
            "max": max(latencies),
            "mean": statistics.mean(latencies),
        },
        "by_category": dict(by_cat),
        "misses": misses_attack,
        "false_alarms": false_alarms,
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    a = report["attack"]
    b = report["benign"]
    t = report["totals"]
    lat = report["latency_ms"]

    lines.append("")
    lines.append(f"Mithril benchmark — threshold={report['threshold']}, n={t['total']}")
    lines.append("=" * 64)
    lines.append("")
    lines.append("              precision    recall   f1-score   support")
    lines.append("")
    lines.append(
        f"      attack       {a['precision']:.2f}      {a['recall']:.2f}      "
        f"{a['f1']:.2f}      {a['support']:>4d}"
    )
    lines.append(
        f"      benign       {b['precision']:.2f}      {b['recall']:.2f}      "
        f"{b['f1']:.2f}      {b['support']:>4d}"
    )
    lines.append("")
    lines.append(f"    accuracy                           {report['accuracy']:.2f}      {t['total']:>4d}")
    lines.append(
        f"   macro avg       {(a['precision']+b['precision'])/2:.2f}      "
        f"{(a['recall']+b['recall'])/2:.2f}      "
        f"{(a['f1']+b['f1'])/2:.2f}      {t['total']:>4d}"
    )
    lines.append("")
    lines.append("Confusion matrix")
    lines.append("-" * 64)
    lines.append(f"  TP={t['tp']:<4d}  FP={t['fp']:<4d}")
    lines.append(f"  FN={t['fn']:<4d}  TN={t['tn']:<4d}")
    lines.append("")
    lines.append("Latency (ms)")
    lines.append("-" * 64)
    lines.append(
        f"  min={lat['min']:.2f}   median={lat['median']:.2f}   "
        f"p95={lat['p95']:.2f}   max={lat['max']:.2f}"
    )
    lines.append("")
    lines.append("Per-category detection")
    lines.append("-" * 64)
    cats = sorted(report["by_category"].items(), key=lambda kv: kv[0])
    for name, c in cats:
        pct = (c["detected"] / c["total"]) * 100 if c["total"] else 0
        lines.append(f"  {name:<28}  {c['detected']:>2d}/{c['total']:<2d}  ({pct:5.1f}%)")
    lines.append("")

    if report["misses"]:
        lines.append("Missed attacks (false negatives)")
        lines.append("-" * 64)
        for m in report["misses"]:
            lines.append(f"  [{m['category']}] {m['text']}")
        lines.append("")
    if report["false_alarms"]:
        lines.append("False alarms (benign flagged as attack)")
        lines.append("-" * 64)
        for fa in report["false_alarms"]:
            lines.append(f"  [{fa['category']}] score={fa['score']:.2f}  {fa['text']}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(ROOT / "scripts" / "benchmark_data.jsonl"))
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--json", action="store_true", help="Emit raw JSON report.")
    args = p.parse_args()

    corpus = load_corpus(Path(args.data))
    report = run(corpus, args.threshold)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
