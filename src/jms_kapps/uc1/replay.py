"""The frame replay: a frame made from a recording, in place of the frame that the robot records.

The replay reads a recording and stamps it as if the robot recorded it now, or at the time the
caller gives. The screwing resource writes the result like any other frame. A frame that the
time-series store already holds does not need the replay: the reader gives the same `Frame` back.

**The only recordings so far are legacy mock data.** `success.csv` and `missing_screw.csv` in
`data/unscrewing_timeseries/` come from the paper's artifact, commit `d320118`, without a change.
They are not measurements:

- Their units are not known. The replay copies the values without a conversion.
- Their `Time` column is taken to be in seconds. That is an assumption.
- Their phases come from their last column, `comment`, which a person wrote by hand. The replay
  maps each of its labels to a phase in `PHASE_OF_LABEL`. A label that names no phase gives no
  phase. The one label it maps against its wording is the touching sample's: the labels call it
  form-fit, and the replay gives it to the Approach, where the decision tree finds contact. So the
  phases of a mock frame are mock as well: a person's labels, not phase boundaries that the robot
  recorded.

Each frame that the replay makes from them says so in its `source`, and the store keeps that tag.
"""

from __future__ import annotations

import csv
import io
import time

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame

# The recordings that ship with this package. Both are legacy mock data.
MOCK_RECORDINGS = ("success", "missing_screw")

# What a replayed mock frame carries as its source, before the recording's name.
MOCK_SOURCE = "legacy-mock-d320118"

# The columns of a mock recording, and the signal each becomes.
_COLUMNS = {
    "RobotPosition": ROBOT_POSITION,
    "AxialForce": AXIAL_FORCE,
    "UnscrewingTorque": UNSCREWING_TORQUE,
}

# The phase that each label of a mock recording's `comment` column names, for every label that
# the two recordings hold.
#
# The sample where the tool reaches the screw head is the Approach's, though its label says
# form-fit: the decision tree finds contact in the Approach, as its last sample, and the Form-fit
# starts after it. That holds for the missing screw too, where the tool reaches the head's position
# and touches nothing ("Nope"). Its two samples after that, as the tool goes on down, keep no phase.
PHASE_OF_LABEL: dict[str, str | None] = {
    "approach": "Approach",
    "form-fit - touches screw head": "Approach",
    "form-fit - touches screw head --> Nope": "Approach",
    "form-fit - tightening": "Form-fit",
    "preparation- unscrewing": "Preparation",
    "disassembly - unscrewing": "Disassembly",
    # The screw is out: the torque and the force are zero, and the robot moves back. Retracting
    # is not one of the four phases.
    "disassembly complete - retract": None,
    "retract": None,
    "retract complete": None,
    "": None,
    "Status: Missing screw": None,
}


def mock_frame(name: str, *, start: int | None = None) -> Frame:
    """The legacy mock recording `name`, as a frame whose first sample is at `start`.

    Args:
        name: One of `MOCK_RECORDINGS`.
        start: The time of the first sample, in nanoseconds since the Unix epoch. Defaults to now.

    Raises:
        ValueError: If the package ships no recording of that name, or if the recording has a
            label that `PHASE_OF_LABEL` does not map.
    """
    if name not in MOCK_RECORDINGS:
        raise ValueError(
            f"no mock recording {name!r}: the replay has {', '.join(MOCK_RECORDINGS)}"
        )
    text = read_asset("jms_kapps.uc1", "data", "unscrewing_timeseries", f"{name}.csv")
    rows = [
        row for row in csv.DictReader(io.StringIO(text), skipinitialspace=True) if row
    ]
    first = time.time_ns() if start is None else start
    return Frame(
        times=[first + round(float(row["Time"]) * 1e9) for row in rows],
        signals={
            signal: [float(row[column]) for row in rows]
            for column, signal in _COLUMNS.items()
        },
        phases=[_phase(name, row["comment"]) for row in rows],
        source=f"{MOCK_SOURCE}/{name}",
    )


def _phase(name: str, label: str) -> str | None:
    label = label.strip()
    if label not in PHASE_OF_LABEL:
        raise ValueError(
            f"the mock recording {name!r} has the label {label!r}, which names no known phase"
        )
    return PHASE_OF_LABEL[label]
