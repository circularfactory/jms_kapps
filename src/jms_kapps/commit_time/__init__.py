"""The harness that measures the commit time on use case 2, and its benchmark strategy, the
WMS strategy `roundrobin` (`round_robin.py`).

The demonstration runs without this package: use case 1 and use case 2 import nothing from it, and
`jms-uc2` finds a strategy only through the entry-point groups `jms_kapps.uc2.module_strategies`
and `jms_kapps.uc2.wms_strategies` (`jms_kapps.uc2.strategies`). The benchmark strategies live in the
`commit_time` folder, independent of the demonstration.
"""
