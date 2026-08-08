from ui.components import _telegram_community_url, brand_bar, experience_hero


def test_telegram_community_url_allows_only_telegram_https(monkeypatch):
    monkeypatch.setenv("TRADEAI_TELEGRAM_COMMUNITY_URL", "https://t.me/tradeai")
    assert _telegram_community_url() == "https://t.me/tradeai"

    for value in (
        "javascript:alert(1)",
        "data:text/html,hello",
        "http://t.me/tradeai",
        "https://example.com/tradeai",
        "https://user:pass@t.me/tradeai",
    ):
        monkeypatch.setenv("TRADEAI_TELEGRAM_COMMUNITY_URL", value)
        assert _telegram_community_url() is None


def test_experience_hero_escapes_content(monkeypatch):
    rendered = []
    monkeypatch.setattr("ui.components.st.html", rendered.append)

    experience_hero("<tag>", "研究", "说明", "状态", (("数据", "<script>"),))

    assert "&lt;tag&gt;" in rendered[0]
    assert "&lt;script&gt;" in rendered[0]
    assert "<script>" not in rendered[0]


def test_brand_bar_has_no_session_resetting_internal_links(monkeypatch):
    rendered = []
    monkeypatch.setattr("ui.components.st.html", rendered.append)
    monkeypatch.delenv("TRADEAI_TELEGRAM_COMMUNITY_URL", raising=False)

    brand_bar(False, True)

    assert 'href="/research"' not in rendered[0]
    assert 'href="/roadmap"' not in rendered[0]
    assert 'href="/help"' not in rendered[0]


def test_monitor_status_card_escapes_dynamic_values():
    from ui.pages.monitor import _status_card

    rendered = _status_card("<b>name</b>", "<script>x</script>", "ok", "<img>", "warn")

    assert "<script>" not in rendered
    assert "&lt;script&gt;x&lt;/script&gt;" in rendered
