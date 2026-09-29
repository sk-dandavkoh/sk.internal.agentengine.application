"""Trigger config invariants and schedule rules, TriggerContext,
CredentialReference shape, PluginMetadata."""

from datetime import UTC, datetime
from typing import Any

import pytest

from akasha.domain.credentials import CredentialReference
from akasha.domain.plugin import PluginMetadata
from akasha.domain.trigger import (
    MAX_INTERVAL_SECONDS,
    MAX_PAYLOAD_DEPTH,
    Trigger,
    TriggerContext,
    TriggerType,
    schedule_problem,
)
from akasha.errors import DomainValidationError


def nested(depth: int) -> dict[str, Any]:
    """A payload whose dicts nest exactly ``depth`` deep (built iteratively)."""
    root: dict[str, Any] = {}
    node = root
    for _ in range(depth - 1):
        child: dict[str, Any] = {}
        node["n"] = child
        node = child
    return root


class TestTrigger:
    def test_cron_requires_expression(self) -> None:
        with pytest.raises(DomainValidationError, match="cron"):
            Trigger.create(TriggerType.CRON)
        with pytest.raises(DomainValidationError, match="cron"):
            Trigger.create(TriggerType.CRON, {"cron": "  "})
        t = Trigger.create(TriggerType.CRON, {"cron": "0 * * * *"})
        assert t.enabled is True

    def test_interval_requires_positive_finite_seconds(self) -> None:
        bads: list[dict[str, object]] = [
            {},
            {"interval_seconds": 0},
            {"interval_seconds": -5},
            {"interval_seconds": True},
            {"interval_seconds": "60"},
            {"interval_seconds": float("nan")},
            {"interval_seconds": float("inf")},
        ]
        for bad in bads:
            with pytest.raises(DomainValidationError, match="interval"):
                Trigger.create(TriggerType.INTERVAL, bad)
        t = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": 30})
        assert t.trigger_type is TriggerType.INTERVAL

    def test_event_requires_name(self) -> None:
        with pytest.raises(DomainValidationError, match="event"):
            Trigger.create(TriggerType.EVENT)

    @pytest.mark.parametrize(
        "t", [TriggerType.MANUAL, TriggerType.API, TriggerType.WEBHOOK]
    )
    def test_config_free_types(self, t: TriggerType) -> None:
        assert Trigger.create(t).trigger_type is t

    def test_type_coerced_from_string(self) -> None:
        assert Trigger.create("manual").trigger_type is TriggerType.MANUAL  # type: ignore[arg-type]


class TestScheduleProblem:
    """Stricter schedule rules are publish gates, not construction errors:
    every one of these triggers must still construct, so older persisted
    drafts stay loadable."""

    @pytest.mark.parametrize(
        "cron",
        [
            "not a cron",  # garbage
            "61 * * * *",  # minute out of range
            "* * * * * *",  # 6 fields (seconds extension)
            "0 0 32 * *",  # day out of range
            "0 0 31 2 *",  # valid syntax, never fires (Feb 31)
            "0 0 30 2 *",  # never fires
            "R * * * *",  # random: non-deterministic
            "0 R * * *",
            "R(0-5) * * * *",
            "H * * * *",  # hashed
            "H/15 * * * *",
        ],
    )
    def test_unusable_cron_is_a_problem(self, cron: str) -> None:
        trigger = Trigger.create(TriggerType.CRON, {"cron": cron})
        problem = schedule_problem(trigger)
        assert problem is not None
        assert cron not in problem  # issues carry no config values (§35)

    @pytest.mark.parametrize(
        "cron",
        [
            "0 * * * *",
            "*/5 * * * *",
            "0 0 29 2 *",  # fires every leap year
            "0 0 * MAR FRI",  # names containing R/H are not random/hashed
            "0 9 * * THU",
            "0 0 L * *",
            "0 0 * * 5#3",
        ],
    )
    def test_usable_cron_has_no_problem(self, cron: str) -> None:
        trigger = Trigger.create(TriggerType.CRON, {"cron": cron})
        assert schedule_problem(trigger) is None

    @pytest.mark.parametrize(
        ("seconds", "ok"),
        [
            (0.5, False),  # below the scheduler tick floor
            (1, True),
            (30, True),
            (MAX_INTERVAL_SECONDS, True),
            (MAX_INTERVAL_SECONDS + 1, False),
            (1e12, False),  # would overflow datetime arithmetic
            (10**30, False),
        ],
    )
    def test_interval_bounds(self, seconds: float, ok: bool) -> None:
        trigger = Trigger.create(TriggerType.INTERVAL, {"interval_seconds": seconds})
        assert (schedule_problem(trigger) is None) is ok

    @pytest.mark.parametrize(
        "t", [TriggerType.MANUAL, TriggerType.API, TriggerType.WEBHOOK]
    )
    def test_unscheduled_types_have_no_problem(self, t: TriggerType) -> None:
        assert schedule_problem(Trigger.create(t)) is None


class TestTriggerContext:
    def test_defaults(self) -> None:
        ctx = TriggerContext(trigger_type=TriggerType.API, source="api")
        assert ctx.payload == {}
        assert ctx.headers == {}
        assert ctx.metadata == {}
        assert ctx.timestamp.tzinfo is not None

    def test_type_coerced_from_string(self) -> None:
        ctx = TriggerContext(trigger_type="webhook")  # type: ignore[arg-type]
        assert ctx.trigger_type is TriggerType.WEBHOOK

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            TriggerContext(
                trigger_type=TriggerType.MANUAL, timestamp=datetime(2026, 1, 1)
            )

    def test_payload_copied(self) -> None:
        # Top-level copy owns the shape; Execution.create deep-copies the
        # payload it persists, so context-only aliasing cannot leak into a run.
        payload = {"in": 1}
        ctx = TriggerContext(trigger_type=TriggerType.WEBHOOK, payload=payload)
        payload["in"] = 2
        assert ctx.payload == {"in": 1}
        assert ctx.timestamp == ctx.timestamp.astimezone(UTC)

    def test_payload_depth_limit(self) -> None:
        ok = TriggerContext(
            trigger_type=TriggerType.WEBHOOK, payload=nested(MAX_PAYLOAD_DEPTH)
        )
        assert ok.payload
        with pytest.raises(DomainValidationError, match="nested too deeply") as err:
            TriggerContext(
                trigger_type=TriggerType.WEBHOOK, payload=nested(MAX_PAYLOAD_DEPTH + 1)
            )
        assert err.value.details == {"max_depth": MAX_PAYLOAD_DEPTH}

    def test_depth_check_survives_pathological_nesting(self) -> None:
        # Deeper than the recursion limit: the check itself must not overflow
        # (lists count as nesting too).
        deep: list[Any] = []
        for _ in range(20_000):
            deep = [deep]
        with pytest.raises(DomainValidationError, match="nested too deeply"):
            TriggerContext(trigger_type=TriggerType.API, payload={"deep": deep})


class TestCredentialReference:
    def test_valid(self) -> None:
        assert CredentialReference("credential:openai-production").ref

    def test_blank_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="blank"):
            CredentialReference("")

    def test_whitespace_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="whitespace"):
            CredentialReference("cred 123")


class TestPluginMetadata:
    def _valid(self, **overrides: object) -> PluginMetadata:
        base: dict[str, object] = {
            "plugin_id": "community.web_tools",
            "name": "Web Tools",
            "version": "1.2.0",
            "engine_compatibility": ">=0.1.0",
            "sdk_compatibility": ">=0.1.0",
        }
        base.update(overrides)
        return PluginMetadata(**base)  # type: ignore[arg-type]

    def test_valid(self) -> None:
        p = self._valid(dependencies=("community.core",))
        assert p.plugin_id == "community.web_tools"

    @pytest.mark.parametrize(
        "plugin_id", ["", "Bad", "-x", "foo..bar", "foo bar", "1plugin"]
    )
    def test_invalid_id(self, plugin_id: str) -> None:
        with pytest.raises(DomainValidationError, match="plugin id"):
            self._valid(plugin_id=plugin_id)

    @pytest.mark.parametrize("version", ["", "1.0", "abc", "v1.2.3"])
    def test_invalid_version(self, version: str) -> None:
        with pytest.raises(DomainValidationError, match="semver"):
            self._valid(version=version)

    def test_blank_compatibility_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="engine compatibility"):
            self._valid(engine_compatibility=" ")

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="name"):
            self._valid(name="")
