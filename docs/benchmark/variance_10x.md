# Replication variance — N=3 replicates at 10× scale (per-run rows for replicate 1 are in runs_10x.csv)

## Per-method metric stability (mean ± std over replicate means)

| method | f1 | mrr | task_success | tool_calls |
|---|---|---|---|---|
| baseline | 0.72 ± 0.02 | 0.78 ± 0.03 | 0.94 ± 0.05 | 4.42 ± 0.21 |
| index | 0.79 ± 0.01 | 0.93 ± 0.00 | 0.96 ± 0.03 | 2.83 ± 0.08 |
| ledger | 0.72 ± 0.01 | 0.98 ± 0.01 | 1.00 ± 0.00 | 4.54 ± 0.06 |
| tag | 0.40 ± 0.03 | 0.30 ± 0.00 | 0.69 ± 0.05 | 5.94 ± 0.39 |

## Per-cell outcome stability (task_success / refused flips across replicates)

Cells with N=3 replicates: 64
Cells whose task_success FLIPPED across replicates: 7
  - q02 / baseline / forbid_native_search: task_success = [1, 0, 1]
  - q02 / tag / forbid_native_search: task_success = [1, 0, 1]
  - q06 / baseline / allow_native_search: task_success = [1, 1, 0]
  - q06 / baseline / forbid_native_search: task_success = [1, 1, 0]
  - q06 / index / forbid_native_search: task_success = [0, 1, 0]
  - q08 / tag / forbid_native_search: task_success = [1, 0, 1]
  - q09 / tag / forbid_native_search: task_success = [1, 1, 0]
