"""Trigger config invariants, CredentialReference shape, PluginMetadata."""

import pytest

from akasha.domain.credentials import CredentialReference
from akasha.domain.plugin import PluginMetadata
from akasha.domain.trigger import Trigger, TriggerType
from akasha.errors import DomainValidationError


class TestTrigger:
    def test_cron_requires_expression(self) -> None:
        with pytest.raises(DomainValidationError, match="cron"):
            Trigger.create(TriggerType.CRON)
        with pytest.raises(DomainValidationError, match="cron"):
            Trigger.create(TriggerType.CRON, {"cron": "  "})
        t = Trigger.create(TriggerType.CRON, {"cron": "0 * * * *"})
        assert t.enabled is True

    def test_interval_requires_positive_seconds(self) -> None:
        bads: list[dict[str, object]] = [
            {},
            {"interval_seconds": 0},
            {"interval_seconds": -5},
            {"interval_seconds": True},
            {"interval_seconds": "60"},
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
