"""Deterministic entry / target / stop-loss computation.

Computed before any LLM sees the snapshot, so the tool always produces concrete numbers.
ATR-based volatility stop, snapped to the nearest structural support; targets derived from
risk-reward multiples and confirmed against overhead resistance.
"""

from __future__ import annotations

from indi_analyst.config import Settings, get_settings
from indi_analyst.models import StockSnapshot, TradeLevels


def compute_levels(snapshot: StockSnapshot, settings: Settings | None = None) -> TradeLevels:
    settings = settings or get_settings()
    tech = snapshot.technicals
    price = tech.last_close

    # ATR fallback: if the volatility estimate is missing, use 2% of price as a proxy.
    atr = tech.atr_14 or (price * 0.02)
    k = settings.atr_stop_multiple

    # --- Entry zone ---
    # Trend-aware: in an uptrend, prefer a shallow pullback toward the 20-EMA / support;
    # otherwise anchor the band tightly around the current price.
    if tech.trend == "uptrend":
        pullback = max(tech.ema_20 or price, (tech.supports[0] if tech.supports else price - atr))
        entry_low = min(price, pullback)
        entry_high = price
    else:
        entry_low = price - 0.4 * atr
        entry_high = price + 0.4 * atr
    entry_mid = (entry_low + entry_high) / 2

    # --- Stop loss: ATR volatility stop. Only ever *widen* it to sit just below a
    # structural support — never tighten above the ATR floor (that would shrink risk to
    # near-zero and distort the risk-reward targets).
    raw_stop = entry_mid - k * atr
    below_stop = [s for s in tech.supports if s < raw_stop]
    if below_stop and (raw_stop - below_stop[0]) <= 0.75 * atr:
        raw_stop = below_stop[0] - 0.15 * atr
    stop_loss = round(raw_stop, 2)
    risk = entry_mid - stop_loss
    if risk <= 0:  # degenerate guard
        risk = k * atr
        stop_loss = round(entry_mid - risk, 2)

    # --- Targets from risk-reward multiples, snapped to *distinct* nearby resistances.
    used: set[float] = set()

    def _target(rr: float) -> float:
        t = entry_mid + rr * risk
        for r in tech.resistances:
            if r not in used and abs(r - t) / t <= 0.02:  # resistance within 2% -> use it
                used.add(r)
                return r
        return t

    target_1 = round(_target(settings.target1_rr), 2)
    target_2 = round(_target(settings.target2_rr), 2)
    # Guarantee separation and ordering (snapping must never collapse T1 == T2).
    if target_2 <= target_1:
        target_2 = round(entry_mid + settings.target2_rr * risk, 2)
        if target_2 <= target_1:
            target_2 = round(target_1 + risk, 2)

    stop_pct = (stop_loss - entry_mid) / entry_mid
    t1_pct = (target_1 - entry_mid) / entry_mid
    t2_pct = (target_2 - entry_mid) / entry_mid
    risk_reward = (target_1 - entry_mid) / risk if risk else 0.0

    return TradeLevels(
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        stop_loss=stop_loss,
        stop_loss_pct=round(stop_pct, 4),
        target_1=target_1,
        target_2=target_2,
        target_1_pct=round(t1_pct, 4),
        target_2_pct=round(t2_pct, 4),
        risk_reward=round(risk_reward, 2),
        atr_multiple=k,
    )
