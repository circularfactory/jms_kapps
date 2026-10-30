# Setup and operation

## Set up

### Local stack

The local stack holds a triple store with the dataset `jms-kapps`, and an InfluxDB.

The three variables of the README's setup name the triple store of every command.

## Stop a run

Each process *deregisters* its service: it removes the service's address from the knowledge graph,
so that no other service calls it. A kill skips the deregistration: the service keeps its address,
and counts as live until its last heartbeat is 90 s old.

## Shared settings

| Setting | What it does |
|---|---|
| `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` | Lets a command clear a dataset on a triple store that is not on this machine. |
| `INFLUXDB_URL`, and `INFLUXDB_TOKEN` where the server asks for one | The InfluxDB of use case 1. Without `INFLUXDB_URL`, use case 1 uses the local InfluxDB on <http://localhost:8181>. |
| `JMS_KAPPS_GRAPHDB_PORT` | The port of this machine on which the local triple store answers, 7200 without it. See [Taken ports](#taken-ports). |
| `--dataset <name>` | The dataset a command runs on and clears, `jms-kapps` without it. `jms-uc1`, `jms-uc2` and their subcommands all take it. |

`uv run jms-uc1 --help` and `uv run jms-uc2 --help` list the options of each use case.

### Connection variables

For a triple store of your own, set `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD` to its address and its login:

```bash
export TRIPLESTORE_URL=https://your-store.example
export TRIPLESTORE_USERNAME=...
export TRIPLESTORE_PASSWORD=...
```

On Windows, in PowerShell:

```powershell
$env:TRIPLESTORE_URL = "https://your-store.example"
$env:TRIPLESTORE_USERNAME = "..."
$env:TRIPLESTORE_PASSWORD = "..."
```

### Dataset

Do not point a run at a dataset that something else uses.

On a triple store of your own, create the dataset once from
[`graphdb/jms-kapps-repo-config.ttl`](../graphdb/jms-kapps-repo-config.ttl). It is a dataset
with SHACL validation and the `rdfsplus-optimized` ruleset, the triple store's set of inference
rules.

A launch loads into its dataset the semantic middleware's Core ontology, service ontology and MES
ontology, and the ontologies and shapes of the use cases.

### Triple store workbench

The workbench is on the address of the triple store, <http://localhost:7200> for the local one.
Select the dataset at the top right, then open *SPARQL*. Each query of the use cases runs there
unchanged.

## FAQ

### SSH access

One forward is sufficient over plain `ssh`:

```bash
ssh -L 8091:127.0.0.1:8091 <host>
```

For use case 2, forward 8090 in the same way.

### Taken ports

When something else holds 7200, `docker compose up -d --wait` stops (see
[Port 7200 is taken](#common-errors)). There are two ways out.

**Move the local triple store.** Name a free port in `JMS_KAPPS_GRAPHDB_PORT`, start the local
triple store again, and point `TRIPLESTORE_URL` at that port. The username stays `admin` and the
password stays `root`:

```bash
export JMS_KAPPS_GRAPHDB_PORT=7201
docker compose up -d --wait
export TRIPLESTORE_URL=http://localhost:7201
```

On Windows, in PowerShell:

```powershell
$env:JMS_KAPPS_GRAPHDB_PORT = "7201"
docker compose up -d --wait
$env:TRIPLESTORE_URL = "http://localhost:7201"
```

Its workbench is then on <http://localhost:7201>.

**Use the triple store on 7200.** A GraphDB that already runs on 7200 can be the triple store of
the run. Set the three variables to it, as [Connection variables](#connection-variables) tells,
and create the dataset `jms-kapps` in it, as [Dataset](#dataset) tells.

### Common errors

A name in angle brackets is different from run to run.

**Port 7200 is taken.** `docker compose up -d --wait` fails with the message of Docker about the
port. Where another container holds it, the message is
`Bind for 127.0.0.1:7200 failed: port is already allocated`. Where another program holds it, the
message contains `address already in use`. Free the port, or do as [Taken ports](#taken-ports)
tells.

**The dataset is missing on your own triple store.** A command ends with a traceback, whose last
line is:

```text
kapps_triplestore_interface.exceptions.InvalidDatasetError: The store at <TRIPLESTORE_URL> has no repository '<dataset>' (404) - <the store's answer>
```

Create the dataset as [Dataset](#dataset) tells, or name a dataset of the triple store
with `--dataset`.

**The middleware refuses to clear a dataset on a triple store that is not on this machine.** A command ends with a traceback.
Its last line starts with `kapps_semantic_middleware.errors.DeploymentValueError:` and continues:

```text
Refusing to clear the default graph in dataset '<dataset>' on '<TRIPLESTORE_URL>'.
That host is neither loopback nor a container alias, so nothing here can tell that it is disposable.
Point TRIPLESTORE_URL at a local store or a service alias, or set TRIPLESTORE_ALLOW_DESTRUCTIVE=1 to state that the target is disposable.
```

The command cleared nothing. If only this demonstration uses the dataset, set
`TRIPLESTORE_ALLOW_DESTRUCTIVE=1` for that command.

**`jms-uc1` refuses while another service is live.** It exits with status 1, after this line:

```text
jms-uc1: <services> carry a fresh heartbeat in <dataset>, so nothing was cleared. No process of this launcher serves them: another launcher runs there, or a process runs on its own from its command line. Stop that first. A process that was killed counts until its last heartbeat is 90 s old. Or pass --force to clear it anyway.
```

Stop the run that holds the dataset with Ctrl+C, such as `jms-uc2` or another `jms-uc1`. After a
kill, wait 90 s. `--force` clears the dataset all the same, and this breaks a run that is still
live there. `jms-uc2` refuses in the same way, and its line starts with `jms-uc2:`.

**The local InfluxDB refuses a sixth database.** It holds a maximum of five databases. A run of use
case 1 that must create a new database ends its operation *failed*, with this reason:

```text
the unscrewing attempt failed: POST /api/v3/configure/database answered <status>: <InfluxDB's reason>
```

To clear the local InfluxDB, and only it:

```bash
docker compose rm --stop --force influxdb
docker volume rm jms-kapps_influxdb-data
docker compose up -d --wait
```
