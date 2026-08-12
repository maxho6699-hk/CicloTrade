import asyncio
from datetime import datetime, timedelta
from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image
from starlette.requests import Request

from core.auth import AuthService
from core.compat import UTC
from core.database import DatabaseManager
from src.apps.api.app import ApiError, app as api_application, membership_order_proof
from src.apps.api.read_model import BrowserIdentity, ReadOnlyLegacyRepository
from src.apps.api.write_service import BrowserWriteService
from core.alerts import AlertService
from payment.proof_storage import payment_proof_root
from payment.receiver_storage import (
    read_receiver_qr,
    receiver_asset_root,
    resolve_receiver_asset,
    store_receiver_qr,
)
from payment.receiving_profile import ReceivingProfileService


@pytest.fixture
def write_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-that-is-longer-than-32-characters")
    monkeypatch.setenv("FPS_PAYMENT_INSTRUCTIONS", "FPS account and order reference")
    monkeypatch.setenv("ALIPAY_PAYMENT_INSTRUCTIONS", "Alipay account and order reference")
    monkeypatch.setenv("WECHAT_PAYMENT_INSTRUCTIONS", "WeChat account and order reference")
    monkeypatch.setenv("PAYMENT_PROOF_DIR", str(tmp_path / "payment-proofs"))
    monkeypatch.setenv("PAYMENT_RECEIVER_ASSET_DIR", str(tmp_path / "payment-receiver-assets"))
    database = DatabaseManager(str(tmp_path / "browser-writes.db"))
    auth = AuthService(database)
    user = auth.register("writer@example.com", "StrongPass123", "Writer", True)
    assert user is not None
    expiry = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    login = auth.login("writer@example.com", "StrongPass123", "127.0.0.1", "pytest")
    repository = ReadOnlyLegacyRepository(tmp_path / "browser-writes.db")
    identity = repository.authenticate(login.access_token)
    return database, identity, BrowserWriteService(database)


def valid_risk():
    return {
        "max_position_per_symbol": 5_000,
        "max_total_position": 50_000,
        "max_daily_loss": 2_000,
        "max_position_per_symbol_cny": 35_000,
        "max_total_position_cny": 350_000,
        "max_daily_loss_cny": 14_000,
        "cooldown_minutes": 30,
        "consecutive_loss_limit": 3,
    }


def test_risk_settings_validate_full_shape_and_persist(write_context):
    _, identity, service = write_context

    saved = service.update_risk(identity, valid_risk())

    assert saved["max_daily_loss"] == 2_000
    assert service.settings(identity)["risk"] == saved


def test_risk_settings_reject_unknown_or_inverted_limits(write_context):
    _, identity, service = write_context
    unknown = {**valid_risk(), "bypass": True}
    inverted = {**valid_risk(), "max_position_per_symbol": 60_000}

    with pytest.raises(ValueError, match="未知"):
        service.update_risk(identity, unknown)
    with pytest.raises(ValueError, match="不能超过"):
        service.update_risk(identity, inverted)


def test_resume_opening_is_user_scoped_idempotent_and_audited(write_context):
    database, identity, service = write_context
    database.execute("UPDATE user_controls SET opening_paused=1 WHERE user_id=?", (identity.id,))
    database.execute(
        """INSERT INTO platform_controls(control_key,control_value,updated_at)
           VALUES ('opening_paused','1',datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value='1',updated_at=datetime('now')"""
    )

    assert service.resume_opening(identity) is True
    assert service.resume_opening(identity) is False
    assert database.fetch_one("SELECT opening_paused FROM user_controls WHERE user_id=?", (identity.id,))["opening_paused"] == 0
    assert database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"] == "1"
    assert database.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='USER_RESUME_OPENING'",
        (identity.id,),
    )["count"] == 1


def test_telegram_preferences_enforce_membership_entitlements(write_context):
    database, identity, service = write_context

    enabled = service.update_telegram_events(identity, {"stock_signal": True})
    assert enabled["stock_signal"] is True

    with pytest.raises(PermissionError, match="期权"):
        service.update_telegram_events(identity, {"option_signal": True})
    assert database.fetch_one("SELECT settings_json FROM user_settings WHERE user_id=?", (identity.id,))


def test_watchlist_add_remove_normalizes_and_preserves_other_settings(write_context):
    _, identity, service = write_context
    service.update_risk(identity, valid_risk())
    service.update_telegram_events(identity, {"price_alert": True})

    added = service.update_watchlist(identity, {"market": "US", "symbol": " pltr "})
    duplicate = service.update_watchlist(identity, {"market": "US", "symbol": "PLTR"})
    service.update_watchlist(identity, {"market": "CN", "symbol": "600519.SS"})
    removed = service.update_watchlist(
        identity, {"market": "US", "symbol": "PLTR"}, remove=True
    )

    assert added == {"us": ["PLTR"], "a_share": []}
    assert duplicate == added
    assert removed == {"us": [], "a_share": ["600519"]}
    assert service.settings(identity)["risk"]["max_total_position"] == 50_000
    assert service.settings(identity)["telegram_events"]["price_alert"] is True


def test_watchlist_rejects_invalid_market_symbol_and_unknown_fields(write_context):
    _, identity, service = write_context

    with pytest.raises(ValueError, match="US 或 CN"):
        service.update_watchlist(identity, {"market": "HK", "symbol": "0700"})
    with pytest.raises(ValueError, match="代码无效"):
        service.update_watchlist(identity, {"market": "US", "symbol": "../PLTR"})
    with pytest.raises(ValueError, match="代码无效"):
        service.update_watchlist(identity, {"market": "US", "symbol": "USD=X"})
    with pytest.raises(ValueError, match="代码无效"):
        service.update_watchlist(identity, {"market": "US", "symbol": "600519"})
    with pytest.raises(ValueError, match="代码无效"):
        service.update_watchlist(identity, {"market": "CN", "symbol": "PLTR"})
    with pytest.raises(ValueError, match="字符串"):
        service.update_watchlist(identity, {"market": "US", "symbol": 123})
    with pytest.raises(ValueError, match="未知字段"):
        service.update_watchlist(identity, {"market": "US", "symbol": "PLTR", "admin": True})


def test_watchlist_pins_are_ordered_idempotent_and_removed_with_symbol(write_context):
    _, identity, service = write_context
    service.update_watchlist(identity, {"market": "US", "symbol": "PLTR"})
    service.update_watchlist(identity, {"market": "US", "symbol": "AAPL"})

    first_pin = service.update_watchlist_pin(
        identity, {"market": "US", "symbol": "AAPL", "pinned": True}
    )
    repeated_pin = service.update_watchlist_pin(
        identity, {"market": "US", "symbol": "AAPL", "pinned": True}
    )

    assert first_pin == repeated_pin == {"us": ["AAPL"], "a_share": []}
    assert service.settings(identity)["watchlists"]["us"] == ["AAPL", "PLTR"]

    service.update_watchlist(identity, {"market": "US", "symbol": "AAPL"}, remove=True)
    assert service.settings(identity)["watchlist_pins"] == {"us": [], "a_share": []}

    with pytest.raises(ValueError, match="代码无效"):
        service.update_watchlist_pin(
            identity, {"market": "US", "symbol": "USD=X", "pinned": True}
        )
    with pytest.raises(ValueError, match="尚未加入"):
        service.update_watchlist_pin(
            identity, {"market": "US", "symbol": "MSFT", "pinned": True}
        )


def test_watchlist_allows_100_symbols_and_rejects_101st_without_mutation(write_context):
    _, identity, service = write_context
    for index in range(100):
        service.update_watchlist(
            identity, {"market": "US", "symbol": f"A{index:011d}"}
        )

    before = service.settings(identity)["watchlists"]
    with pytest.raises(ValueError, match="最多保存 100"):
        service.update_watchlist(identity, {"market": "US", "symbol": "Z00000000000"})

    assert len(before["us"]) == 100
    assert service.settings(identity)["watchlists"] == before


def test_alert_creation_uses_legacy_plan_and_condition_rules(write_context):
    _, identity, service = write_context

    alerts = service.create_alert(identity, {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
        "logic": "AND",
    })

    assert alerts[0]["symbol"] == "AAPL"
    assert alerts[0]["market"] == "US"
    assert alerts[0]["conditions_list"][0]["value"] == 220


def test_alert_creation_accepts_explicit_us_and_cn_markets(write_context):
    _, identity, service = write_context

    us_items = service.create_alert(identity, {
        "market": "US",
        "symbol": " aapl ",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
    })
    legacy_us_items = service.create_alert(identity, {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
    })
    cn_items = service.create_alert(identity, {
        "market": "CN",
        "symbol": "600519.SS",
        "conditions": [{"type": "price", "operator": ">=", "value": 1500}],
    })

    assert {(item["market"], item["symbol"]) for item in cn_items} == {
        ("US", "AAPL"),
        ("CN", "600519"),
    }
    assert us_items[0]["market"] == "US"
    assert len(legacy_us_items) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"market": "A股", "symbol": "600519"},
        {"market": "US", "symbol": "600519"},
        {"market": "CN", "symbol": "AAPL"},
    ],
)
def test_alert_creation_rejects_invalid_market_symbol_pairs(write_context, payload):
    _, identity, service = write_context
    with pytest.raises(ValueError):
        service.create_alert(identity, {
            **payload,
            "conditions": [{"type": "price", "operator": ">=", "value": 100}],
        })


def test_alert_metadata_validates_cross_repeat_expiry_channels_and_deduplicates(write_context):
    database, identity, service = write_context
    expires = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    payload = {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
        "trigger_mode": "crosses_above",
        "repeat_mode": "repeat",
        "expires_at": expires,
        "channels": ["website"],
        "notify_only": True,
    }
    first = service.create_alert(identity, payload)
    repeated = service.create_alert(identity, payload)

    assert len(first) == len(repeated) == 1
    assert database.fetch_one("SELECT COUNT(*) count FROM price_alerts WHERE user_id=?", (identity.id,))["count"] == 1
    item = first[0]
    assert item["trigger_mode"] == "crosses_above"
    assert item["repeat_mode"] == "repeat"
    assert item["channels"] == ["website"]
    assert item["notify_only"] is True

    with pytest.raises(ValueError, match="跌破"):
        service.create_alert(identity, {
            "symbol": "MSFT",
            "conditions": [{"type": "price", "operator": ">=", "value": 100}],
            "trigger_mode": "crosses_below",
            "channels": ["website"],
        })
    database.execute("UPDATE users SET plan_type='免费版',subscription_expire=NULL WHERE id=?", (identity.id,))
    with pytest.raises(ValueError, match="Telegram"):
        service.create_alert(identity, {
            "symbol": "TSLA",
            "conditions": [{"type": "price", "operator": ">=", "value": 100}],
            "channels": ["telegram"],
        })


def test_crossing_repeat_alert_requires_crossing_and_can_trigger_again(write_context):
    database, identity, service = write_context
    item = service.create_alert(identity, {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
        "trigger_mode": "crosses_above",
        "repeat_mode": "repeat",
        "channels": ["website"],
    })[0]
    alerts = AlertService(database)

    assert alerts.evaluate(identity.id, {"AAPL": 221}) == []
    assert len(alerts.evaluate(identity.id, {"AAPL": 219})) == 0
    assert len(alerts.evaluate(identity.id, {"AAPL": 220})) == 1
    assert alerts.list(identity.id)[0]["is_active"] == 1
    assert len(alerts.evaluate(identity.id, {"AAPL": 221})) == 0
    assert len(alerts.evaluate(identity.id, {"AAPL": 219})) == 0
    assert len(alerts.evaluate(identity.id, {"AAPL": 221})) == 1
    assert database.fetch_one("SELECT COUNT(*) count FROM price_alerts WHERE id=?", (item["id"],))["count"] == 1
    assert database.fetch_one("SELECT COUNT(*) count FROM notifications")["count"] == 2


def test_alert_deactivation_is_user_scoped_idempotent_and_audited(write_context):
    database, identity, service = write_context
    alert = service.create_alert(identity, {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": ">=", "value": 220}],
    })[0]
    other = BrowserIdentity(
        id=identity.id + 10_000,
        display_name="Other",
        plan_type="免费版",
        subscription_expire=None,
    )

    with pytest.raises(ValueError, match="找不到"):
        service.deactivate_alert(other, int(alert["id"]))
    assert database.fetch_one("SELECT is_active FROM price_alerts WHERE id=?", (alert["id"],))["is_active"] == 1

    first = service.deactivate_alert(identity, int(alert["id"]))
    second = service.deactivate_alert(identity, int(alert["id"]))

    assert first[0]["is_active"] == second[0]["is_active"] == 0
    assert database.fetch_one("SELECT COUNT(*) count FROM price_alerts WHERE id=?", (alert["id"],))["count"] == 1
    assert database.fetch_one(
        "SELECT COUNT(*) count FROM strategy_action_logs WHERE user_id=? AND action='ALERT_DEACTIVATE'",
        (identity.id,),
    )["count"] == 1


def test_browser_write_service_does_not_expose_personal_paper_orders(write_context):
    _, _, service = write_context
    assert not hasattr(service, "create_paper_order")


@pytest.mark.parametrize("method", ["fps", "alipay", "wechat"])
def test_membership_order_is_idempotent_and_redacted(write_context, method):
    database, identity, service = write_context
    payload = {"plan": "专业版", "cycle": "monthly", "method": method, "terms_accepted": True}

    idempotency_key = f"membership-order-{method}"
    first = service.create_membership_order(identity, payload, idempotency_key)
    second = service.create_membership_order(identity, payload, idempotency_key)

    assert first == second
    assert first["pay_method"] == method
    assert first["payment_instructions"]
    assert "external_id" not in first
    assert database.fetch_one(
        "SELECT COUNT(*) count FROM subscription_orders WHERE user_id=?", (identity.id,)
    )["count"] == 1


def test_membership_payment_instructions_normalize_escaped_newlines(write_context, monkeypatch):
    _, identity, service = write_context
    monkeypatch.setenv("FPS_PAYMENT_INSTRUCTIONS", "FPS ID: 1234567\\nReference: order number")

    order = service.create_membership_order(
        identity,
        {"plan": "高级版", "cycle": "monthly", "method": "fps", "terms_accepted": True},
        "membership-instruction-lines",
    )

    assert order["payment_instructions"] == "FPS ID: 1234567\nReference: order number"


def test_tg_admin_receiver_qr_is_bound_to_order_and_enforces_owner_status(write_context):
    database, identity, service = write_context
    auth = AuthService(database)
    admin = auth.register("receiver-admin@example.com", "StrongPass123", "Receiver Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    image = BytesIO()
    Image.new("RGB", (256, 256), "white").save(image, format="PNG")
    stored = store_receiver_qr(image.getvalue(), "image/png")
    profiles = ReceivingProfileService(database)
    profiles.set_receiver_text(admin["id"], "alipay", "Alipay receiver 123")
    profiles.set_receiver_qr(
        admin["id"], "alipay", stored, "telegram-alipay-qr", "telegram-alipay-unique"
    )

    order = service.create_membership_order(
        identity,
        {"plan": "高级版", "cycle": "monthly", "method": "alipay", "terms_accepted": True},
        "membership-receiver-qr",
    )
    assert order["payment_instructions"] == "Alipay receiver 123"
    assert order["payment_qr_available"] is True
    assert service.membership_payment_qr(identity, order["order_no"]).startswith(b"\xff\xd8")

    auth.register("receiver-stranger@example.com", "StrongPass123", "Stranger", True)
    stranger_login = auth.login(
        "receiver-stranger@example.com", "StrongPass123", "127.0.0.1", "pytest"
    )
    stranger_identity = ReadOnlyLegacyRepository(Path(database._db_path)).authenticate(
        stranger_login.access_token
    )
    with pytest.raises(PermissionError):
        service.membership_payment_qr(stranger_identity, order["order_no"])
    database.execute("UPDATE subscription_orders SET status='paid' WHERE order_no=?", (order["order_no"],))
    with pytest.raises(PermissionError):
        service.membership_payment_qr(identity, order["order_no"])


def test_payment_proof_directory_rejects_public_web_roots(monkeypatch):
    public_path = Path(__file__).resolve().parents[4] / "static" / "payment-proofs"
    monkeypatch.setenv("PAYMENT_PROOF_DIR", str(public_path))

    with pytest.raises(ValueError, match="公开网站目录"):
        payment_proof_root()


def test_receiver_qr_directory_rejects_public_web_roots(monkeypatch):
    public_path = Path(__file__).resolve().parents[4] / "static" / "payment-receivers"
    monkeypatch.setenv("PAYMENT_RECEIVER_ASSET_DIR", str(public_path))

    with pytest.raises(ValueError, match="公开网站目录"):
        receiver_asset_root()


def test_receiver_qr_rejects_tampered_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_RECEIVER_ASSET_DIR", str(tmp_path / "payment-receiver-assets"))
    image = BytesIO()
    Image.new("RGB", (96, 96), "white").save(image, format="PNG")
    stored = store_receiver_qr(image.getvalue(), "image/png")
    resolve_receiver_asset(stored.storage_key).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="完整性校验失败"):
        read_receiver_qr(stored.storage_key, stored.sha256)


def test_browser_membership_proof_is_sanitized_private_and_idempotent(write_context, tmp_path):
    database, identity, service = write_context
    order = service.create_membership_order(
        identity,
        {"plan": "专业版", "cycle": "monthly", "method": "alipay", "terms_accepted": True},
        "membership-proof-order",
    )
    image = BytesIO()
    Image.new("RGB", (320, 480), "white").save(image, format="PNG")

    first = service.submit_membership_proof(
        identity, order["order_no"], image.getvalue(), "image/png"
    )
    repeated = service.submit_membership_proof(
        identity, order["order_no"], image.getvalue(), "image/png"
    )

    assert first == repeated
    claim = database.fetch_one(
        """SELECT status,evidence_source,evidence_storage_key,evidence_file_unique_id
           FROM manual_payment_claims WHERE order_no=?""",
        (order["order_no"],),
    )
    assert claim["status"] == "submitted"
    assert claim["evidence_source"] == "web"
    proof_files = list((tmp_path / "payment-proofs").glob("*.jpg"))
    assert [path.name for path in proof_files] == [claim["evidence_storage_key"]]
    with Image.open(proof_files[0]) as stored:
        assert stored.format == "JPEG"
        assert stored.getexif() == {}


def test_browser_membership_proof_rejects_non_image_without_storing(write_context, tmp_path):
    database, identity, service = write_context
    order = service.create_membership_order(
        identity,
        {"plan": "高级版", "cycle": "monthly", "method": "wechat", "terms_accepted": True},
        "membership-invalid-proof",
    )

    with pytest.raises(ValueError, match="有效图片"):
        service.submit_membership_proof(identity, order["order_no"], b"not-an-image", "image/png")

    assert database.fetch_one(
        "SELECT COUNT(*) count FROM manual_payment_claims WHERE order_no=?", (order["order_no"],)
    )["count"] == 0
    assert not list((tmp_path / "payment-proofs").glob("*"))


def test_membership_proof_http_upload_requires_authenticated_owned_order(
    write_context, tmp_path, monkeypatch
):
    database, identity, service = write_context
    order = service.create_membership_order(
        identity,
        {"plan": "高级版", "cycle": "monthly", "method": "fps", "terms_accepted": True},
        "membership-http-proof",
    )
    image = BytesIO()
    Image.new("RGB", (320, 480), "white").save(image, format="PNG")
    login = AuthService(database).login(
        "writer@example.com", "StrongPass123", "127.0.0.1", "proof-http-test"
    )
    monkeypatch.setattr(
        api_application.state,
        "repository",
        ReadOnlyLegacyRepository(tmp_path / "browser-writes.db"),
    )
    monkeypatch.setattr(api_application.state, "write_service", service, raising=False)
    boundary = b"CicloTradePaymentProofBoundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="proof"; filename="payment.png"\r\n'
        b"Content-Type: image/png\r\n\r\n"
        + image.getvalue()
        + b"\r\n--" + boundary + b"--\r\n"
    )

    def request_with_token(token: str | None) -> Request:
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        headers = [
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/rewrite/v1/membership/orders/{order['order_no']}/proof",
                "path_params": {"order_no": order["order_no"]},
                "headers": headers,
                "app": api_application,
            },
            receive,
        )

    response = asyncio.run(membership_order_proof(request_with_token(login.access_token)))
    assert response.status_code == 201
    assert json.loads(response.body)["status"] == "submitted"
    with pytest.raises(ApiError) as caught:
        asyncio.run(membership_order_proof(request_with_token(None)))
    assert caught.value.status == 401


def test_membership_proof_chunked_body_is_rejected_before_multipart_spooling(
    write_context, tmp_path, monkeypatch
):
    database, identity, service = write_context
    order = service.create_membership_order(
        identity,
        {"plan": "高级版", "cycle": "monthly", "method": "fps", "terms_accepted": True},
        "membership-chunked-proof-limit",
    )
    login = AuthService(database).login(
        "writer@example.com", "StrongPass123", "127.0.0.1", "proof-limit-test"
    )
    monkeypatch.setattr(
        api_application.state,
        "repository",
        ReadOnlyLegacyRepository(tmp_path / "browser-writes.db"),
    )
    monkeypatch.setattr(api_application.state, "write_service", service, raising=False)
    boundary = b"chunked-proof"
    chunks = [
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="proof"; filename="large.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
        + b"x" * (2 * 1024 * 1024),
        b"x" * (2 * 1024 * 1024),
        b"x" * (2 * 1024 * 1024) + b"\r\n--" + boundary + b"--\r\n",
    ]
    sent = []

    async def receive():
        body = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "POST",
        "path": f"/api/rewrite/v1/membership/orders/{order['order_no']}/proof",
        "raw_path": b"",
        "query_string": b"",
        "headers": [
            (b"authorization", f"Bearer {login.access_token}".encode("ascii")),
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8001),
    }
    asyncio.run(api_application(scope, receive, send))

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    assert response_start["status"] == 413
    assert not list((tmp_path / "payment-proofs").glob("*"))
