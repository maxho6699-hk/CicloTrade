from ui.quant_format import contract_label, source_label, strategy_version_label


def test_internal_quant_codes_are_rendered_as_customer_labels():
    stock = {"market": "CN", "instrument_type": "stock", "symbol": "600519"}
    option = {
        "market": "US", "instrument_type": "option", "symbol": "AAPL",
        "option_expiry": "2026-09-18", "option_right": "CALL", "option_strike": 210,
    }

    assert contract_label(stock, show_market=True) == "大A · 600519"
    assert contract_label(option, show_market=True) == "美股 · AAPL · 2026-09-18 · 210 Call"
    assert strategy_version_label("catalog-2026-08-08") == "每日模型 2026-08-08"
    assert strategy_version_label("v2") == "第 2 版"
    assert source_label("ciclotrade-adaptive") == "系統量化模型"
