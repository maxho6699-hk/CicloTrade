from __future__ import annotations

import gzip
import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ops/scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("build_web_release", "build_web_release.py")
verifier = _load("verify_web_release", "verify_web_release.py")


def _run(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _source_repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir(parents=True)
    _run(root, "init")
    _run(root, "config", "user.email", "release-test@example.invalid")
    _run(root, "config", "user.name", "Release Test")
    files = {
        "app.py": b"app = object()\n",
        "asgi_app.py": b"application = object()\n",
        "config.yaml": b"environment: test\n",
        "config/settings.py": b"SETTING = 'test'\n",
        "requirements.txt": b"fastapi==0.1\n",
        "backtest/engine.py": b"pass\n",
        "core/domain.py": b"pass\n",
        "notification/service.py": b"pass\n",
        "payment/proof_storage.py": b"pass\n",
        "sandbox_runner/run.py": b"pass\n",
        "scheduler/tasks.py": b"pass\n",
        "strategies/base.py": b"pass\n",
        "strategy_client/client.py": b"pass\n",
        "trading/orders.py": b"pass\n",
        "ui/page.py": b"pass\n",
        "src/apps/api/routes.py": b"pass\n",
        "src/packages/contracts/api.py": b"pass\n",
        "src/apps/web/package-lock.json": b'{"lockfileVersion":3}\n',
        "src/apps/web/dist/index.html": b'<script src="/assets/app-abcdef12.js"></script>\n',
        "src/apps/web/dist/assets/app-abcdef12.js": b"console.log('ok')\n",
        "migrations/0032_membership_promotions.sql": b"SELECT 32;\n",
        "migrations/0033_membership_promotion_settlement.sql": b"SELECT 33;\n",
        "migrations/0034_personal_paper.sql": b"SELECT 34;\n",
        "migrations/0035_entitlement_policy_versions.sql": b"SELECT 35;\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _run(root, "add", ".")
    _run(root, "commit", "-m", "fixture")
    return root


def _bundle(root: Path, tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    artifact = tmp_path / "web-release.tar.gz"
    manifest = tmp_path / "web-release.manifest.json"
    baseline = _run(root, "rev-parse", "HEAD")
    data = builder.build_release(root, artifact, manifest, baseline=baseline, source_date_epoch=1)
    return artifact, manifest, data


def _write_manifest(path: Path, data: dict[str, object]) -> None:
    path.write_bytes(verifier.canonical_json(data))


def _rebind_artifact(path: Path, manifest: Path) -> None:
    data = verifier.read_manifest(manifest)
    data["artifact"] = {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
    _write_manifest(manifest, data)


def _rewrite_tar(source: Path, target: Path, *, remove: str | None = None, add: dict[str, bytes] | None = None, link: bool = False) -> None:
    with tarfile.open(source, "r:gz") as old, tarfile.open(target, "w:gz", compresslevel=9) as new:
        for member in old.getmembers():
            if member.name == remove:
                continue
            payload = old.extractfile(member).read() if member.isfile() else b""
            new.addfile(member, BytesIO(payload) if member.isfile() else None)
        for name, content in (add or {}).items():
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.size = len(content)
            new.addfile(info, BytesIO(content))
        if link:
            info = tarfile.TarInfo("linked")
            info.type = tarfile.SYMTYPE
            info.linkname = "app.py"
            new.addfile(info)


def test_build_is_reproducible_and_verifies(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, data = _bundle(root, tmp_path)
    second = tmp_path / "second.tar.gz"
    second_manifest = tmp_path / "second.manifest.json"
    builder.build_release(root, second, second_manifest, baseline=data["source"]["baseline"], source_date_epoch=1)

    assert artifact.read_bytes() == second.read_bytes()
    assert verifier.verify_release(root, artifact, manifest) == []
    assert data["migrations"]["required"][-1] == "0035_entitlement_policy_versions.sql"
    assert data["lifecycle"] == {"allowed_actions": ["restart"], "service": "ciclotrade-rewrite-api.service"}


def test_archive_payload_is_the_exact_git_blob_bytes(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    expected = subprocess.run(["git", "-C", str(root), "show", "HEAD:app.py"], check=True, capture_output=True).stdout
    with tarfile.open(artifact, "r:gz") as archive:
        member = archive.getmember("app.py")
        assert archive.extractfile(member).read() == expected
    assert verifier.verify_release(root, artifact, manifest) == []


def test_builder_rechecks_clean_source_after_archive_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _source_repo(tmp_path)
    artifact = tmp_path / "release.tar.gz"
    manifest = tmp_path / "release.json"
    original = builder._assert_source_snapshot
    calls = 0

    def mutate_after_first_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "app.py").write_text("changed\n", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_assert_source_snapshot", mutate_after_first_check)
    with pytest.raises(builder.ReleaseBuildError, match="source repository is not clean"):
        builder.build_release(root, artifact, manifest, baseline=_run(root, "rev-parse", "HEAD"), source_date_epoch=1)


def test_rejects_tampered_manifest_archive_and_blob_binding(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["files"][0]["source"] = "0" * 40
    _write_manifest(manifest, data)
    assert any("tracked source" in item for item in verifier.verify_release(root, artifact, manifest))


def test_rejects_manifest_extra_keys_and_collisions(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["extra"] = True
    data["files"][0]["extra"] = True
    data["files"].append({**data["files"][0], "path": "Ａpp.py"})
    _write_manifest(manifest, data)
    violations = verifier.verify_release(root, artifact, manifest)
    assert "manifest has unexpected top-level keys" in violations
    assert "manifest file entry has unexpected keys" in violations
    assert "manifest has casefold or Unicode path collisions" in violations


def test_rejects_noncanonical_gzip_and_tar_metadata(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    broken = tmp_path / "broken.tar.gz"
    raw = bytearray(artifact.read_bytes())
    raw[4:8] = (2).to_bytes(4, "little")
    broken.write_bytes(raw)
    _rebind_artifact(broken, manifest)
    assert "gzip metadata is not deterministic" in verifier.verify_release(root, broken, manifest)

    rewritten = tmp_path / "rewritten.tar.gz"
    with tarfile.open(artifact, "r:gz") as old, rewritten.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in old.getmembers():
                    payload = old.extractfile(member).read()
                    member.mtime = 2
                    new.addfile(member, BytesIO(payload))
    _rebind_artifact(rewritten, manifest)
    assert "archive member metadata is not deterministic" in verifier.verify_release(root, rewritten, manifest)


def test_archive_streaming_stops_before_reading_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    member = tarfile.TarInfo("bomb")
    member.size = verifier.MAX_MEMBER_BYTES + 1

    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter((member,))

        def extractfile(self, _member):
            raise AssertionError("bomb payload must not be read")

    monkeypatch.setattr(verifier, "_gzip_metadata", lambda *args: [])
    monkeypatch.setattr(verifier.tarfile, "open", lambda *args, **kwargs: FakeArchive())
    _, violations = verifier._archive_members(Path("unused.tar.gz"), 1)
    assert "archive member exceeds size limit" in violations


def test_rejects_mode_size_and_member_count_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["files"][0]["mode"] = 0o600
    data["files"][0]["size"] = verifier.MAX_MEMBER_BYTES + 1
    _write_manifest(manifest, data)
    violations = verifier.verify_release(root, artifact, manifest)
    assert "manifest file mode is invalid" in violations
    assert "manifest file size is invalid" in violations

    monkeypatch.setattr(verifier, "MAX_MEMBERS", 1)
    assert "archive has too many members" in verifier.verify_release(root, artifact, manifest)
