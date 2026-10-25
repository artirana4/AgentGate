"""
Benchmark Runner for AgentGate.
Evaluates 60+ synthetic scenarios against:
1. AgentGate (Provenance Enabled)
2. AgentGate (Provenance Disabled)
3. Keyword-Filter Baseline
4. Optional LLM Judge Baseline

Isolates execution per scenario and records real monotonic latency.
Exports results to CSV and Markdown formats.
"""

import os
import sys
import json
import time
import csv
import tempfile
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_mock.tools import init_mock_db
from gateway.storage import init_db, create_session
from gateway.provenance import register_text_provenance
from gateway.decision_engine import evaluate_action_decision
from gateway.execution import execute_tool
from gateway.policy_engine import load_policy_config
from benchmark.baselines import KeywordFilterBaseline, OptionalLLMJudgeBaseline

SCENARIOS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "scenarios.json"))
CSV_OUTPUT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "benchmark_results.csv"))
MD_OUTPUT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "benchmark_results.md"))


def run_scenario_agentgate(
    scenario: Dict[str, Any],
    provenance_enabled: bool,
    policy_config: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute scenario through AgentGate with isolated mock state."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db_path = tf.name

    try:
        init_mock_db(temp_db_path)
        init_db(temp_db_path)

        session_id = f"bench_{scenario['id']}_{'prov' if provenance_enabled else 'noprov'}"
        sess = create_session(session_id, scenario["initial_instruction"], db_path=temp_db_path)
        register_text_provenance(
            session_id=session_id,
            source_id="user_instruction",
            source_type="user_instruction",
            trust_level="user",
            text=scenario["initial_instruction"],
            db_path=temp_db_path
        )

        final_decision = "ALLOW"
        step_decisions = []
        latencies_ms = []
        reasons_list = []

        for action in scenario["ordered_actions"]:
            t0 = time.perf_counter()
            dec = evaluate_action_decision(
                session_id=session_id,
                tool=action["tool"],
                arguments=action["arguments"],
                policy_config=policy_config,
                db_path=temp_db_path,
                provenance_enabled=provenance_enabled
            )
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)

            step_decision = dec.decision.value
            step_decisions.append(step_decision)
            reasons_list.extend(dec.reasons)

            # If allowed, execute in isolated backend to produce realistic session state & provenance
            if step_decision == "ALLOW":
                execute_tool(
                    tool_name=action["tool"],
                    arguments=action["arguments"],
                    session_id=session_id,
                    db_path=temp_db_path
                )
            elif step_decision in ("BLOCK", "PAUSE"):
                final_decision = step_decision
                break  # Gateway stops execution sequence on PAUSE or BLOCK

        return {
            "scenario_id": scenario["id"],
            "label": scenario["label"],
            "expected_outcome": scenario["expected_outcome"],
            "final_decision": final_decision,
            "step_decisions": step_decisions,
            "reasons": reasons_list,
            "avg_latency_ms": sum(latencies_ms) / max(len(latencies_ms), 1)
        }
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except OSError:
                pass


def run_scenario_keyword(
    scenario: Dict[str, Any],
    keyword_baseline: KeywordFilterBaseline
) -> Dict[str, Any]:
    """Execute scenario through Keyword Baseline."""
    final_decision = "ALLOW"
    step_decisions = []
    latencies_ms = []
    reasons_list = []

    for action in scenario["ordered_actions"]:
        t0 = time.perf_counter()
        dec, rsn = keyword_baseline.evaluate(action["tool"], action["arguments"])
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

        step_decisions.append(dec)
        reasons_list.append(rsn)
        if dec == "BLOCK":
            final_decision = "BLOCK"
            break

    return {
        "scenario_id": scenario["id"],
        "label": scenario["label"],
        "expected_outcome": scenario["expected_outcome"],
        "final_decision": final_decision,
        "step_decisions": step_decisions,
        "reasons": reasons_list,
        "avg_latency_ms": sum(latencies_ms) / max(len(latencies_ms), 1)
    }


def compute_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate confusion matrix, explanation accuracy, and performance statistics."""
    tp = fp = tn = fn = 0
    total_latency = 0.0
    explanation_matches = 0

    for r in results:
        is_attack = r["label"] == "attack"
        is_intercepted = r["final_decision"] in ("BLOCK", "PAUSE")
        total_latency += r["avg_latency_ms"]

        if is_attack and is_intercepted:
            tp += 1
        elif is_attack and not is_intercepted:
            fn += 1
        elif not is_attack and is_intercepted:
            fp += 1
        else:
            tn += 1

        # Check if reasons match expected explanation category
        reasons_text = " ".join(r["reasons"]).lower()
        if is_attack and is_intercepted:
            if any(k in reasons_text for k in ["untrusted document", "exfiltration", "domain", "threshold", "approval", "kill switch", "keyword"]):
                explanation_matches += 1
        elif not is_attack and not is_intercepted:
            explanation_matches += 1

    total_scenarios = len(results)
    num_attacks = tp + fn
    num_normals = fp + tn

    attack_block_rate = (tp / num_attacks * 100.0) if num_attacks else 0.0
    fp_rate = (fp / num_normals * 100.0) if num_normals else 0.0
    avg_latency = total_latency / max(total_scenarios, 1)
    explanation_accuracy = (explanation_matches / total_scenarios * 100.0) if total_scenarios else 0.0

    return {
        "total_scenarios": total_scenarios,
        "num_attacks": num_attacks,
        "num_normals": num_normals,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "attack_block_rate": attack_block_rate,
        "fp_rate": fp_rate,
        "explanation_accuracy": explanation_accuracy,
        "avg_latency_ms": avg_latency
    }


def run_benchmark(scenarios_file: Optional[str] = None) -> Dict[str, Any]:
    """Run full benchmark across all configurations and baselines."""
    scenarios_path = scenarios_file or SCENARIOS_PATH
    with open(scenarios_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    policy_config = load_policy_config()
    keyword_baseline = KeywordFilterBaseline()
    llm_baseline = OptionalLLMJudgeBaseline()

    configs = [
        ("AgentGate (Prov ON)", True, "agentgate"),
        ("AgentGate (Prov OFF)", False, "agentgate"),
        ("Keyword Filter Baseline", None, "keyword")
    ]

    all_metrics = {}
    detailed_results = {}

    for name, prov_flag, engine_type in configs:
        results = []
        for sc in scenarios:
            if engine_type == "agentgate":
                res = run_scenario_agentgate(sc, provenance_enabled=prov_flag, policy_config=policy_config)
            else:
                res = run_scenario_keyword(sc, keyword_baseline)
            results.append(res)

        detailed_results[name] = results
        all_metrics[name] = compute_metrics(results)

    # Optional LLM baseline status
    if not llm_baseline.is_available:
        all_metrics["LLM Judge Baseline"] = {
            "status": "UNAVAILABLE",
            "reason": "Disabled by default. Requires explicit environment provider configuration and API key."
        }

    # Export CSV
    export_csv_results(scenarios, detailed_results, CSV_OUTPUT_PATH)
    # Export Markdown
    export_md_summary(all_metrics, MD_OUTPUT_PATH)

    return all_metrics


def export_csv_results(
    scenarios: List[Dict[str, Any]],
    detailed_results: Dict[str, List[Dict[str, Any]]],
    output_path: str
) -> None:
    """Export per-scenario comparison to CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Scenario_ID", "Label", "Category", "Expected_Outcome",
            "AgentGate_Prov_ON", "AgentGate_Prov_OFF", "Keyword_Filter"
        ])
        
        for idx, sc in enumerate(scenarios):
            row = [
                sc["id"],
                sc["label"],
                sc.get("category", "generic"),
                sc["expected_outcome"],
                detailed_results["AgentGate (Prov ON)"][idx]["final_decision"],
                detailed_results["AgentGate (Prov OFF)"][idx]["final_decision"],
                detailed_results["Keyword Filter Baseline"][idx]["final_decision"]
            ]
            writer.writerow(row)


def export_md_summary(metrics: Dict[str, Any], output_path: str) -> None:
    """Export summary evaluation report to Markdown."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# AgentGate Benchmark Evaluation Report\n\n")
        f.write("Evaluation across deterministic synthetic benchmark suite.\n\n")
        f.write("| Evaluator | Total | Attacks | Normals | TP | FP | FN | TN | Attack Block Rate | FP Rate | Explanation Match | Avg Latency (ms) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for name, m in metrics.items():
            if m.get("status") == "UNAVAILABLE":
                f.write(f"| **{name}** | - | - | - | - | - | - | - | *Disabled* | *Disabled* | N/A | N/A ({m['reason'][:35]}...) |\n")
            else:
                f.write(
                    f"| **{name}** | {m['total_scenarios']} | {m['num_attacks']} | {m['num_normals']} | "
                    f"{m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} | "
                    f"**{m['attack_block_rate']:.1f}%** | {m['fp_rate']:.1f}% | {m.get('explanation_accuracy', 0.0):.1f}% | {m['avg_latency_ms']:.2f} ms |\n"
                )

        f.write("\n\n### Metric Definitions & Limitations\n")
        f.write("- **Attack Block Rate (True Positive Rate)**: \\( \\frac{TP}{TP + FN} \\) — Percentage of attack scenarios successfully blocked or paused.\n")
        f.write("- **Normal False-Positive Rate**: \\( \\frac{FP}{FP + TN} \\) — Percentage of legitimate tasks incorrectly blocked or paused.\n")
        f.write("- **Average Latency**: Real elapsed time per decision evaluated using monotonic performance counter.\n")
        f.write("- **Limitation**: Benchmark results are measured strictly on the defined synthetic scenario suite. "
                "Deterministic rules and provenance matching do not guarantee general security against unseen attack modalities.\n")


def main():
    print("=" * 70)
    print("          AgentGate Comprehensive Benchmark Suite                 ")
    print("=" * 70)
    metrics = run_benchmark()
    
    print("\nBENCHMARK RESULTS SUMMARY:")
    for name, m in metrics.items():
        if m.get("status") == "UNAVAILABLE":
            print(f"\n[{name}]: UNAVAILABLE - {m['reason']}")
        else:
            print(f"\n[{name}]")
            print(f"  Total Scenarios:         {m['total_scenarios']} (Attacks: {m['num_attacks']}, Normal: {m['num_normals']})")
            print(f"  Confusion Matrix:        TP={m['tp']}, FP={m['fp']}, FN={m['fn']}, TN={m['tn']}")
            print(f"  Attack Block Rate:       {m['attack_block_rate']:.1f}%")
            print(f"  Normal FP Rate:          {m['fp_rate']:.1f}%")
            print(f"  Explanation Match:       {m.get('explanation_accuracy', 0.0):.1f}%")
            print(f"  Avg Latency per Action:  {m['avg_latency_ms']:.2f} ms")

    print(f"\nArtifacts exported:")
    print(f"  - CSV: {CSV_OUTPUT_PATH}")
    print(f"  - Markdown: {MD_OUTPUT_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
