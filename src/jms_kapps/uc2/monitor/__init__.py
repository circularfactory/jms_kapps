"""The monitor of use case 2: its own process, its own middleware, its own page.

The Launcher starts it and links to it, in "graph only" unless a person ticked the box that
collects the refusals in the monitor, which starts it in "graph + streams". Its middleware
registers it as a Resource with a Service and no workflows, and sends its heartbeat, so the
Launcher sees it live. It has no classes of its own: the use case's ontology, which the paper
prints, names none for a monitor, so it registers as a plain `cfc:Resource` with a plain
`svc:Service`. Its own code writes nothing to the graph. It reads the graph in the background,
and in "graph + streams" the participants' handover event streams too, but not in "graph only"
(`memory.py`, with every graph query in `reads.py`, which keeps the query each read sent last).
`api.py` serves what it holds as JSON, with the wires between the modules (`wires.py`) and the
handovers it saw (`handovers.py`), and takes the mode and the interval of the graph read that a
page sets; `static/` is the page. It computes no route: only the module that holds a box knows
where the box goes next.

**The monitor serves a page of its own**, not a dashboard of the middleware's dashboard registry,
which the dashboards of the modules and the WMS use.
"""
