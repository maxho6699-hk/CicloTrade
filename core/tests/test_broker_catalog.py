from core.broker_catalog import US_LAUNCH_BROKER_CATALOG, public_us_launch_broker_catalog


def test_us_launch_catalog_is_exactly_the_approved_five_and_fail_closed():
    assert [entry.key for entry in US_LAUNCH_BROKER_CATALOG] == [
        "futu_moomoo", "tiger", "ibkr", "webull", "longbridge",
    ]
    assert all(entry.connection_available is False for entry in US_LAUNCH_BROKER_CATALOG)


def test_public_catalog_excludes_deferred_and_fallback_platforms():
    payload = public_us_launch_broker_catalog()
    provider_text = " ".join(str(item) for item in payload)

    assert len(payload) == 5
    assert "Alpaca" not in provider_text
    assert "QMT" not in provider_text
    assert "PTrade" not in provider_text
    assert payload[0]["status"] == "market_data_only"
    assert payload[1]["status"] == "limited_backend_capability"
    assert all(item["connection_available"] is False for item in payload)
