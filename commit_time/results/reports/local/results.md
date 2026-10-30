# Results of the store `local`

## The store

- URL: http://localhost:7200
- GraphDB 10.8.5, GRAPHDB_LITE, licensed cores 1
- ruleset: rdfsplus-optimized
- heap: 12.0 GiB (12884901888 bytes)

## The client

- CPU: AMD Ryzen 9 7900X 12-Core Processor, 24 logical CPUs
- memory: 62.0 GiB
- OS: Linux-7.0.0-38-generic-x86_64-with-glibc2.39
- the store runs on this client: yes
- jms_kapps commit: bc2d4d5edfffc4323a5fe0adeb1ce3dbb2e4bdba

## Replays with outcome mismatches

None.

## Runs with refused spawns

None.

## The order of each repetition

- heartbeats on, repetition 1: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 2: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 3: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 4: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 5: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 6: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 7: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 8: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 9: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 10: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 11: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 12: kapps-shacl-off, kapps-shacl-on, writes-only
- heartbeats on, repetition 13: kapps-shacl-on, writes-only, kapps-shacl-off
- heartbeats on, repetition 14: kapps-shacl-off, kapps-shacl-on, writes-only

## The runs

| Run | Arm | Heartbeats | Rep. | Outcome | Valid | Failed checks | Admitted in window | Not admitted | Refusals in window | Spawns accepted / offered | Client CPU median % | Other activity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261028-012516-e775 | kapps-shacl-on | on | 1 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 310, status: done 150 | none | none | 150 / 150 | 7.41 | none |
| 20261028-013818-91ed | kapps-shacl-off | on | 1 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 309, status: done 150 | none | none | 150 / 150 | 7.40 | none |
| 20261028-014507-1cd8 | kapps-shacl-off | on | 2 | completed | True | none | status: queued 150, heartbeat 309, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.18 | none |
| 20261028-015156-3b60 | kapps-shacl-on | on | 2 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 310 | none | none | 150 / 150 | 3.35 | none |
| 20261028-020451-37e6 | kapps-shacl-on | on | 3 | completed | True | none | status: queued 150, status: running 150, heartbeat 310, operation 600, status: done 150 | none | none | 150 / 150 | 3.31 | none |
| 20261028-021746-5c44 | kapps-shacl-off | on | 3 | completed | True | none | heartbeat 309, status: queued 150, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.09 | none |
| 20261028-022434-d489 | kapps-shacl-off | on | 4 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 310, status: done 150 | none | none | 150 / 150 | 3.13 | none |
| 20261028-023122-bd5d | kapps-shacl-on | on | 4 | completed | True | none | status: queued 150, heartbeat 309, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.13 | none |
| 20261028-024420-4806 | kapps-shacl-on | on | 5 | completed | True | none | status: queued 150, status: running 150, heartbeat 309, operation 600, status: done 150 | none | none | 150 / 150 | 3.30 | none |
| 20261028-025717-2473 | kapps-shacl-off | on | 5 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 310, status: done 150 | none | none | 150 / 150 | 3.06 | none |
| 20261028-030405-cac7 | kapps-shacl-off | on | 6 | completed | True | none | status: queued 150, heartbeat 310, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.09 | none |
| 20261028-031053-8aee | kapps-shacl-on | on | 6 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 309 | none | none | 150 / 150 | 3.26 | none |
| 20261028-032349-86c9 | kapps-shacl-on | on | 7 | completed | True | none | status: queued 151, heartbeat 310, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.22 | none |
| 20261028-033645-8128 | kapps-shacl-off | on | 7 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 308, status: done 150 | none | none | 150 / 150 | 3.14 | none |
| 20261028-034333-7e39 | kapps-shacl-off | on | 8 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 310 | none | none | 150 / 150 | 3.14 | none |
| 20261028-035021-5ee0 | kapps-shacl-on | on | 8 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 309 | none | none | 150 / 150 | 3.26 | none |
| 20261028-040317-a464 | kapps-shacl-on | on | 9 | completed | True | none | status: queued 151, heartbeat 309, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.22 | none |
| 20261028-041613-178d | kapps-shacl-off | on | 9 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 308 | none | none | 150 / 150 | 3.05 | none |
| 20261028-042302-90bc | kapps-shacl-off | on | 10 | completed | True | none | status: queued 150, heartbeat 310, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.09 | none |
| 20261028-042949-a6e3 | kapps-shacl-on | on | 10 | completed | True | none | status: queued 150, heartbeat 309, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.22 | none |
| 20261028-044248-51a7 | kapps-shacl-on | on | 11 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 310 | none | none | 150 / 150 | 3.10 | none |
| 20261028-045546-43f9 | kapps-shacl-off | on | 11 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 310 | none | none | 150 / 150 | 3.05 | none |
| 20261028-050233-5a0a | kapps-shacl-off | on | 12 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 309 | none | none | 150 / 150 | 3.01 | none |
| 20261028-050922-58f2 | kapps-shacl-on | on | 12 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 309, status: done 150 | none | none | 150 / 150 | 3.18 | none |
| 20261028-052220-2b55 | kapps-shacl-on | on | 13 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 310, status: done 150 | none | none | 150 / 150 | 3.06 | none |
| 20261028-053516-34f4 | kapps-shacl-off | on | 13 | completed | True | none | status: queued 150, heartbeat 309, status: running 150, operation 600, status: done 150 | none | none | 150 / 150 | 3.05 | none |
| 20261028-054204-afec | kapps-shacl-off | on | 14 | completed | True | none | status: queued 150, status: running 150, operation 600, status: done 150, heartbeat 308 | none | none | 150 / 150 | 2.97 | none |
| 20261028-054852-9401 | kapps-shacl-on | on | 14 | completed | True | none | status: queued 150, status: running 150, operation 600, heartbeat 310, status: done 150 | none | none | 150 / 150 | 3.14 | none |

## The writes-only replays

| Replay | Replays | Heartbeats | Rep. | Outcome | Valid | Writes before / in the window | Verified receipts | Late writes | Largest lateness (s) | Overtaking | Mismatches | Other activity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261028-013226-048d | 20261028-012516-e775 | on | 1 | completed | True | 663 / 1360 | 2023 | 0 | 0.00 | 4 | 0 | none |
| 20261028-015903-59d9 | 20261028-015156-3b60 | on | 2 | completed | True | 663 / 1360 | 2023 | 0 | 0.00 | 4 | 0 | none |
| 20261028-021159-b7ab | 20261028-020451-37e6 | on | 3 | completed | True | 661 / 1360 | 2021 | 0 | 0.00 | 4 | 0 | none |
| 20261028-023830-5a50 | 20261028-023122-bd5d | on | 4 | completed | True | 666 / 1359 | 2025 | 0 | 0.00 | 3 | 0 | none |
| 20261028-025128-000d | 20261028-024420-4806 | on | 5 | completed | True | 662 / 1359 | 2021 | 0 | 0.00 | 4 | 0 | none |
| 20261028-031801-010e | 20261028-031053-8aee | on | 6 | completed | True | 663 / 1359 | 2022 | 0 | 0.00 | 2 | 0 | none |
| 20261028-033056-c66f | 20261028-032349-86c9 | on | 7 | completed | True | 664 / 1360 | 2024 | 0 | 0.00 | 2 | 0 | none |
| 20261028-035728-1486 | 20261028-035021-5ee0 | on | 8 | completed | True | 665 / 1359 | 2024 | 0 | 0.00 | 2 | 0 | none |
| 20261028-041024-3b8c | 20261028-040317-a464 | on | 9 | completed | True | 661 / 1359 | 2020 | 0 | 0.00 | 1 | 0 | none |
| 20261028-043657-1af5 | 20261028-042949-a6e3 | on | 10 | completed | True | 668 / 1359 | 2027 | 0 | 0.00 | 1 | 0 | none |
| 20261028-044956-b488 | 20261028-044248-51a7 | on | 11 | completed | True | 666 / 1360 | 2026 | 0 | 0.00 | 2 | 0 | none |
| 20261028-051630-b2fc | 20261028-050922-58f2 | on | 12 | completed | True | 661 / 1359 | 2020 | 0 | 0.00 | 2 | 0 | none |
| 20261028-052928-cba3 | 20261028-052220-2b55 | on | 13 | completed | True | 667 / 1360 | 2027 | 0 | 0.00 | 0 | 0 | none |
| 20261028-055600-4b2c | 20261028-054852-9401 | on | 14 | completed | True | 666 / 1360 | 2026 | 0 | 0.00 | 2 | 0 | none |
