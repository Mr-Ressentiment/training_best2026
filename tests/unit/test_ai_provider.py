"""T015: validate the AI boundary with controlled, credential-free HTTP fakes.

Constructor keywords, extract arguments, and provider_name/model_name metadata
are centralized below as reviewable T017 test seams, not new public API fields.
Only the extraction body is AIExtractionPayload; replay metadata stays outside it.
"""

import asyncio
from copy import deepcopy
import json
import time
from unittest.mock import Mock

import httpx
import pytest

from app.config import Settings
from app.models.schemas import AIExtractionPayload


MODELS = {"qwen": "qwen3.8-flash", "gemini": "gemini-3.5-flash-lite"}
CATALOG = [
    {"sku": "SKU-WRAP-18", "name": "Industrial Stretch Film 18in 80ga"},
    {"sku": "SKU-WRAP-15", "name": "Standard Pallet Wrap 15in 65ga"},
]


@pytest.fixture(autouse=True)
def controlled_io(no_external_network, monkeypatch):
    """Immediate-error tests must not secretly spend time on retry/backoff."""
    def forbidden_sleep(*args, **kwargs):
        raise AssertionError("Provider tests must not wait or retry with backoff")

    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)


def _live_provider(module, provider_name, client, *, timeout=15.0):
    """Small injectable HTTP client seam; no key or external SDK is needed."""
    settings = Settings(
        llm_provider=provider_name, llm_api_key="", database_url="sqlite:///:memory:",
        live_inference_timeout=timeout,
    )
    return module.LiveAIProvider(settings=settings, client=client)


def _fixture_provider(module, fixture_id, directory):
    return module.FixtureAIProvider(fixture_id=fixture_id, fixtures_dir=directory)


def _extract(provider, raw_text):
    return provider.extract(raw_text=raw_text, catalog=deepcopy(CATALOG))


def _assert_identity(provider, name, model, *, replay):
    assert provider.provider_name == name
    assert provider.model_name == model
    assert provider.is_replay_mode is replay


class ControlledTransport:
    """Spy on the configured model request; answer without using the network.

    Response envelopes are confined here so a transport adaptation does not
    change the behavioral contract. Unexpected provider/model calls fail closed.
    """

    def __init__(self, extraction_text, *, failure=None):
        self.extraction_text = extraction_text
        self.failure = failure
        self.calls = {"qwen": 0, "gemini": 0}
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        model = body.get("model", "")
        if model == MODELS["qwen"]:
            name = "qwen"
        elif model == MODELS["gemini"] or MODELS["gemini"] in request.url.path:
            name = "gemini"
        else:
            raise AssertionError(f"Unexpected live model in controlled request: {model} {request.url.path}")
        self.calls[name] += 1
        self.requests.append(request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout("Controlled stalled inference at client deadline", request=request)
        if self.failure == "connection":
            raise httpx.ConnectError("Controlled connection refusal", request=request)
        if isinstance(self.failure, int):
            return httpx.Response(self.failure, json={"error": {"message": "Controlled upstream failure"}})
        if name == "qwen":
            response = {"choices": [{"message": {"content": self.extraction_text}}]}
        else:
            response = {"candidates": [{"content": {"parts": [{"text": self.extraction_text}]}}]}
        return httpx.Response(200, json=response)


def _fixture_spy(module, monkeypatch):
    spy = Mock(side_effect=AssertionError("Failed live inference must never substitute fixture extraction"))
    monkeypatch.setattr(module.FixtureAIProvider, "extract", spy)
    return spy


def _assert_single_configured_call(transport, provider_name, fixture_spy):
    assert transport.calls[provider_name] == 1
    other = "gemini" if provider_name == "qwen" else "qwen"
    assert transport.calls[other] == 0
    fixture_spy.assert_not_called()


def test_accepted_boundary_symbols_exist(require_ai_provider):
    module = require_ai_provider()
    for name in (
        "OrderShieldAIProvider", "LiveAIProvider", "FixtureAIProvider",
        "AIProviderUnavailableError", "AIOutputValidationError",
    ):
        assert hasattr(module, name), f"T017 accepted boundary is missing {name}"


@pytest.mark.parametrize("provider_name", MODELS)
def test_valid_live_json_is_validated_and_keeps_exact_provider_identity(
    require_ai_provider, clean_acme_fixture, po_clean_acme_text, provider_name, monkeypatch,
):
    expected = AIExtractionPayload.model_validate(clean_acme_fixture["extraction"])
    module = require_ai_provider()
    fixture_spy = _fixture_spy(module, monkeypatch)
    transport = ControlledTransport(expected.model_dump_json())
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False) as client:
        provider = _live_provider(module, provider_name, client)
        result = _extract(provider, po_clean_acme_text)

    assert isinstance(result, AIExtractionPayload)
    assert result == expected
    _assert_identity(provider, provider_name, MODELS[provider_name], replay=False)
    _assert_single_configured_call(transport, provider_name, fixture_spy)


@pytest.mark.parametrize("provider_name", MODELS)
@pytest.mark.parametrize("raw_output", ['{"unexpected_field": 123}', "{not valid JSON"], ids=["invalid-schema", "invalid-json"])
def test_untrusted_output_is_rejected_without_fabricated_payload(
    require_ai_provider, po_clean_acme_text, provider_name, raw_output, monkeypatch,
):
    module = require_ai_provider()
    fixture_spy = _fixture_spy(module, monkeypatch)
    transport = ControlledTransport(raw_output)
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False) as client:
        provider = _live_provider(module, provider_name, client)
        with pytest.raises(module.AIOutputValidationError) as error:
            _extract(provider, po_clean_acme_text)

    assert str(error.value).strip()
    _assert_single_configured_call(transport, provider_name, fixture_spy)


@pytest.mark.parametrize("provider_name", MODELS)
def test_stalled_transport_has_bounded_client_deadline_and_explicit_failure(
    require_ai_provider, po_clean_acme_text, provider_name, monkeypatch,
):
    module = require_ai_provider()
    fixture_spy = _fixture_spy(module, monkeypatch)
    transport = ControlledTransport("unused", failure="timeout")
    # No default client timeout: the application must set its own bounded budget.
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False) as client:
        provider = _live_provider(module, provider_name, client)
        with pytest.raises(module.AIProviderUnavailableError) as error:
            _extract(provider, po_clean_acme_text)

    assert str(error.value).strip()
    _assert_single_configured_call(transport, provider_name, fixture_spy)
    timeouts = transport.requests[0].extensions.get("timeout")
    assert timeouts, "The provider must configure a client inference deadline"
    assert all(value is not None and 0 < value <= 15.0 for value in timeouts.values())


@pytest.mark.parametrize("provider_name", MODELS)
@pytest.mark.parametrize("failure", [401, "connection", 429, 503], ids=["auth", "connection-refused", "quota", "upstream"])
def test_immediate_failures_do_not_retry_fail_over_or_substitute_fixtures(
    require_ai_provider, po_clean_acme_text, provider_name, failure, monkeypatch,
):
    module = require_ai_provider()
    fixture_spy = _fixture_spy(module, monkeypatch)
    transport = ControlledTransport("unused", failure=failure)
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=None, trust_env=False) as client:
        provider = _live_provider(module, provider_name, client)
        with pytest.raises(module.AIProviderUnavailableError) as error:
            _extract(provider, po_clean_acme_text)

    assert str(error.value).strip()
    _assert_single_configured_call(transport, provider_name, fixture_spy)


@pytest.mark.parametrize("fixture_name", ["clean_acme", "discrepancy_apex", "ambiguous_apex"])
def test_committed_fixture_extraction_is_validated_and_explicitly_nonlive(
    require_ai_provider, app_fixtures_dir, fixtures_dir, fixture_name,
):
    dataset = json.loads((app_fixtures_dir / f"{fixture_name}.json").read_text(encoding="utf-8"))
    expected = AIExtractionPayload.model_validate(dataset["extraction"])
    raw_text = (fixtures_dir / dataset["document_filename"]).read_text(encoding="utf-8")
    module = require_ai_provider()
    provider = _fixture_provider(module, dataset["fixture_id"], app_fixtures_dir)

    result = _extract(provider, raw_text)

    assert isinstance(result, AIExtractionPayload)
    assert result == expected
    _assert_identity(provider, "fixture", "pre-verified-dataset", replay=True)


def test_fixture_provider_also_rejects_schema_invalid_extraction(
    require_ai_provider, tmp_path, clean_acme_fixture, po_clean_acme_text,
):
    dataset = deepcopy(clean_acme_fixture)
    dataset["extraction"] = {"unexpected_field": 123}
    (tmp_path / "clean_acme.json").write_text(json.dumps(dataset), encoding="utf-8")
    module = require_ai_provider()

    with pytest.raises(module.AIOutputValidationError):
        provider = _fixture_provider(module, dataset["fixture_id"], tmp_path)
        _extract(provider, po_clean_acme_text)
