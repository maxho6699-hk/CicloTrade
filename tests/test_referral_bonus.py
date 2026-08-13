from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from core.auth import AuthService
from core.compat import UTC
from core.database import DatabaseManager
from core.referral_affiliate import (
    ReferralBonusService,
    ReferralCommissionService,
    ReferralProgramService,
    ReferralService,
    ReferralWalletService,
)
from payment.order_service import OrderService


@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(str(tmp_path / "referral-bonus.db"))
    auth = AuthService(database)
    admin = auth.register("bonus-admin@example.com", "CorrectHorse123", "Bonus Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    ReferralProgramService(database).enable(admin["id"])
    return database


def _user(auth: AuthService, name: str, referral: str = "") -> dict:
    user = auth.register(f"{name}@example.com", "CorrectHorse123", name.title(), True, referral)
    assert user
    return user


def _order(database: DatabaseManager, user_id: int, key: str) -> dict:
    service = OrderService(database)
    order = service.create_order(user_id, "标准版", "monthly", "paypal", terms_accepted=True, source="legacy", idempotency_key=key)
    assert service.process_callback(f"event-{key}", order["order_no"], "paid", {})
    return service.get_order(order["order_no"])


def _qualify(database, auth, referrer, snapshot, key, now):
    invite = ReferralService(database).ensure_profile(referrer["id"])["invite_code"]
    referred = _user(auth, f"{key}-user", invite)
    order = _order(database, referred["id"], key)
    attribution = database.fetch_one("SELECT * FROM referral_attributions WHERE referred_user_id=?", (referred["id"],))
    with database.transaction() as conn:
        ReferralBonusService.record_qualification(conn, order={**order, "referral_eligible_snapshot": 1, "referral_bonus_policy_snapshot": json.dumps(snapshot)}, attribution=attribution, now=now)
    return order


def test_partial_refund_reverses_contributor_once(db):
    auth = AuthService(db)
    referrer = _user(auth, "partial-bonus-owner")
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    snapshot = {"enabled": True, "hold_days": 30, "version": 1, "tiers": [{"qualified_count": 1, "cumulative_amount_minor": 10_000}]}
    order = _qualify(db, auth, referrer, snapshot, "partial-bonus-refund", now)
    with db.transaction() as conn:
        ReferralBonusService.record_reversal(conn, source_order_no=order["order_no"], now=now)
        ReferralBonusService.record_reversal(conn, source_order_no=order["order_no"], now=now + timedelta(minutes=1))
    assert db.fetch_one("SELECT status FROM referral_bonus_contributors WHERE source_order_no=?", (order["order_no"],))["status"] == "reversed"
    assert db.fetch_one("SELECT reversed_amount_minor FROM referral_bonus_award_events")["reversed_amount_minor"] == 10_000


def test_period_uses_first_frozen_policy_and_incremental_award(db):
    auth = AuthService(db)
    referrer = _user(auth, "period-lock")
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    first = {"enabled": True, "hold_days": 30, "version": 1, "tiers": [{"qualified_count": 2, "cumulative_amount_minor": 15_000}, {"qualified_count": 3, "cumulative_amount_minor": 20_000}]}
    changed = {"enabled": True, "hold_days": 0, "version": 2, "tiers": [{"qualified_count": 2, "cumulative_amount_minor": 50_000}]}
    _qualify(db, auth, referrer, first, "period-first", now)
    _qualify(db, auth, referrer, changed, "period-second", now + timedelta(minutes=1))
    period = db.fetch_one("SELECT policy_version,hold_days,current_target_minor FROM referral_bonus_periods")
    award = db.fetch_one("SELECT policy_version,award_delta_minor,status FROM referral_bonus_award_events")
    assert period == {"policy_version": "1", "hold_days": 30, "current_target_minor": 15_000}
    assert award == {"policy_version": "1", "award_delta_minor": 15_000, "status": "pending"}


def test_matured_bonus_clawback_cancels_open_withdrawal_and_paid_debt_blocks_new_request(db):
    auth = AuthService(db)
    referrer = _user(auth, "bonus-withdrawal")
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    snapshot = {"enabled": True, "hold_days": 0, "version": 1, "tiers": [{"qualified_count": 1, "cumulative_amount_minor": 30_000}]}
    order = _qualify(db, auth, referrer, snapshot, "bonus-open", now)
    wallet = ReferralWalletService(db)
    assert ReferralCommissionService(db).release_due(referrer["id"], now + timedelta(seconds=1)) == 1
    request = wallet.request_withdrawal(referrer["id"], 20_000, "bonus-open-withdrawal-0001")
    with db.transaction() as conn:
        ReferralBonusService.record_reversal(conn, source_order_no=order["order_no"], now=now + timedelta(minutes=1))
    assert db.fetch_one("SELECT status FROM referral_withdrawal_requests WHERE public_id=?", (request["public_id"],))["status"] == "system_cancelled"
