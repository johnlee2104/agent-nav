# Scalability deltas (base -> 10x), matched on queries: ['q01', 'q02', 'q04', 'q05', 'q06', 'q07', 'q08', 'q09']

## precision

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.64 | 0.75 | +0.11 |
| index | 0.77 | 0.78 | +0.00 |
| ledger | 0.77 | 0.71 | -0.06 |
| tag | 0.77 | 0.41 | -0.36 |

## recall

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.77 | 0.83 | +0.06 |
| index | 0.83 | 0.83 | +0.00 |
| ledger | 0.83 | 0.83 | +0.00 |
| tag | 0.83 | 0.58 | -0.25 |

## f1

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.65 | 0.75 | +0.10 |
| index | 0.76 | 0.77 | +0.01 |
| ledger | 0.76 | 0.74 | -0.03 |
| tag | 0.76 | 0.44 | -0.32 |

## mrr

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.75 | 0.80 | +0.05 |
| index | 0.91 | 0.94 | +0.03 |
| ledger | 0.97 | 0.97 | +0.00 |
| tag | 0.84 | 0.31 | -0.53 |

## task_success

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 1.00 | 1.00 | +0.00 |
| index | 0.94 | 0.94 | +0.00 |
| ledger | 1.00 | 1.00 | +0.00 |
| tag | 1.00 | 0.75 | -0.25 |

## hallucination

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.00 | 0.00 | +0.00 |
| index | 0.00 | 0.00 | +0.00 |
| ledger | 0.00 | 0.00 | +0.00 |
| tag | 0.00 | 0.00 | +0.00 |

## tool_calls

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 3.75 | 4.12 | +0.38 |
| index | 3.31 | 2.94 | -0.38 |
| ledger | 4.31 | 4.50 | +0.19 |
| tag | 3.81 | 6.38 | +2.56 |

## context_inflation

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 0.00 | 0.00 | +0.00 |
| index | 990.00 | 9442.00 | +8452.00 |
| ledger | 17.00 | 17.00 | +0.00 |
| tag | 15.00 | 15.00 | +0.00 |

## compliant

| method | base | 10x | delta |
|---|---|---|---|
| baseline | 1.00 | 1.00 | +0.00 |
| index | 1.00 | 1.00 | +0.00 |
| ledger | 1.00 | 1.00 | +0.00 |
| tag | 1.00 | 1.00 | +0.00 |

