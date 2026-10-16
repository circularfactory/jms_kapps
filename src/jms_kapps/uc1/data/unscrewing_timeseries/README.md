# Legacy mock data

The two recordings in this folder, `success.csv` and `missing_screw.csv`, are **legacy mock
data**. They are not measurements of a real unscrewing. They come unchanged from the paper's first
artifact, and use case 1 replays them in place of the robot (`jms_kapps.uc1.replay`). Each row is one sample of the robot's position, the axial force and
the unscrewing torque, with a comment that names the phase of the unscrewing.

Do not read the numbers as the behaviour of a real screw or a real robot. A later release replaces
the two files with purpose-built example data.
