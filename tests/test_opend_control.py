from __future__ import annotations

import subprocess

import pytest

import data.opend_control as opend_control
from data.opend_control import OpenDControlError, OpenDVerificationController
from ui.pages.admin import _captcha_is_expired


PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"


def test_captcha_refresh_returns_only_a_new_valid_image(tmp_path, monkeypatch):
    captcha = tmp_path / "PicVerifyCode.png"
    captcha.write_bytes(PNG + b"old")
    controller = OpenDVerificationController(captcha)

    def refresh(command: str) -> str:
        assert command == "req_pic_verify_code"
        captcha.write_bytes(PNG + b"new")
        return "OK"

    monkeypatch.setattr(controller, "_exchange", refresh)
    assert controller.request_captcha() == PNG + b"new"


def test_captcha_refresh_accepts_opend_jpeg_with_png_filename(tmp_path, monkeypatch):
    captcha = tmp_path / "PicVerifyCode.png"
    captcha.write_bytes(PNG + b"old")
    controller = OpenDVerificationController(captcha)

    def refresh(command: str) -> str:
        assert command == "req_pic_verify_code"
        captcha.write_bytes(JPEG + b"new")
        return "OK"

    monkeypatch.setattr(controller, "_exchange", refresh)
    assert controller.request_captcha() == JPEG + b"new"


def test_captcha_refresh_rejects_unsupported_image_content(tmp_path, monkeypatch):
    captcha = tmp_path / "PicVerifyCode.png"
    captcha.write_bytes(PNG + b"old")
    controller = OpenDVerificationController(captcha)

    def refresh(_command: str) -> str:
        captcha.write_bytes(b"not-an-image")
        return "OK"

    monkeypatch.setattr(controller, "_exchange", refresh)
    with pytest.raises(OpenDControlError, match="图片无效"):
        controller.request_captcha()


def test_captcha_submission_is_strict_and_never_accepts_other_commands(monkeypatch, tmp_path):
    controller = OpenDVerificationController(tmp_path / "unused.png")
    sent: list[str] = []
    monkeypatch.setattr(controller, "_exchange", lambda command: sent.append(command) or "OK")

    with pytest.raises(ValueError, match="4 位"):
        controller.submit_captcha("abc")
    with pytest.raises(ValueError, match="4 位"):
        controller.submit_captcha("ab-c")

    assert controller.submit_captcha("aB12").startswith("验证码已提交")
    assert sent == ["input_pic_verify_code -code=aB12"]

    with pytest.raises(OpenDControlError, match="不允许"):
        OpenDVerificationController()._exchange("shutdown")
    with pytest.raises(OpenDControlError, match="不允许"):
        OpenDVerificationController()._exchange(
            "input_pic_verify_code -code=AB12\r\nshutdown"
        )


def test_captcha_failure_response_is_not_reported_as_success(monkeypatch, tmp_path):
    controller = OpenDVerificationController(tmp_path / "unused.png")
    monkeypatch.setattr(controller, "_exchange", lambda _command: "验证码错误，登录失败")
    with pytest.raises(OpenDControlError, match="未通过"):
        controller.submit_captcha("AB12")


def test_probe_uses_a_two_second_child_process_timeout_and_short_cache(monkeypatch):
    calls: list[dict] = []

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 2, stdout="VERIFICATION_REQUIRED\n", stderr="")

    opend_control.clear_opend_probe_cache()
    monkeypatch.setattr(opend_control.subprocess, "run", run)
    first = opend_control.probe_opend_status(force=True)
    second = opend_control.probe_opend_status()

    assert first.state == "verification_required"
    assert second == first
    assert len(calls) == 1
    assert calls[0]["timeout"] == 2.0
    assert calls[0]["command"][1:3] == ["-m", "data.opend_probe"]


def test_probe_timeout_returns_a_safe_fallback_state(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("probe", 2)

    opend_control.clear_opend_probe_cache()
    monkeypatch.setattr(opend_control.subprocess, "run", timeout)
    status = opend_control.probe_opend_status(force=True)
    assert status.state == "unavailable"
    assert "备用行情" in status.message


def test_admin_rejects_expired_or_invalid_captcha_timestamps():
    assert _captcha_is_expired(100.0, now=219.9) is False
    assert _captcha_is_expired(100.0, now=220.0) is True
    assert _captcha_is_expired(221.0, now=220.0) is True
    assert _captcha_is_expired(None, now=220.0) is True
