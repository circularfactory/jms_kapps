# Results of the store `kit`

## The store

- URL: https://graphdb.[redacted private-hosts 1]
- GraphDB 10.8.5, GRAPHDB_ENTERPRISE, licensed cores 4
- ruleset: rdfsplus-optimized
- heap: 12.0 GiB (12884901888 bytes)

## The client

- CPU: AMD Ryzen 9 7900X 12-Core Processor, 24 logical CPUs
- memory: 62.0 GiB
- OS: Linux-7.0.0-38-generic-x86_64-with-glibc2.39
- the store runs on this client: no
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
| 20261027-180421-e676 | kapps-shacl-on | on | 1 | completed | True | none | status: done 150, status: running 150, operation 599, heartbeat 307, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-181812-8d50 | kapps-shacl-off | on | 1 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 309, status: queued 150 | none | none | 150 / 150 | 6.43 | none |
| 20261027-182514-8f6a | kapps-shacl-off | on | 2 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 307, status: queued 150 | none | none | 150 / 150 | 6.30 | none |
| 20261027-183217-5bb5 | kapps-shacl-on | on | 2 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 308, status: queued 150 | none | none | 150 / 150 | 6.39 | none |
| 20261027-184613-02ad | kapps-shacl-on | on | 3 | completed | True | none | operation 600, heartbeat 305, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.22 | none |
| 20261027-190003-8549 | kapps-shacl-off | on | 3 | completed | True | none | operation 600, heartbeat 309, status: done 150, status: running 150, status: queued 150 | none | none | 150 / 150 | 6.42 | none |
| 20261027-190704-fb21 | kapps-shacl-off | on | 4 | completed | True | none | operation 600, status: running 150, status: done 150, status: queued 150, heartbeat 308 | none | none | 150 / 150 | 6.31 | none |
| 20261027-191408-b4d3 | kapps-shacl-on | on | 4 | completed | True | none | heartbeat 310, operation 600, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.20 | none |
| 20261027-192753-04c3 | kapps-shacl-on | on | 5 | completed | True | none | heartbeat 309, operation 600, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.32 | none |
| 20261027-194146-13d8 | kapps-shacl-off | on | 5 | completed | True | none | operation 600, status: done 150, status: running 150, heartbeat 308, status: queued 150 | none | none | 150 / 150 | 6.36 | none |
| 20261027-194850-7745 | kapps-shacl-off | on | 6 | completed | True | none | operation 600, heartbeat 310, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.38 | none |
| 20261027-195553-6f2a | kapps-shacl-on | on | 6 | completed | True | none | operation 600, heartbeat 309, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.30 | none |
| 20261027-200944-c9c1 | kapps-shacl-on | on | 7 | completed | True | none | operation 600, heartbeat 310, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.81 | none |
| 20261027-202330-a81b | kapps-shacl-off | on | 7 | completed | True | none | heartbeat 308, status: done 150, status: running 150, operation 599, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-203033-3a7d | kapps-shacl-off | on | 8 | completed | True | none | operation 600, status: running 150, status: done 150, status: queued 150, heartbeat 310 | none | none | 150 / 150 | 6.80 | none |
| 20261027-203733-67af | kapps-shacl-on | on | 8 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 310, status: queued 150 | none | none | 150 / 150 | 6.76 | none |
| 20261027-205125-49aa | kapps-shacl-on | on | 9 | completed | True | none | heartbeat 310, operation 600, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-210515-c19a | kapps-shacl-off | on | 9 | completed | True | none | operation 600, heartbeat 310, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.82 | none |
| 20261027-211215-073e | kapps-shacl-off | on | 10 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 307, status: queued 150 | none | none | 150 / 150 | 6.76 | none |
| 20261027-211916-985b | kapps-shacl-on | on | 10 | completed | True | none | heartbeat 309, operation 600, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-213309-f846 | kapps-shacl-on | on | 11 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 309, status: queued 150 | none | none | 150 / 150 | 6.76 | none |
| 20261027-214700-49b3 | kapps-shacl-off | on | 11 | completed | True | none | operation 601, heartbeat 308, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.81 | none |
| 20261027-215401-81df | kapps-shacl-off | on | 12 | completed | True | none | operation 601, status: running 150, status: done 150, heartbeat 307, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-220102-7e83 | kapps-shacl-on | on | 12 | completed | True | none | operation 600, heartbeat 309, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.75 | none |
| 20261027-221502-c869 | kapps-shacl-on | on | 13 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 309, status: queued 150 | none | none | 150 / 150 | 6.84 | none |
| 20261027-222855-81b4 | kapps-shacl-off | on | 13 | completed | True | none | operation 601, heartbeat 308, status: running 150, status: done 150, status: queued 150 | none | none | 150 / 150 | 6.76 | none |
| 20261027-223557-825c | kapps-shacl-off | on | 14 | completed | True | none | operation 600, status: running 150, status: done 150, heartbeat 310, status: queued 150 | none | none | 150 / 150 | 6.80 | none |
| 20261027-224258-66db | kapps-shacl-on | on | 14 | completed | True | none | operation 600, status: done 150, status: running 150, heartbeat 309, status: queued 150 | none | none | 150 / 150 | 6.76 | none |

## The writes-only replays

| Replay | Replays | Heartbeats | Rep. | Outcome | Valid | Writes before / in the window | Verified receipts | Late writes | Largest lateness (s) | Overtaking | Mismatches | Other activity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261027-181149-705c | 20261027-180421-e676 | on | 1 | completed | True | 658 / 1357 | 2015 | 0 | 0.01 | 2 | 0 | none |
| 20261027-183951-d2d4 | 20261027-183217-5bb5 | on | 2 | completed | True | 660 / 1359 | 2019 | 0 | 0.01 | 3 | 0 | none |
| 20261027-185345-84f2 | 20261027-184613-02ad | on | 3 | completed | True | 661 / 1357 | 2018 | 0 | 0.00 | 1 | 0 | none |
| 20261027-192138-4481 | 20261027-191408-b4d3 | on | 4 | completed | True | 661 / 1360 | 2021 | 0 | 0.00 | 3 | 0 | none |
| 20261027-193523-3088 | 20261027-192753-04c3 | on | 5 | completed | True | 660 / 1360 | 2020 | 0 | 0.01 | 2 | 0 | none |
| 20261027-200327-3dd2 | 20261027-195553-6f2a | on | 6 | completed | True | 661 / 1359 | 2020 | 0 | 0.00 | 2 | 0 | none |
| 20261027-201711-3005 | 20261027-200944-c9c1 | on | 7 | completed | True | 659 / 1359 | 2018 | 0 | 0.00 | 5 | 0 | none |
| 20261027-204506-cd29 | 20261027-203733-67af | on | 8 | completed | True | 659 / 1360 | 2019 | 0 | 0.00 | 2 | 0 | none |
| 20261027-205854-915b | 20261027-205125-49aa | on | 9 | completed | True | 660 / 1360 | 2020 | 0 | 0.00 | 1 | 0 | none |
| 20261027-212648-b755 | 20261027-211916-985b | on | 10 | completed | True | 663 / 1359 | 2022 | 0 | 0.01 | 3 | 0 | none |
| 20261027-214039-b3fc | 20261027-213309-f846 | on | 11 | completed | True | 661 / 1359 | 2020 | 0 | 0.00 | 1 | 0 | none |
| 20261027-220837-4abf | 20261027-220102-7e83 | on | 12 | completed | True | 661 / 1359 | 2020 | 0 | 0.00 | 0 | 0 | none |
| 20261027-222233-05b1 | 20261027-221502-c869 | on | 13 | completed | True | 663 / 1359 | 2022 | 0 | 0.00 | 3 | 0 | none |
| 20261027-225031-5986 | 20261027-224258-66db | on | 14 | completed | True | 657 / 1360 | 2017 | 0 | 0.00 | 1 | 0 | none |
