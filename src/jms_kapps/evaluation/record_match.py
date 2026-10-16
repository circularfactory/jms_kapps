"""Which configuration of a spec a record belongs to (jms_kapps 105, interim names).

The report (`report`) and the configurations table (`table`) both match a run to a configuration
here. A run matches in one of two ways:

- by its key, which is the configuration's key (`SpecConfiguration.key`); or
- by the legacy key, for a record of the "before" series, which was keyed before the transit time
  and the retry wait joined the key (jms_kapps 93). Such a record matches the configuration whose
  legacy key (`spec.legacy_key`) is its key, when its `run_settings` hold the configuration's
  transit time and retry wait and its sweep names the spec being read, by file name. Another
  transit time, another retry wait, no run settings or another spec: no match.

A run that matches neither way is of no configuration of the spec. The readers only read.
"""

from __future__ import annotations

from collections.abc import Sequence

from jms_kapps.evaluation.spec import SpecConfiguration, legacy_key
from jms_kapps.evaluation.summary import RunSummary


class ConfigurationIndex:
    """The configurations `found` of one spec, to match runs to them."""

    def __init__(self, found: Sequence[SpecConfiguration]) -> None:
        self._by_key = {configuration.key: configuration for configuration in found}
        self._by_legacy_key: dict[str, list[SpecConfiguration]] = {}
        for configuration in found:
            self._by_legacy_key.setdefault(legacy_key(configuration), []).append(
                configuration
            )

    def of(self, run: RunSummary) -> SpecConfiguration | None:
        """The configuration that `run` is a run of, or None where it is of none."""
        current = self._by_key.get(run.key or "")
        if current is not None:
            return current
        for configuration in self._by_legacy_key.get(run.key or "", ()):
            if (
                run.spec_file == configuration.spec_file
                and run.transit_seconds == configuration.values["transit_seconds"]
                and run.retry_seconds == configuration.values["retry_seconds"]
            ):
                return configuration
        return None
