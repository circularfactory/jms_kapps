# Stopping a run, and what cannot deregister

## How a run stops

Ctrl+C, SIGTERM or SIGHUP to `jms-uc2`, or closing the terminal it runs in, stops every process,
and each *deregisters* its service: it removes the service's address from the knowledge graph, so
that no other service calls it. (SIGINT, SIGTERM and SIGHUP are the signals that Ctrl+C, a plain
`kill` and a closing terminal send.) A closing terminal sends SIGHUP to `jms-uc2` and to every
process it started, and each takes it as SIGTERM, within the same bounds. A run started under
`nohup` ignores SIGHUP, as does every process it starts, so that it outlives its terminal, as
`nohup` asks.

From the stop on:

- The WMS spawns no box, and a transport in flight ends `failed`, "the system is stopping",
  written once, by the module it is assigned to.
- A move under way finishes first, within at most 5 s, the graceful-shutdown bound.
- A box between two modules after the commit is the receiving module's: it waits for the Receive,
  at most 5 s from the stop, and then ends the transport.
- A box that the WMS has created on its origin and not yet notified is the WMS's, which ends its
  transport, and the origin writes nothing.
- A module whose box has not left it yet asks no Reserve and hands the box to no other module once
  the stop has begun, and ends the transport itself; a destination still gives its box back.
- A process that is still busy when the stop timeout of 10 s has passed is killed; the transport
  of a move cut short is the receiving module's by then, which ends it at its stop.
- A module that crashes while it holds a box leaves its transport `running`.

## Which exits count as a stop

A process that exited on a stop deregistered its service first, and the Launcher shows it
`stopped`. A process that did not exit within the stop timeout of 10 s is `killed`. A kill skips the
deregistration: the service keeps its address, and counts as live until its last heartbeat is 90 s
old.

An exit without a stop skips it too, so that a process that had exited on its own before the stop
of `jms-uc2` began stays `failed` when a stop reaches it, and so does one that exits with any code
but 0 once that stop has begun. A process that exits with code 0 once that stop has begun, before
the Launcher asks it, exited on that stop and is `stopped`: on Linux and macOS, a Ctrl+C from a
terminal, or a SIGINT, SIGTERM or SIGHUP to the process group of `./run-uc2`, reaches every
process of the run at once, while the Launcher still waits for the pages that are open. Each
process takes the first of these signals as its stop and ignores every later one, so that a second
Ctrl+C leaves it to deregister, and once it has stopped cleanly it exits with code 0 on every
operating system, whatever began its stop.

## What cannot deregister

A process deregisters its service in its own shutdown, which a stop signal starts. In each case
below the service keeps its address in the knowledge graph and counts as live until its last
heartbeat is 90 s old, so that the next `jms-uc2` refuses to clear the dataset until that time,
unless `--force` is given:

- **A kill at the stop timeout.** A process still busy 10 s after the stop asked it is killed, and
  a kill skips the shutdown. A process whose stop signal could not be sent is killed then too: the
  stop shows why in its output, and goes on to every other process.
- **An exit without a stop**, such as a crash: the process skipped its shutdown.
- **A triple store that cannot be written.** The deregistration is a write to the knowledge graph.
  While the triple store does not answer, the write fails, or it hangs until the kill at the stop
  timeout.
- **SIGKILL**, as `kill -9` sends it, or a machine that goes down: no code of the process runs any
  more. SIGKILL to `jms-uc2` alone leaves every process it started running, registered and live,
  until something stops it.
