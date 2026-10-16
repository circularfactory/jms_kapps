"""The evaluation of the revised paper: the measurement harness and the benchmark strategies, each
built by a ticket of its own. The benchmark strategy `southward` (`southward.py`) is the first.

The demonstration runs without this package: use case 1 and use case 2 import nothing from it, and
`jms-uc2` finds a strategy only through the entry-point groups `jms_kapps.uc2.module_strategies`
and `jms_kapps.uc2.wms_strategies` (`jms_kapps.uc2.strategies`). The README beside this file says
how a strategy registers there. (Added on 2026-10-10, jms_kapps 52, decided by Etienne on
2026-10-07: the benchmark strategies live in the `evaluation` folder, independent of the
demonstration.)
"""
