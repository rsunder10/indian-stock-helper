"""Backtester tests — offline, synthetic frames, no network.

The critical one is `test_no_look_ahead`: it proves that a point-in-time snapshot is unaffected by
future bars, which is the whole correctness claim of the harness.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from indi_analyst.backtest.engine import run_backtest
from indi_analyst.backtest.metrics import aggregate, compute_stats
from indi_analyst.backtest.models import SymbolResult, Trade
from indi_analyst.backtest.replay import snapshot_at
from indi_analyst.backtest.simulator import parse_entry_actions, resolve_exit, simulate_symbol
from indi_analyst.config import Settings
from indi_analyst.models import Action, Conviction
from tests.conftest import make_ohlcv


# --- replay: no look-ahead -------------------------------------------------------------------

def test_no_look_ahead():
    """snapshot_at(df, i) must not change when future bars are appended to df."""
    df = make_ohlcv("up", n=250)
    i = 200

    snap_now = snapshot_at(df, i, "TEST")
    df_extended = pd.concat([df, make_ohlcv("down", n=50, start=float(df["Close"].iloc[-1]))])
    snap_later = snapshot_at(df_extended, i, "TEST")

    assert snap_now.technicals.model_dump() == snap_later.technicals.model_dump()
    assert snap_now.data_as_of == snap_later.data_as_of


def test_snapshot_is_technical_only():
    df = make_ohlcv("up", n=220)
    snap = snapshot_at(df, 210, "TEST")
    # Fundamentals/news are deliberately empty (no point-in-time source).
    assert snap.fundamentals.pe_ratio is None
    assert snap.news == []
    assert snap.news_sentiment is None
    assert snap.technicals.last_close == pytest.approx(float(df["Close"].iloc[210]))


# --- simulator: exit resolution --------------------------------------------------------------

def _series(values) -> pd.Series:
    return pd.Series([float(v) for v in values])


def test_resolve_exit_hits_target():
    highs = _series([100, 101, 102, 110, 104])
    lows = _series([99, 99, 99, 99, 99])
    closes = _series([100, 101, 102, 103, 104])
    idx, price, reason = resolve_exit(
        highs, lows, closes, entry_idx=0, stop=95, target=108, max_hold=10
    )
    assert (idx, price, reason) == (3, 108, "target_1")


def test_resolve_exit_hits_stop():
    highs = _series([100, 101, 102, 103, 104])
    lows = _series([99, 99, 90, 99, 99])
    closes = _series([100, 101, 95, 103, 104])
    idx, price, reason = resolve_exit(
        highs, lows, closes, entry_idx=0, stop=95, target=120, max_hold=10
    )
    assert (idx, price, reason) == (2, 95, "stop")


def test_resolve_exit_timeout_exits_at_close():
    highs = _series([100, 101, 102, 103, 104])
    lows = _series([99, 99, 99, 99, 99])
    closes = _series([100, 101, 102, 103, 104])
    idx, price, reason = resolve_exit(
        highs, lows, closes, entry_idx=0, stop=90, target=200, max_hold=3
    )
    assert (idx, reason) == (3, "timeout")
    assert price == pytest.approx(103.0)


def test_resolve_exit_same_bar_stop_wins():
    # Bar 1 pierces both the stop and the target — the conservative rule books it as a stop.
    highs = _series([100, 120, 104])
    lows = _series([99, 90, 99])
    closes = _series([100, 101, 104])
    idx, price, reason = resolve_exit(
        highs, lows, closes, entry_idx=0, stop=95, target=110, max_hold=10
    )
    assert (idx, price, reason) == (1, 95, "stop")


# --- simulator: end-to-end over the real pipeline --------------------------------------------

def _bt_settings(**overrides) -> Settings:
    base = dict(backtest_warmup_bars=30, backtest_max_hold_bars=20)
    base.update(overrides)
    return Settings(**base)


def test_simulate_symbol_generates_valid_trades():
    df = make_ohlcv("up", n=300)
    result = simulate_symbol(df, symbol="TEST.NS", settings=_bt_settings())
    assert result.error is None
    assert result.buy_hold_return is not None
    assert result.trades, "an uptrend should produce at least one entry"
    for t in result.trades:
        assert t.exit_reason in {"target_1", "stop", "timeout"}
        assert t.entry_action in {Action.BUY, Action.ACCUMULATE}
        assert t.bars_held >= 0
        # return and R-multiple must agree in sign (same entry/exit prices, positive risk).
        assert (t.return_pct >= 0) == (t.r_multiple >= 0)
        assert t.exit_date >= t.entry_date


def test_simulate_symbol_short_history_errors_cleanly():
    df = make_ohlcv("up", n=20)
    result = simulate_symbol(df, symbol="TEST.NS", settings=_bt_settings(backtest_warmup_bars=200))
    assert result.trades == []
    assert result.error is not None


def test_entry_actions_gate_trades():
    df = make_ohlcv("up", n=300)
    # Only SELL opens a trade -> an uptrend produces none.
    result = simulate_symbol(df, symbol="TEST.NS", settings=_bt_settings(backtest_entry_actions="SELL"))
    assert result.error is None
    assert result.trades == []


def test_parse_entry_actions_fallback():
    assert parse_entry_actions("garbage,,") == {Action.BUY, Action.ACCUMULATE}
    assert parse_entry_actions("buy") == {Action.BUY}


# --- metrics ---------------------------------------------------------------------------------

def _trade(ret: float, r: float, day: int, action=Action.BUY, conv=Conviction.MEDIUM) -> Trade:
    return Trade(
        symbol="X.NS",
        entry_date=date(2024, 1, day),
        entry_price=100.0,
        exit_date=date(2024, 1, day + 1),
        exit_price=100.0 * (1 + ret),
        exit_reason="target_1" if ret > 0 else "stop",
        return_pct=ret,
        r_multiple=r,
        bars_held=1,
        entry_action=action,
        entry_conviction=conv,
    )


def test_compute_stats_known_values():
    trades = [_trade(0.10, 2.0, 1), _trade(0.05, 1.0, 2), _trade(-0.04, -1.0, 3), _trade(-0.02, -0.5, 4)]
    s = compute_stats(trades)
    assert s.trades == 4
    assert s.wins == 2 and s.losses == 2
    assert s.win_rate == pytest.approx(0.5)
    assert s.avg_win_pct == pytest.approx(0.075)
    assert s.avg_loss_pct == pytest.approx(-0.03)
    assert s.expectancy_r == pytest.approx(0.375)
    assert s.profit_factor == pytest.approx(2.5)  # 0.15 / 0.06
    assert s.max_drawdown is not None and s.max_drawdown < 0


def test_compute_stats_empty():
    s = compute_stats([])
    assert s.trades == 0 and s.win_rate is None and s.profit_factor is None


def test_aggregate_pools_trades_and_benchmarks():
    r1 = SymbolResult(symbol="A.NS", trades=[_trade(0.10, 2.0, 1)], bars=300, buy_hold_return=0.20)
    r2 = SymbolResult(symbol="B.NS", trades=[_trade(-0.04, -1.0, 2)], bars=300, buy_hold_return=0.40)
    res = aggregate([r1, r2], target="watchlist:A,B", settings=_bt_settings())
    assert res.symbols == 2 and res.ok_symbols == 2
    assert res.stats.trades == 2
    assert res.stats.buy_hold_return == pytest.approx(0.30)  # mean(0.20, 0.40)
    assert set(res.by_action) <= {a.value for a in Action}


# --- engine: orchestration + failure isolation -----------------------------------------------

class _FlakySource:
    """History raises for any symbol containing BAD; returns a fixture frame otherwise."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def resolve(self, query: str):
        return query, None, "NSE"

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        if "BAD" in symbol:
            raise ValueError("no data")
        return self._df


def test_run_backtest_single_symbol():
    df = make_ohlcv("up", n=300)
    from tests.conftest import MockPriceSource

    res = run_backtest("TEST", settings=_bt_settings(), price_source=MockPriceSource(df))
    assert res.target == "TEST"
    assert res.ok_symbols == 1
    assert res.stats.trades >= 1


def test_run_backtest_isolates_failures():
    df = make_ohlcv("up", n=300)
    res = run_backtest(
        "watchlist:GOOD,BAD", settings=_bt_settings(), price_source=_FlakySource(df)
    )
    assert res.symbols == 2
    assert res.ok_symbols == 1
    assert any("BAD" in w for w in res.warnings)
