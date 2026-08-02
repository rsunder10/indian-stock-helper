"""CLI tests — fully offline. Real domain objects flow through the render helpers; the heavy
entry points (analyze/scan/backtest) are monkeypatched for the `main` dispatch tests."""

from __future__ import annotations

import pytest

from indi_analyst import cli
from indi_analyst.analysis.engine import analyze_snapshot
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.backtest import run_backtest
from indi_analyst.models import CorporateActions, Fundamentals
from indi_analyst.screener import scan_universe
from tests.conftest import MockPriceSource, make_ohlcv


@pytest.fixture
def rec():
    snap = build_snapshot("TEST", price_source=MockPriceSource(make_ohlcv("up")), news_source=None)
    return analyze_snapshot(snap, provider="rulebased")


@pytest.fixture
def scan_result(tmp_path):
    settings = cli.get_settings()
    settings.screener_cache_path = str(tmp_path / "scan.db")
    return scan_universe(
        "watchlist:TEST.NS",
        provider="rulebased",
        settings=settings,
        price_source=MockPriceSource(make_ohlcv("up")),
        news_source=None,
        use_cache=False,
        persist=False,
    )


@pytest.fixture
def bt_result():
    return run_backtest("TEST.NS", price_source=MockPriceSource(make_ohlcv("up", n=300)), limit=1)


# --- small helpers ----------------------------------------------------------


def test_fmt_pct_signs():
    assert cli._fmt_pct(0.123) == "+12.3%"
    assert cli._fmt_pct(-0.05) == "-5.0%"


def test_plain_strips_markdown():
    assert cli._plain("**bold** text") == "bold text"


def test_fundamentals_line_lists_present_fields():
    line = cli._fundamentals_line(Fundamentals(pe_ratio=18.0, roe=0.2, debt_to_equity=0.5))
    assert "P/E 18.0" in line and "ROE 20%" in line and "D/E 0.50" in line


def test_fundamentals_line_empty_when_nothing_present():
    assert cli._fundamentals_line(Fundamentals()) == ""


def test_corporate_actions_lines_none_is_empty():
    assert cli._corporate_actions_lines(None) == []


def test_corporate_actions_lines_dividends():
    ca = CorporateActions(dividend_paying_years=3, lookback_years=5)
    out = cli._corporate_actions_lines(ca)
    assert out and "paid in 3 of last 5" in out[0]


# --- render helpers ---------------------------------------------------------


def test_render_has_core_sections(rec):
    out = cli.render(rec)
    assert "CALL:" in out
    assert "TRADE LEVELS" in out
    assert "THESIS" in out
    assert rec.snapshot.symbol in out
    assert out.strip().endswith(rec.disclaimer.strip()[-20:])


def test_render_scan_tabulates(scan_result):
    rows = scan_result.rows
    out = cli.render_scan(scan_result, rows, top=None)
    assert "SCREEN:" in out
    assert scan_result.universe in out


def test_render_sectors(scan_result):
    from indi_analyst.screener import summarize_sectors

    summaries = summarize_sectors(scan_result.ok_rows())
    out = cli.render_sectors(summaries)
    assert isinstance(out, str)


def test_render_backtest(bt_result):
    out = cli.render_backtest(bt_result, top=5)
    assert isinstance(out, str) and out


# --- _build_filter (via the real parser) ------------------------------------


def _screen_args(*flags):
    return cli.build_parser().parse_args(["screen", *flags])


def test_build_filter_none_when_no_criteria():
    assert cli._build_filter(_screen_args()) is None


def test_build_filter_min_score_and_action():
    flt = cli._build_filter(_screen_args("--min-score", "70", "--action", "BUY,ACCUMULATE"))
    assert flt is not None
    assert flt.min_score == 70
    from indi_analyst.models import Action

    assert flt.actions == {Action.BUY, Action.ACCUMULATE}


def test_build_filter_from_preset():
    flt = cli._build_filter(_screen_args("--preset", "high-conviction-buys"))
    assert flt is not None


# --- main() dispatch --------------------------------------------------------


def test_main_no_args_prints_help(capsys):
    assert cli.main([]) == 2


def test_main_bare_symbol_routes_to_analyze(monkeypatch, rec, capsys):
    monkeypatch.setattr(cli, "analyze", lambda query, provider=None: rec)
    assert cli.main(["RELIANCE"]) == 0
    assert "CALL:" in capsys.readouterr().out


def test_main_analyze_error_returns_1(monkeypatch, capsys):
    def boom(query, provider=None):
        raise ValueError("bad ticker")

    monkeypatch.setattr(cli, "analyze", boom)
    assert cli.main(["analyze", "NOPE"]) == 1
    assert "Error analyzing" in capsys.readouterr().err


def test_main_screen_dispatch(monkeypatch, scan_result, capsys):
    monkeypatch.setattr(cli, "scan_universe", lambda *a, **k: scan_result)
    assert cli.main(["screen", "--universe", "watchlist:TEST.NS"]) == 0
    assert "SCREEN:" in capsys.readouterr().out


def test_main_screen_top_zero_means_all(monkeypatch, scan_result):
    captured = {}

    def fake_scan(*a, **k):
        return scan_result

    def fake_render(result, rows, top):
        captured["top"] = top
        return ""

    monkeypatch.setattr(cli, "scan_universe", fake_scan)
    monkeypatch.setattr(cli, "render_scan", fake_render)
    cli.main(["screen", "--universe", "watchlist:TEST.NS", "--top", "0"])
    assert captured["top"] is None


def test_main_backtest_requires_target(capsys):
    assert cli.main(["backtest"]) == 2
    assert "Provide a symbol" in capsys.readouterr().err


def test_main_backtest_dispatch(monkeypatch, bt_result, capsys):
    monkeypatch.setattr(cli, "run_backtest", lambda *a, **k: bt_result)
    assert cli.main(["backtest", "TEST.NS"]) == 0
