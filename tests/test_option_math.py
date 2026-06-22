from trading_system.analysis.option_math import compute_features


def _row(strike, ce_oi, pe_oi, ce_ltp=100.0, pe_ltp=100.0):
    return {
        "expiry": "2026-05-08",
        "strike_price": strike,
        "underlying_spot_price": 22500,
        "call_options": {
            "instrument_key": f"CE_{strike}",
            "market_data": {
                "ltp": ce_ltp, "oi": ce_oi, "volume": 1000,
                "bid_price": ce_ltp - 1, "ask_price": ce_ltp + 1,
            },
            "option_greeks": {"iv": 13.0},
        },
        "put_options": {
            "instrument_key": f"PE_{strike}",
            "market_data": {
                "ltp": pe_ltp, "oi": pe_oi, "volume": 1000,
                "bid_price": pe_ltp - 1, "ask_price": pe_ltp + 1,
            },
            "option_greeks": {"iv": 13.0},
        },
    }


def test_max_pain_basic():
    payload = {
        "data": [
            _row(22300, ce_oi=100, pe_oi=2000),
            _row(22400, ce_oi=300, pe_oi=1500),
            _row(22500, ce_oi=1000, pe_oi=1000),
            _row(22600, ce_oi=1500, pe_oi=300),
            _row(22700, ce_oi=2000, pe_oi=100),
        ]
    }
    f = compute_features(payload)
    assert f.atm_strike == 22500
    assert f.highest_call_oi_strike == 22700
    assert f.highest_put_oi_strike == 22300
    assert f.expected_range_low == 22300
    assert f.expected_range_high == 22700
    assert f.max_pain == 22500
