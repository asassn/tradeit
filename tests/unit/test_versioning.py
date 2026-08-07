from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tradeit.core.enums import ArtifactKind
from tradeit.errors import ConfigError
from tradeit.reproducibility.versioning import (
    ArtifactVersion,
    RunManifest,
    canonical_json,
    content_hash,
)

UTC = dt.UTC


class TestCanonicalisation:
    def test_key_order_does_not_change_the_hash(self):
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

    def test_numerically_equal_decimals_written_differently_still_differ(self):
        """Decimal("0.30") and Decimal("0.3") are equal but not identical text.

        They hash differently, and that is the correct behaviour: the canonical
        form preserves the precision the author wrote, so a config edit that
        changes only formatting is still visible as a change rather than
        silently collapsing.
        """
        assert content_hash({"x": Decimal("0.30")}) != content_hash({"x": Decimal("0.3")})

    def test_float_and_decimal_are_distinguished(self):
        assert content_hash({"x": Decimal("0.3")}) != content_hash({"x": 0.3})

    def test_a_changed_value_changes_the_hash(self):
        assert content_hash({"threshold": 0.6}) != content_hash({"threshold": 0.61})

    def test_nested_structures_are_stable(self):
        left = {"outer": {"b": [1, 2], "a": {"z": 1, "y": 2}}}
        right = {"outer": {"a": {"y": 2, "z": 1}, "b": [1, 2]}}
        assert content_hash(left) == content_hash(right)

    def test_sets_are_ordered_before_hashing(self):
        assert content_hash({"s": {"b", "a"}}) == content_hash({"s": {"a", "b"}})

    def test_unserialisable_values_are_refused_not_coerced(self):
        """Silently stringifying an unknown type would make two different
        objects hash identically."""
        with pytest.raises(ConfigError, match="not canonically serialisable"):
            canonical_json({"fn": object()})

    def test_dates_are_rendered_isoformat(self):
        assert "2024-03-08" in canonical_json({"d": dt.date(2024, 3, 8)})


class TestArtifactVersion:
    def _artifact(self, **payload) -> ArtifactVersion:
        return ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "baseline", payload or {"a": 1})

    def test_identity_is_content_not_name(self):
        a = ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "alpha", {"x": 1})
        b = ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "renamed", {"x": 1})
        assert a.matches(b), "renaming a strategy must not make it a different strategy"

    def test_different_kinds_never_match(self):
        a = ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "x", {"v": 1})
        b = ArtifactVersion.of(ArtifactKind.MODEL, "x", {"v": 1})
        assert not a.matches(b)

    def test_label_is_stable_and_readable(self):
        artifact = self._artifact()
        assert artifact.label == f"baseline@{artifact.digest[:12]}"

    def test_creation_time_does_not_affect_identity(self):
        early = ArtifactVersion.of(
            ArtifactKind.MODEL, "m", {"v": 1}, dt.datetime(2024, 1, 1, tzinfo=UTC)
        )
        late = ArtifactVersion.of(
            ArtifactKind.MODEL, "m", {"v": 1}, dt.datetime(2025, 1, 1, tzinfo=UTC)
        )
        assert early.digest == late.digest


class TestRunManifest:
    def _manifest(self, **overrides) -> RunManifest:
        payload = {
            "run_id": "scan-2024-03-08",
            "as_of": dt.datetime(2024, 3, 8, 21, 30, tzinfo=UTC),
            "strategy_config": ArtifactVersion.of(
                ArtifactKind.STRATEGY_CONFIG, "baseline", {"risk": 0.005}
            ),
            "data_snapshot": ArtifactVersion.of(
                ArtifactKind.DATA_SNAPSHOT, "eod", {"ohlcv_bars": 4021}
            ),
            "feature_set": ArtifactVersion.of(ArtifactKind.FEATURE_SET, "v1", {"sma": [50, 200]}),
            "model": None,
            "code_version": "0.2.0",
            "created_at": dt.datetime(2024, 3, 8, 21, 35, tzinfo=UTC),
        }
        payload.update(overrides)
        return RunManifest(**payload)

    def test_naive_as_of_is_rejected(self):
        with pytest.raises(ConfigError, match="timezone-aware"):
            self._manifest(as_of=dt.datetime(2024, 3, 8, 21, 30))

    def test_artifact_kinds_are_enforced_by_slot(self):
        """Swapping a model artifact into the config slot is a silent
        reproducibility hole; it is rejected instead."""
        wrong = ArtifactVersion.of(ArtifactKind.MODEL, "xgb", {"depth": 6})
        with pytest.raises(ConfigError, match="strategy_config must be"):
            self._manifest(strategy_config=wrong)

    def test_two_runs_with_identical_context_reproduce_each_other(self):
        first = self._manifest(run_id="scan-a")
        second = self._manifest(run_id="scan-b", created_at=dt.datetime(2025, 1, 1, tzinfo=UTC))
        assert first.reproduces(second)

    def test_a_config_change_breaks_reproducibility(self):
        changed = ArtifactVersion.of(ArtifactKind.STRATEGY_CONFIG, "baseline", {"risk": 0.010})
        assert not self._manifest().reproduces(self._manifest(strategy_config=changed))

    def test_a_code_change_breaks_reproducibility(self):
        """Same config, same data, different code is not the same run."""
        assert not self._manifest().reproduces(self._manifest(code_version="0.3.0"))

    def test_a_different_as_of_breaks_reproducibility(self):
        other = self._manifest(as_of=dt.datetime(2024, 3, 9, 21, 30, tzinfo=UTC))
        assert not self._manifest().reproduces(other)

    def test_describe_exposes_every_pinned_component(self):
        described = self._manifest().describe()
        assert set(described) == {
            "run_id",
            "as_of",
            "manifest_digest",
            "strategy_config",
            "data_snapshot",
            "feature_set",
            "model",
            "code_version",
        }
        assert described["model"] is None
