"""LLM layer — fully offline. JSON parsing, prompt serialization, provider selection, and the
Ollama HTTP path with a monkeypatched transport (no network, no live models)."""

from __future__ import annotations

import importlib.util
import json

import httpx
import pytest

from indi_analyst.analysis.levels import compute_levels
from indi_analyst.analysis.scoring import score
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.analysis.valuation import compute_valuation
from indi_analyst.config import Settings
from indi_analyst.llm import ollama_provider
from indi_analyst.llm.base import ProviderError
from indi_analyst.llm.factory import build_provider, build_provider_with_fallback
from indi_analyst.llm.parsing import parse_verdict
from indi_analyst.llm.prompts import SYSTEM_PROMPT, serialize
from indi_analyst.llm.rulebased import RuleBasedProvider
from indi_analyst.models import AnalystVerdict, Conviction, SectorMacroSignal
from tests.conftest import MockPriceSource, make_ohlcv

# A well-formed verdict payload the schema accepts.
VALID_JSON = json.dumps(
    {
        "thesis": ["Trades above its 200-DMA", "RSI not yet overbought"],
        "conviction": "HIGH",
        "action_agrees": True,
        "suggested_action": "BUY",
        "key_risks": ["Sector rotation"],
        "catalysts": ["Q3 results"],
        "summary": "Constructive setup with a favorable risk/reward.",
    }
)


def _pipeline(settings: Settings | None = None, *, with_macro: bool = False):
    """Build (snapshot, levels, quant, valuation) offline from the mock source."""
    settings = settings or Settings()
    snap = build_snapshot(
        "TEST", settings=settings, price_source=MockPriceSource(make_ohlcv("up")), news_source=None
    )
    if with_macro:
        snap.macro_signals = [
            SectorMacroSignal(
                kind="budget",
                label="Union Budget",
                sector="Testing",
                tailwind=0.5,
                drivers=["Allocation up 20% YoY"],
            )
        ]
    levels = compute_levels(snap, settings)
    quant = score(snap, settings)
    valuation = compute_valuation(snap, settings)
    return snap, levels, quant, valuation


# --- parse_verdict ----------------------------------------------------------


def test_parse_verdict_plain_json():
    v = parse_verdict(VALID_JSON)
    assert isinstance(v, AnalystVerdict)
    assert v.conviction is Conviction.HIGH
    assert v.thesis


def test_parse_verdict_strips_json_fence():
    v = parse_verdict(f"```json\n{VALID_JSON}\n```")
    assert v.conviction is Conviction.HIGH


def test_parse_verdict_strips_bare_fence():
    v = parse_verdict(f"```\n{VALID_JSON}\n```")
    assert v.conviction is Conviction.HIGH


def test_parse_verdict_ignores_surrounding_prose():
    v = parse_verdict(f"Here is my analysis:\n{VALID_JSON}\nHope that helps!")
    assert v.action_agrees is True


def test_parse_verdict_no_object_raises():
    with pytest.raises(ProviderError, match="No JSON object"):
        parse_verdict("I could not analyze this stock.")


def test_parse_verdict_unbalanced_raises():
    with pytest.raises(ProviderError, match="Unbalanced JSON"):
        parse_verdict('{"thesis": ["a", "b"]')


def test_parse_verdict_invalid_json_raises():
    with pytest.raises(ProviderError, match="invalid JSON"):
        parse_verdict("{not valid json,}")


def test_parse_verdict_schema_mismatch_raises():
    with pytest.raises(ProviderError, match="did not match"):
        parse_verdict('{"conviction": "SUPER-HIGH"}')


# --- serialize (prompts) ----------------------------------------------------


def test_serialize_is_json_after_prefix():
    snap, levels, quant, valuation = _pipeline()
    out = serialize(snap, levels, quant, valuation)
    assert out.startswith("Analyze this stock")
    payload = json.loads(out.split("\n\n", 1)[1])
    assert payload["stock"]["symbol"] == snap.symbol
    assert "trade_levels" in payload
    assert "macro_overlays" not in payload  # none injected


def test_serialize_includes_macro_and_fair_value():
    snap, levels, quant, valuation = _pipeline(with_macro=True)
    out = serialize(snap, levels, quant, valuation)
    payload = json.loads(out.split("\n\n", 1)[1])
    assert payload["macro_overlays"][0]["label"] == "Union Budget"
    if valuation.fair_value is not None:
        assert "fair_value" in payload


def test_serialize_without_valuation_omits_fair_value():
    snap, levels, quant, _ = _pipeline()
    out = serialize(snap, levels, quant, None)
    payload = json.loads(out.split("\n\n", 1)[1])
    assert "fair_value" not in payload


def test_system_prompt_demands_json_only():
    assert "JSON" in SYSTEM_PROMPT


# --- factory ----------------------------------------------------------------


def test_build_provider_rulebased():
    assert isinstance(build_provider("rulebased"), RuleBasedProvider)


def test_build_provider_unknown_raises():
    with pytest.raises(ProviderError, match="Unknown provider"):
        build_provider("nope-9000", Settings())


def test_build_provider_ollama_constructs_without_network():
    prov = build_provider("ollama", Settings(ollama_base_url="http://localhost:11434/"))
    assert prov.name == "ollama"
    assert prov.base_url == "http://localhost:11434"  # trailing slash trimmed


@pytest.mark.parametrize(
    ("name", "settings"),
    [
        ("anthropic", Settings(anthropic_api_key=None)),
        ("openai", Settings(openai_api_key=None)),
        ("gemini", Settings(gemini_api_key=None)),
    ],
)
def test_cloud_provider_without_key_raises(name, settings):
    with pytest.raises(ProviderError, match="not set"):
        build_provider(name, settings)


@pytest.mark.parametrize(
    ("name", "module", "settings"),
    [
        ("anthropic", "anthropic", Settings(anthropic_api_key="fake-key")),
        ("openai", "openai", Settings(openai_api_key="fake-key")),
    ],
)
def test_cloud_provider_missing_sdk_raises(name, module, settings):
    if importlib.util.find_spec(module) is not None:
        pytest.skip(f"{module} SDK is installed; the missing-SDK branch is not exercised here")
    with pytest.raises(ProviderError, match="not installed"):
        build_provider(name, settings)


def test_fallback_rulebased_has_no_note():
    prov, note = build_provider_with_fallback("rulebased", Settings())
    assert isinstance(prov, RuleBasedProvider)
    assert note is None


def test_fallback_on_unavailable_provider_returns_note():
    prov, note = build_provider_with_fallback("anthropic", Settings(anthropic_api_key=None))
    assert isinstance(prov, RuleBasedProvider)
    assert note is not None and "anthropic" in note


# --- Ollama provider (monkeypatched httpx) ----------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_ollama_verdict_parses_model_json(monkeypatch):
    snap, levels, quant, valuation = _pipeline()

    def fake_post(url, json, timeout):
        assert url.endswith("/api/chat")
        return _FakeResponse({"message": {"content": VALID_JSON}})

    monkeypatch.setattr(ollama_provider.httpx, "post", fake_post)
    prov = build_provider("ollama", Settings())
    v = prov.verdict(snap, levels, quant, valuation)
    assert v.conviction is Conviction.HIGH


def test_ollama_empty_response_raises(monkeypatch):
    snap, levels, quant, valuation = _pipeline()

    def fake_post(url, json, timeout):
        return _FakeResponse({"message": {"content": ""}})

    monkeypatch.setattr(ollama_provider.httpx, "post", fake_post)
    prov = build_provider("ollama", Settings())
    with pytest.raises(ProviderError, match="empty response"):
        prov.verdict(snap, levels, quant, valuation)


def test_ollama_connect_error_is_actionable(monkeypatch):
    snap, levels, quant, valuation = _pipeline()

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama_provider.httpx, "post", fake_post)
    prov = build_provider("ollama", Settings())
    with pytest.raises(ProviderError, match="Cannot reach Ollama"):
        prov.verdict(snap, levels, quant, valuation)


def test_ollama_http_error_raises(monkeypatch):
    snap, levels, quant, valuation = _pipeline()

    def fake_post(url, json, timeout):
        raise httpx.HTTPError("500")

    monkeypatch.setattr(ollama_provider.httpx, "post", fake_post)
    prov = build_provider("ollama", Settings())
    with pytest.raises(ProviderError, match="request failed"):
        prov.verdict(snap, levels, quant, valuation)
