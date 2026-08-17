# services/risk.py
#
# ── ADVANCED RISK MANAGEMENT ENGINE ────────────────────────────────────────
# Upgraded from three flat scalar checks (allocation %, drawdown %, position
# count) to a corporate-style pre-trade audit that additionally produces:
#   - a composite 0-100 risk_score and A-F risk_grade for the specific order
#   - a parametric 1-day Value-at-Risk estimate for the position
#   - volatility-aware suggested stop-loss / take-profit levels and the
#     resulting risk:reward ratio
#   - a liquidity check against average daily volume (when supplied)
#   - a concentration check (this position vs. total account exposure)
#
# All ORIGINAL fields (cleared, breach_reason, max_qty_allowed) are still
# returned unchanged — this is purely additive so existing callers
# (orders.py, stocks.py, the frontend RiskCheckResult type) keep working
# without modification. New optional parameters default to conservative
# assumptions when the caller doesn't have real market data (ATR / ADV) to
# pass in yet, and are clearly labelled as estimates in the response.

import math
from typing import Dict, Any, Optional

# z-score for a 95% one-tailed confidence VaR (industry-standard default)
VAR_Z_SCORE_95 = 1.645


class RiskManager:
    def __init__(
        self,
        account_balance: float = 1000000.0,
        max_trade_allocation_pct: float = 15.0,
        max_daily_drawdown_pct: float = 3.5,
        max_portfolio_size: int = 8,
        max_single_position_var_pct: float = 2.0,     # max acceptable 1-day VaR as % of account
        min_risk_reward_ratio: float = 1.5,            # corporate desks typically require >= 1.5:1
        default_daily_volatility_pct: float = 2.0,     # conservative fallback if no real ATR/volatility supplied
        max_adv_participation_pct: float = 10.0,       # don't be more than 10% of a day's average volume
    ):
        self.account_balance = account_balance
        self.max_trade_allocation_pct = max_trade_allocation_pct
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_portfolio_size = max_portfolio_size
        self.max_single_position_var_pct = max_single_position_var_pct
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.default_daily_volatility_pct = default_daily_volatility_pct
        self.max_adv_participation_pct = max_adv_participation_pct

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _grade_from_score(self, score: float) -> str:
        if score < 20:
            return "A"
        if score < 40:
            return "B"
        if score < 60:
            return "C"
        if score < 80:
            return "D"
        return "F"

    def _estimate_var_1d(self, order_value: float, volatility_pct: float) -> float:
        """Parametric 1-day VaR at 95% confidence: order_value * daily_vol * z-score."""
        return round(order_value * (volatility_pct / 100.0) * VAR_Z_SCORE_95, 2)

    def _suggest_stop_and_target(self, price: float, volatility_pct: float, transaction_type: str = "BUY") -> Dict[str, float]:
        """
        Volatility-scaled stop-loss / take-profit suggestion (ATR-style, using
        volatility_pct as an ATR proxy when a real ATR isn't supplied).
        Stop distance = 1x daily volatility; target distance = min_risk_reward_ratio x stop distance.
        """
        stop_distance = price * (volatility_pct / 100.0)
        target_distance = stop_distance * self.min_risk_reward_ratio

        is_short = transaction_type.upper() in ("SELL", "SHORT")
        if is_short:
            stop_price = price + stop_distance
            target_price = price - target_distance
        else:
            stop_price = price - stop_distance
            target_price = price + target_distance

        return {
            "stop_loss_price": round(max(0.0, stop_price), 2),
            "take_profit_price": round(max(0.0, target_price), 2),
            "risk_reward_ratio": round(target_distance / stop_distance, 2) if stop_distance > 0 else 0.0,
        }

    # ── Core Evaluation ─────────────────────────────────────────────────────

    def evaluate_order(
        self,
        symbol: str,
        price: float,
        qty: int,
        current_drawdown_pct: float,
        active_portfolio_count: int,
        transaction_type: str = "BUY",
        volatility_pct: Optional[float] = None,
        avg_daily_volume: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Runs pre-trade validation checks (hard gates, unchanged behavior) AND
        a corporate-style advisory risk audit (new, additive) covering VaR,
        volatility-aware stop/target levels, liquidity, and a composite
        risk score/grade.

        `volatility_pct` and `avg_daily_volume` are optional — pass real ATR%
        and average daily volume from the technicals engine when available
        for a materially more accurate audit. Without them, conservative
        defaults are used and the response is labelled accordingly.
        """
        volatility_pct = volatility_pct if volatility_pct and volatility_pct > 0 else self.default_daily_volatility_pct
        volatility_is_estimated = volatility_pct == self.default_daily_volatility_pct

        if price <= 0 or qty <= 0:
            return {
                "cleared": False,
                "breach_reason": "Invalid transaction metrics (price or quantity must be positive value).",
                "max_qty_allowed": 0
            }

        order_value = price * qty
        allocated_pct = (order_value / self.account_balance) * 100.0

        # ── HARD GATES (unchanged — these can still block the order) ────────

        # 1. Verification of Maximum Capital Allocation Ceiling
        if allocated_pct > self.max_trade_allocation_pct:
            max_allowed_val = self.account_balance * (self.max_trade_allocation_pct / 100.0)
            max_qty_allowed = int(max_allowed_val / price)
            return {
                "cleared": False,
                "breach_reason": f"Allocated trade size ({allocated_pct:.2f}%) exceeds the allocation limit of {self.max_trade_allocation_pct}%.",
                "max_qty_allowed": max_qty_allowed
            }

        # 2. Daily Drawdown Threshold Verification
        if current_drawdown_pct >= self.max_daily_drawdown_pct:
            return {
                "cleared": False,
                "breach_reason": f"Daily portfolio drawdown ({current_drawdown_pct:.2f}%) has breached limit of {self.max_daily_drawdown_pct}%. Trading disabled.",
                "max_qty_allowed": 0
            }

        # 3. Portfolio Size / Congestion Checks
        if active_portfolio_count >= self.max_portfolio_size:
            return {
                "cleared": False,
                "breach_reason": f"Active position count ({active_portfolio_count}) meets the maximum allowed size limit of {self.max_portfolio_size}.",
                "max_qty_allowed": 0
            }

        # ── ADVISORY / CORPORATE-STYLE AUDIT (new — informs but doesn't auto-block) ──

        var_1d = self._estimate_var_1d(order_value, volatility_pct)
        var_pct_of_account = round((var_1d / self.account_balance) * 100.0, 3)
        stop_target = self._suggest_stop_and_target(price, volatility_pct, transaction_type)

        liquidity_flag = None
        liquidity_participation_pct = None
        if avg_daily_volume and avg_daily_volume > 0:
            liquidity_participation_pct = round((qty / avg_daily_volume) * 100.0, 3)
            if liquidity_participation_pct > self.max_adv_participation_pct:
                liquidity_flag = (
                    f"Order size is {liquidity_participation_pct:.2f}% of average daily volume "
                    f"(desk limit {self.max_adv_participation_pct}%) — may move the price or be hard to exit quickly."
                )

        # Composite risk score (0-100, higher = riskier), blending four factors:
        #   - capital concentration (this order vs. allocation ceiling)
        #   - VaR consumption (this order's 1-day VaR vs. the account-level cap)
        #   - proximity to the daily drawdown limit already used up today
        #   - liquidity strain (participation rate vs. desk limit), if known
        concentration_score = min(100.0, (allocated_pct / self.max_trade_allocation_pct) * 100.0)
        var_score = min(100.0, (var_pct_of_account / self.max_single_position_var_pct) * 100.0)
        drawdown_score = min(100.0, (current_drawdown_pct / self.max_daily_drawdown_pct) * 100.0)
        liquidity_score = min(100.0, (liquidity_participation_pct / self.max_adv_participation_pct) * 100.0) if liquidity_participation_pct is not None else 0.0

        weights_sum = 0.35 + 0.30 + 0.20 + (0.15 if liquidity_participation_pct is not None else 0.0)
        risk_score = round(
            (0.35 * concentration_score + 0.30 * var_score + 0.20 * drawdown_score +
             (0.15 * liquidity_score if liquidity_participation_pct is not None else 0.0)) / weights_sum,
            1
        )
        risk_grade = self._grade_from_score(risk_score)

        # ── TOP-LEVEL GATE: the composite audit can now itself block a trade ──
        # Previously risk_score/risk_grade were purely advisory -- an order
        # could clear the three hard gates above with a genuinely terrible
        # composite score (huge VaR relative to account, poor R:R, thin
        # liquidity) and still go through with just a warning attached. An
        # F-grade (>=80/100) order is now blocked outright, putting the risk
        # engine on equal footing with the allocation/drawdown/portfolio-size
        # checks rather than subordinate to them.
        if risk_grade == "F":
            return {
                "cleared": False,
                "breach_reason": (
                    f"Composite risk audit graded this order 'F' (score {risk_score}/100) -- "
                    f"combined concentration/VaR/drawdown/liquidity exposure is too high even "
                    f"though it passed the individual hard limits."
                ),
                "max_qty_allowed": 0,
                "risk_score": risk_score,
                "risk_grade": risk_grade,
                "value_at_risk_1d": var_1d,
                "value_at_risk_1d_pct_of_account": var_pct_of_account,
            }

        warnings = []
        if var_pct_of_account > self.max_single_position_var_pct:
            warnings.append(
                f"Estimated 1-day VaR ({var_pct_of_account:.2f}% of account) exceeds the "
                f"{self.max_single_position_var_pct}% single-position guideline."
            )
        if stop_target["risk_reward_ratio"] < self.min_risk_reward_ratio:
            warnings.append(
                f"Risk:reward on the suggested stop/target ({stop_target['risk_reward_ratio']}:1) "
                f"is below the desk minimum of {self.min_risk_reward_ratio}:1."
            )
        if liquidity_flag:
            warnings.append(liquidity_flag)
        if volatility_is_estimated:
            warnings.append(
                f"No live volatility (ATR) was supplied — using a conservative "
                f"{self.default_daily_volatility_pct}% daily volatility assumption. "
                f"Pass real ATR% for a materially more accurate audit."
            )

        return {
            # ── Original fields (unchanged) ──
            "cleared": True,
            "breach_reason": "",
            "max_qty_allowed": qty,

            # ── Advanced corporate-style audit (additive) ──
            "risk_score": risk_score,
            "risk_grade": risk_grade,
            "concentration_pct": round(allocated_pct, 3),
            "value_at_risk_1d": var_1d,
            "value_at_risk_1d_pct_of_account": var_pct_of_account,
            "volatility_pct_used": volatility_pct,
            "volatility_is_estimated": volatility_is_estimated,
            "suggested_stop_loss_price": stop_target["stop_loss_price"],
            "suggested_take_profit_price": stop_target["take_profit_price"],
            "risk_reward_ratio": stop_target["risk_reward_ratio"],
            "liquidity_participation_pct": liquidity_participation_pct,
            "warnings": warnings,
        }


# Instantiate Global Risk Management Unit
risk_service = RiskManager()