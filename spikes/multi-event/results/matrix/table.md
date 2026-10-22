| config | runs | requests | failed | switches ok/total | stale | in-flight old | all workers on new event, s | switches with a worker unobserved | switch request, ms | kiosk p50/p99, ms | timer p50/p99, ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a-pg | 5 | 2818 | 0 | 100/100 | 0 | 10 | median 2.04 (1.97-2.99), max 5.09 (4.48-5.48) | 5 | 40 (37-49) | 7.5 (6.3-9.2) / 34 (25-54) | 9.5 (8.6-12.2) / 43 (28-64) |
| a-sqlite | 5 | 2819 | 0 | 100/100 | 0 | 1 | median 2.05 (2.03-2.98), max 5.50 (4.49-5.55) | 1 | 10 (8-15) | 3.4 (2.9-3.7) / 11 (8-16) | 3.2 (2.7-3.4) / 6 (6-13) |
| b-pg | 5 | 2819 | 0 | 100/100 | 0 | 1 | median 1.99 (1.99-2.06), max 4.99 (4.05-5.49) | 2 | 12 (11-13) | 6.3 (5.3-7.3) / 17 (11-24) | 9.8 (9.3-10.9) / 18 (17-30) |
| b-sqlite | 5 | 2818 | 0 | 100/100 | 0 | 39 | median 2.49 (2.05-3.00), max 5.05 (4.99-5.50) | 0 | 4 (4-5) | 3.7 (3.2-4.2) / 10 (9-15) | 3.3 (2.9-3.5) / 10 (5-18) |

Cells with a range show the median across runs, then (min-max) across runs.
