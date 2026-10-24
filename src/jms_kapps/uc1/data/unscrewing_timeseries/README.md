# Example recordings

The two recordings in this folder, `success.csv` and `missing_screw.csv`, are example data: two
example recordings of an unscrewing attempt, to try the anomaly detector against, not training
data. They come unchanged from the paper's first artifact, and use case 1 replays them in place of
the robot (`jms_kapps.uc1.replay`). Each row is one sample of the
robot's position, the axial force and the unscrewing torque, with a comment that names the phase
of the unscrewing. The replay assumes the ontology's units for them (newton, newton metre,
millimetre, second), as an assumption of the demo, not as a property of the files.

The numbers do not describe the behaviour of a real screw or a real robot.
