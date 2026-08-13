from __future__ import annotations

from pathlib import Path
import hashlib

from ops.scripts.verify_nginx_route_cutover import verify


ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_nginx_config_is_accepted() -> None:
    assert verify(ROOT / "ops" / "nginx-ciclotrade.conf") == []


def test_candidate_is_bound_to_baseline_and_hash(tmp_path: Path) -> None:
    candidate = ROOT / "ops" / "nginx-ciclotrade.conf"
    source = candidate.read_text(encoding="utf-8")
    baseline = tmp_path / "baseline.conf"
    baseline.write_text(source.replace("|paper|more", ""), encoding="utf-8")
    expected = hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert verify(candidate, baseline=baseline, expected_sha256=expected) == []


def test_extra_candidate_change_is_rejected(tmp_path: Path) -> None:
    source = (ROOT / "ops" / "nginx-ciclotrade.conf").read_text(encoding="utf-8")
    baseline = tmp_path / "baseline.conf"
    candidate = tmp_path / "candidate.conf"
    baseline.write_text(source.replace("|paper|more", ""), encoding="utf-8")
    candidate.write_text(source.replace("client_max_body_size 5m", "client_max_body_size 6m"), encoding="utf-8")
    assert verify(candidate, baseline=baseline) == ["candidate changes more than paper/more routes"]


def test_missing_route_is_rejected(tmp_path: Path) -> None:
    source = (ROOT / "ops" / "nginx-ciclotrade.conf").read_text(encoding="utf-8")
    config = tmp_path / "nginx.conf"
    config.write_text(source.replace("|paper|more", "|paper"), encoding="utf-8")
    assert verify(config) == ["missing SPA routes: more"]


def test_lifecycle_command_is_rejected(tmp_path: Path) -> None:
    source = (ROOT / "ops" / "nginx-ciclotrade.conf").read_text(encoding="utf-8")
    config = tmp_path / "nginx.conf"
    config.write_text(source + "\n# systemctl reload nginx\n", encoding="utf-8")
    assert verify(config) == ["config contains a lifecycle command"]


def test_wrong_candidate_hash_is_rejected() -> None:
    candidate = ROOT / "ops" / "nginx-ciclotrade.conf"
    assert verify(candidate, expected_sha256="0" * 64) == ["candidate SHA-256 mismatch"]
