"""Optional sampling parameters are validated and omitted unless explicitly selected."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wheel_team import models
from wheel_team.config import ModelConfig, TeamConfig


@pytest.mark.parametrize("value", [-0.1, 2.1, float("nan"), float("inf"), -float("inf")])
def test_temperature_fields_reject_out_of_range_or_nonfinite(value):
    with pytest.raises(ValidationError):
        ModelConfig(model_id="offline", temperature=value)
    with pytest.raises(ValidationError):
        TeamConfig(model=ModelConfig(model_id="offline"), explorer_temperature=value)


@pytest.mark.parametrize("value", [None, 0.0, 1.7, 2.0])
def test_temperature_fields_accept_explicit_values_without_changing_defaults(value):
    model = ModelConfig(model_id="offline", temperature=value)
    config = TeamConfig(model=model, explorer_temperature=value)
    assert config.model.temperature == value
    assert config.explorer_temperature == value
    assert ModelConfig(model_id="offline").temperature is None
    assert TeamConfig(model=ModelConfig(model_id="offline")).explorer_temperature is None


@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("temperature", [None, 0.0, 1.7])
def test_model_constructor_emits_temperature_only_when_selected(monkeypatch, observed, temperature):
    seen = {}
    client = object()
    monkeypatch.setenv("WHEEL_SAMPLING_TEST_KEY", "offline-constructor-fixture")
    monkeypatch.setattr(models.httpx, "AsyncClient", lambda **kwargs: object())
    monkeypatch.setattr(models, "AsyncOpenAI", lambda **kwargs: client)

    def constructor(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(models, "InferenceOpenAIModel", constructor)
    monkeypatch.setattr(models, "ObservedOpenAIModel", constructor)
    config = ModelConfig(
        provider="openai",
        model_id="offline",
        endpoint="http://fixture.invalid/v1",
        api_key_env="WHEEL_SAMPLING_TEST_KEY",
        allow_inference_network=True,
        temperature=temperature,
    )
    _, owned_client = models.build_model(config, telemetry=object() if observed else None)
    assert owned_client is client
    expected = {"max_tokens": 4096}
    if temperature is not None:
        expected["temperature"] = temperature
    assert seen["params"] == expected
