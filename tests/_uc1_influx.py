"""The InfluxDB database that every UC1 live test writes its frames into.

One database for all of them, whatever GraphDB dataset a run uses: `Tests`. InfluxDB 3 Core allows
five databases and refuses the sixth, so a database for each disposable dataset made every UC1 live
check fail on a machine after a few runs (jms_kapps 74). `Tests` exists already where the tests ran
on the GraphDB repository of that name. Nothing deletes it or its rows: its data stays for the
paper's runs. A test reads only its own frame, chosen by its id and time window. (Decided by Sören
Weindel on 2026-10-12, jms_kapps 103; before, the database was named after the dataset.)

The UC1 runtime and the demos keep the screwing resource's default database, `jms-kapps`.
"""

INFLUX_TEST_DATABASE = "Tests"
