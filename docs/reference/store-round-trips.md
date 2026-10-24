# The triple store round trips of use case 2

What one spawn, one hop and one journey's end cost in reads and writes of the triple store, once
every module knows the next module's BoxTransportCapability and the WarehouseManagementSystem (the
two are read once at start; neither changes, and the topology and the WMS are seeded before any
module starts). These are the round trips that the measurement harness measures. A peer's workflow
endpoint is read at every call, because a restarted peer has a new address.

## Per module that carries a box on

- At the origin: the intake's read of the transport.
- At every later module: its fetch of the box with its transport, after its Receive.
- At a module that is not the destination: one read of every connection of the station. A module
  reads every connection at every decision, in one read, and its module strategy names the next
  module in memory.

## Per hop, from the giving module G to the receiving module R

- The lookup of R's Reserve (G).
- R's read of what it holds, in Reserve.
- Where R is not the destination, R's look-ahead: its read of every connection, the lookup of the
  Reserve of its next module N (R), and N's read of what it holds.
- The handover, which is one write, after the semantic middleware's own reads: its two
  preconditions, R's possession state, and the read of each data object it updates by the
  Object–Graph Mapper (OGM).
- The lookup of R's Receive (G).
- R's fetch of the box with its transport (above).

Each Reserve that R refuses with a refusal that passes, and that G asks again after the retry time,
adds the lookup of R's Reserve and R's read of what it holds, R's look-ahead where R was free, and
the same for G's detour, if it has one. A Reserve that R refuses as `behind` adds R's fetch of the
transport of each box ahead, until one still waits. A reservation whose box has not arrived at its
bound adds R's read of what it holds and, where the box is R's, its fetch of the box with its
transport and the write of the transport `failed`. So does a reservation that R still holds at its
stop, after its wait for the Receive. A neighbour that G cannot reach, and leaves out, costs the
lookup of its Reserve, or its failed call; G's choice again reads nothing, since it decides on the
connections read for the decision, and its new choice adds the round trips of a Reserve above. The
same holds for R at its look-ahead. A deadlock alarm adds none: it goes to the module's output and
its handover event stream, not to the triple store.

## The spawn, from the WMS to the origin O

- The WMS's reads come before: every module with its types and every connection, in one read; and,
  where it picks the origin, what each module it may pick holds (the entry modules, or every module
  where the topology names none). None of them grows with the boxes in the knowledge graph: the WMS
  names the box by a new UUID. It makes them once more where another spawn left the flight after
  they began.
- The lookup of O's Reserve (WMS).
- O's read of what it holds, in Reserve, and O's look-ahead, as per hop.
- The create of the box on O with its transport, one write, after the read of O's possession state.
- The notification of O, after the semantic middleware's read of O's live service.
- O's intake's read of the transport, counted per module above.

A Reserve that O refuses ends the spawn with nothing written. A reservation of O whose notification
has not come at its bound adds O's read of what it holds and, where the box is O's, its fetch of the
box with its transport.

## The end

The destination's handover back is one write, with the transport `done`, after the same reads of the
semantic middleware, then the notification of the WMS. The WMS's handler adds one `ASK` whether it
holds the transport's box for a `done` transport, and none for a `failed` one.

## What the counts cover

The counts are those of the use case's own triple store traffic, as the code makes it. The
measurement harness's own reads (the completion matcher, the server watch) are described in the
harness's README and are not in these counts.
