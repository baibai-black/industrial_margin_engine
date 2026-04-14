from __future__ import annotations

import math

from .domain import ExerciseStyle, InstrumentType, OptionRight, Position, RiskConfig, ScenarioFamily, ScenarioPoint, UnderlyingState


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _option_time_in_years(days_to_expiry: int | None) -> float:
    if days_to_expiry is None:
        return 30.0 / 365.0
    return max(days_to_expiry / 365.0, 1.0 / 365.0)


def black_scholes_price(
    spot: float,
    strike: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    time_to_expiry: float,
    option_right: OptionRight,
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN,
) -> float:
    spot = max(spot, 0.01)
    strike = max(strike, 0.01)
    volatility = max(volatility, 0.01)
    time_to_expiry = max(time_to_expiry, 1.0 / 365.0)
    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiry
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discounted_spot = spot * math.exp(-dividend_yield * time_to_expiry)
    discounted_strike = strike * math.exp(-rate * time_to_expiry)

    if option_right == OptionRight.CALL:
        price = discounted_spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
    else:
        price = discounted_strike * normal_cdf(-d2) - discounted_spot * normal_cdf(-d1)

    if exercise_style == ExerciseStyle.AMERICAN:
        # Lightweight early-exercise adjustment suitable for a demo service.
        dividend_premium = max(0.0, dividend_yield * spot * min(time_to_expiry, 1.0) * 0.20)
        carry_premium = max(0.0, rate * strike * min(time_to_expiry, 1.0) * 0.03)
        if option_right == OptionRight.CALL:
            price += dividend_premium
        else:
            price += carry_premium

    return max(price, 0.0)


def position_mark(position: Position, state: UnderlyingState, *, spot: float | None = None, vol: float | None = None) -> float:
    scenario_spot = max(spot if spot is not None else state.spot, 0.01)
    scenario_vol = max(vol if vol is not None else state.base_vol, 0.01)

    if position.instrument_type == InstrumentType.STOCK:
        return scenario_spot

    if position.instrument_type == InstrumentType.FUTURE:
        time_to_expiry = _option_time_in_years(position.days_to_expiry)
        return scenario_spot * math.exp((state.rate - state.dividend_yield) * time_to_expiry)

    if position.instrument_type == InstrumentType.OPTION:
        if position.strike is None or position.option_right is None:
            raise ValueError("Option position requires strike and option_right.")
        return black_scholes_price(
            spot=scenario_spot,
            strike=position.strike,
            rate=state.rate,
            dividend_yield=state.dividend_yield,
            volatility=scenario_vol,
            time_to_expiry=_option_time_in_years(position.days_to_expiry),
            option_right=position.option_right,
            exercise_style=position.exercise_style,
        )

    raise ValueError("Unsupported instrument type: %s" % position.instrument_type)


def position_market_value(position: Position, state: UnderlyingState, *, spot: float | None = None, vol: float | None = None) -> float:
    return position_mark(position, state, spot=spot, vol=vol) * position.quantity * position.multiplier


def tims_scenarios(state: UnderlyingState, config: RiskConfig) -> list[ScenarioPoint]:
    if config.tims_scenario_count < 2:
        raise ValueError("TIMS scenario count must be at least 2.")
    step = (2.0 * config.tims_scan_range_pct) / (config.tims_scenario_count - 1)
    shocks = [(-config.tims_scan_range_pct + step * idx) for idx in range(config.tims_scenario_count)]
    points = []
    for idx, shock in enumerate(shocks, start=1):
        points.append(
            ScenarioPoint(
                scenario_id="TIMS_%02d" % idx,
                family=ScenarioFamily.TIMS,
                underlying=state.symbol,
                shock_spot_pct=shock,
                shock_vol_pct=0.0,
                spot=max(state.spot * (1.0 + shock), 0.01),
                vol=max(state.base_vol, 0.01),
                rate=state.rate,
                dividend_yield=state.dividend_yield,
            )
        )
    return points


def base_risk_scenarios(state: UnderlyingState, config: RiskConfig) -> list[ScenarioPoint]:
    points = []
    counter = 1
    for price_shock in config.base_price_shocks:
        for vol_shock in config.base_vol_shocks:
            points.append(
                ScenarioPoint(
                    scenario_id="BASE_%02d" % counter,
                    family=ScenarioFamily.BASE_RISK,
                    underlying=state.symbol,
                    shock_spot_pct=price_shock,
                    shock_vol_pct=vol_shock,
                    spot=max(state.spot * (1.0 + price_shock), 0.01),
                    vol=max(state.base_vol * (1.0 + vol_shock), 0.01),
                    rate=state.rate,
                    dividend_yield=state.dividend_yield,
                )
            )
            counter += 1
    return points


def concentration_scenarios(state: UnderlyingState) -> list[ScenarioPoint]:
    down = state.concentration_down_pct
    up = state.concentration_up_pct
    vol_jump = state.concentration_vol_up_pct
    template = [
        (-down, vol_jump),
        (-down * 0.60, vol_jump * 0.60),
        (0.0, vol_jump),
        (up * 0.60, vol_jump * 0.20),
        (up, vol_jump * 0.50),
        (-down, 0.0),
    ]
    points = []
    for idx, (spot_shock, vol_shock) in enumerate(template, start=1):
        points.append(
            ScenarioPoint(
                scenario_id="CONC_%02d" % idx,
                family=ScenarioFamily.CONCENTRATION,
                underlying=state.symbol,
                shock_spot_pct=spot_shock,
                shock_vol_pct=vol_shock,
                spot=max(state.spot * (1.0 + spot_shock), 0.01),
                vol=max(state.base_vol * (1.0 + vol_shock), 0.01),
                rate=state.rate,
                dividend_yield=state.dividend_yield,
            )
        )
    return points


def build_scenario_points(state: UnderlyingState, family: ScenarioFamily, config: RiskConfig) -> list[ScenarioPoint]:
    if family == ScenarioFamily.TIMS:
        return tims_scenarios(state, config)
    if family == ScenarioFamily.BASE_RISK:
        return base_risk_scenarios(state, config)
    if family == ScenarioFamily.CONCENTRATION:
        return concentration_scenarios(state)
    raise ValueError("Unsupported scenario family: %s" % family)
