"""Use case 2: the decentral FlexConveyor.

Each conveyor module runs as its own process. One hop works like the real FlexConveyor
(`handshake.py`): the giving module asks the receiving module to reserve itself, its handover
commits the change of possession before the box moves, it conveys the box, its own move, and it
calls the receiving module's Receive, the light barrier, from which the receiving module carries
the box on. The WarehouseManagementSystem (`wms.py`) starts a box's journey and ends it: it spawns
the box onto the FlexConveyor station and notifies its origin, and the destination gives the box
back and notifies it. The journey is one transport in the graph (`transport.py`), and every write of it is a commit of
the middleware. The Launcher (`launcher/`) starts one process for each module of a topology
(`topology.py`, `topologies/`), one for the WarehouseManagementSystem, and the monitor
(`monitor/`), which shows what the graph holds and the refusals the participants' handover event
streams report.

`ontology/flexconveyor_usecase.ttl` declares the classes of this use case.
`shapes/flexconveyor_usecase_shapes.ttl` holds the SHACL shapes that each write must satisfy.
The paper prints one of those shapes.

`vertical_slice.py` is the smallest run that proves every seam: two modules, one box, one
handover, and one fault the triple store refuses. On any other topology, each module chooses the
next module of a box on a shortest path to its destination (`routing.py`).
"""
