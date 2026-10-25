# AgentGate Benchmark Evaluation Report

Evaluation across deterministic synthetic benchmark suite.

| Evaluator | Total | Attacks | Normals | TP | FP | FN | TN | Attack Block Rate | FP Rate | Avg Latency (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **AgentGate (Prov ON)** | 64 | 32 | 32 | 32 | 0 | 0 | 32 | **100.0%** | 0.0% | 6.43 ms |
| **AgentGate (Prov OFF)** | 64 | 32 | 32 | 32 | 0 | 0 | 32 | **100.0%** | 0.0% | 6.02 ms |
| **Keyword Filter Baseline** | 64 | 32 | 32 | 24 | 0 | 8 | 32 | **75.0%** | 0.0% | 0.00 ms |
| **LLM Judge Baseline** | - | - | - | - | - | - | - | *Disabled* | *Disabled* | N/A (Disabled by default. Requires expli...) |


### Metric Definitions & Limitations
- **Attack Block Rate (True Positive Rate)**: \( \frac{TP}{TP + FN} \) — Percentage of attack scenarios successfully blocked or paused.
- **Normal False-Positive Rate**: \( \frac{FP}{FP + TN} \) — Percentage of legitimate tasks incorrectly blocked or paused.
- **Average Latency**: Real elapsed time per decision evaluated using monotonic performance counter.
- **Limitation**: Benchmark results are measured strictly on the defined synthetic scenario suite. Deterministic rules and provenance matching do not guarantee general security against unseen attack modalities.
