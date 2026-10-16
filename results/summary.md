# Lakehouse benchmark summary

Values are mean ± 95% CI half-width (t-distribution) unless n=1.

## E1 Governance propagation

| tables | approach | contract error | phase | seconds | statements | recall | over-masking |
|---|---|---|---|---|---|---|---|
| 40 | central | - | drift_after_sweep | 7.11 ± 5.3 (n=3) | 20 ± 11 (n=3) | 0.766 ± 0.15 (n=3) | 0.157 ± 0.11 (n=3) |
| 40 | central | - | drift_before_sweep | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) |
| 40 | central | - | rollout | 23.9 ± 6.1 (n=3) | 87 ± 8.6 (n=3) | 0.795 ± 0.067 (n=3) | 0.171 ± 0.014 (n=3) |
| 40 | domain | 0.0 | drift_at_publish | 6.95 ± 3.1 (n=3) | 14.7 ± 7.6 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 40 | domain | 0.0 | rollout | 33.8 ± 12 (n=3) | 73 ± 9 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 40 | domain | 0.05 | drift_at_publish | 6.35 ± 1.9 (n=3) | 14 ± 7.5 (n=3) | 0.954 ± 0.11 (n=3) | 0 ± 0 (n=3) |
| 40 | domain | 0.05 | rollout | 33.1 ± 12 (n=3) | 67.3 ± 6.3 (n=3) | 0.923 ± 0.029 (n=3) | 0 ± 0 (n=3) |
| 120 | central | - | drift_after_sweep | 17.9 ± 1.3 (n=3) | 57.3 ± 3.8 (n=3) | 0.816 ± 0.039 (n=3) | 0.172 ± 0.11 (n=3) |
| 120 | central | - | drift_before_sweep | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) |
| 120 | central | - | rollout | 67.1 ± 9.6 (n=3) | 262 ± 42 (n=3) | 0.782 ± 0.051 (n=3) | 0.173 ± 0.021 (n=3) |
| 120 | domain | 0.0 | drift_at_publish | 19.7 ± 6.3 (n=3) | 47 ± 11 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 120 | domain | 0.0 | rollout | 98 ± 15 (n=3) | 232 ± 30 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 120 | domain | 0.05 | drift_at_publish | 19.1 ± 1.2 (n=3) | 43.7 ± 10 (n=3) | 0.929 ± 0.02 (n=3) | 0 ± 0 (n=3) |
| 120 | domain | 0.05 | rollout | 93.6 ± 14 (n=3) | 218 ± 35 (n=3) | 0.936 ± 0.039 (n=3) | 0 ± 0 (n=3) |
| 400 | central | - | drift_after_sweep | 50.3 ± 7.9 (n=3) | 166 ± 11 (n=3) | 0.776 ± 0.16 (n=3) | 0.159 ± 0.033 (n=3) |
| 400 | central | - | drift_before_sweep | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) | 0 ± 0 (n=3) |
| 400 | central | - | rollout | 206 ± 28 (n=3) | 843 ± 79 (n=3) | 0.803 ± 0.014 (n=3) | 0.168 ± 0.017 (n=3) |
| 400 | domain | 0.0 | drift_at_publish | 64.3 ± 16 (n=3) | 150 ± 28 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 400 | domain | 0.0 | rollout | 310 ± 64 (n=3) | 742 ± 67 (n=3) | 1 ± 0 (n=3) | 0 ± 0 (n=3) |
| 400 | domain | 0.05 | drift_at_publish | 60.6 ± 5 (n=3) | 141 ± 24 (n=3) | 0.942 ± 0.016 (n=3) | 0 ± 0 (n=3) |
| 400 | domain | 0.05 | rollout | 309 ± 55 (n=3) | 704 ± 56 (n=3) | 0.948 ± 0.011 (n=3) | 0 ± 0 (n=3) |

## E2 Workload isolation (equal 4 credits/h for SHARED and ISOLATED)

| config | domain | completed per run | p50 s | p95 s | mean queued (overload) s |
|---|---|---|---|---|---|
| ISOLATED | CUSTOMER | 277 ± 16 (n=5) | 1.38 ± 0.04 (n=5) | 2.39 ± 0.26 (n=5) | 0 ± 0 (n=5) |
| ISOLATED | FINANCE | 300 ± 25 (n=5) | 1.34 ± 0.17 (n=5) | 2.49 ± 0.32 (n=5) | 0 ± 0 (n=5) |
| ISOLATED | SALES | 277 ± 16 (n=5) | 1.25 ± 0.04 (n=5) | 2.38 ± 0.25 (n=5) | 0 ± 0 (n=5) |
| ISOLATED | SUPPLY | 311 ± 9.7 (n=5) | 4.65 ± 0.18 (n=5) | 5.84 ± 0.4 (n=5) | 1.11 ± 0.12 (n=5) |
| SHARED | CUSTOMER | 189 ± 11 (n=5) | 1.9 ± 0.13 (n=5) | 3.44 ± 0.22 (n=5) | 0.347 ± 0.061 (n=5) |
| SHARED | FINANCE | 189 ± 7.9 (n=5) | 1.68 ± 0.38 (n=5) | 3.68 ± 0.21 (n=5) | 0.361 ± 0.063 (n=5) |
| SHARED | SALES | 208 ± 3 (n=5) | 1.67 ± 0.075 (n=5) | 3.17 ± 0.19 (n=5) | 0.337 ± 0.093 (n=5) |
| SHARED | SUPPLY | 510 ± 6.7 (n=5) | 2.83 ± 0.03 (n=5) | 3.53 ± 0.15 (n=5) | 0.64 ± 0.15 (n=5) |
| SHARED_MC | CUSTOMER | 274 ± 28 (n=5) | 1.29 ± 0.088 (n=5) | 2.42 ± 0.32 (n=5) | 0.0052 ± 0.0042 (n=5) |
| SHARED_MC | FINANCE | 310 ± 33 (n=5) | 1.23 ± 0.079 (n=5) | 2.54 ± 0.39 (n=5) | 0.0036 ± 0.0055 (n=5) |
| SHARED_MC | SALES | 328 ± 30 (n=5) | 1.09 ± 0.1 (n=5) | 2.31 ± 0.29 (n=5) | 0.0008 ± 0.00056 (n=5) |
| SHARED_MC | SUPPLY | 858 ± 72 (n=5) | 1.57 ± 0.16 (n=5) | 2.52 ± 0.26 (n=5) | 0.0028 ± 0.0024 (n=5) |

Query errors: 0

## E3 Cross-domain access

| method | create s | logical bytes | exposed to analyst | consumer query s |
|---|---|---|---|---|
| COPY | 3.36 ± 0.95 (n=5) | 307030016 | O_CLERK;C_PHONE | 0.195 ± 0.019 (n=5) |
| CLONE | 1.61 ± 0.13 (n=5) | 305674752 | none | 0.212 ± 0.077 (n=5) |
| VIEW | 0.396 ± 0.034 (n=5) | 0 | none | 0.236 ± 0.086 (n=5) |

| method | rows before insert | rows after insert | source rows after | refresh s |
|---|---|---|---|---|
| COPY | 14850000 | 14850000 | 15000000 | 3.135 |
| CLONE | 14850000 | 14850000 | 15000000 | 1.301 |
| VIEW | 14850000 | 15000000 | 15000000 | - |

| table | active bytes (owned) | retained for clone |
|---|---|---|
| SALES.ORDERS_DP | 310310400 | 0 |
| FINANCE.ORDERS_CLONE | 0 | 0 |
| FINANCE.ORDERS_COPY | 305178112 | 0 |

| warehouse | credits used |
|---|---|
| LH_WH_ADMIN | 3.616109961 |
| LH_WH_CUSTOMER | 0.608849425 |
| LH_WH_E3 | 0.302900278 |
| LH_WH_FINANCE | 0.578454188 |
| LH_WH_PROBE | 1.208901627 |
| LH_WH_SALES | 0.560415817 |
| LH_WH_SHARED | 2.415922204 |
| LH_WH_SHARED_MC | 5.548787185 |
| LH_WH_SUPPLY | 0.720331658 |
