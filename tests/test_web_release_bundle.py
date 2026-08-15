from __future__ import annotations

import gzip
import hashlib
import importlib.util
from io import BytesIO
import os
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
        "data/__init__.py": b"\n",
        "data/datasource.py": b"class DataSource: pass\n",
        "data/akshare_adapter.py": b"class AkshareAdapter: pass\n",
        "data/polygon_adapter.py": b"class PolygonAdapter: pass\n",
        "data/wrdata_adapter.py": b"class WrdataAdapter: pass\n",
        "data/yfinance_adapter.py": b"class YfinanceAdapter: pass\n",
        "data/opend_adapter.py": b"class OpenDAdapter: pass\n",
        "data/opend_control.py": b"class OpenDControl: pass\n",
        "data/opend_probe.py": b"class OpenDProbe: pass\n",
        "data/cache/fixture.py": b"pass\n",
        "data/fixture.db": b"SQLite format 3\x00",
        "data/payment-proofs/receipt.json": b"{}\n",
        "core/domain.py": b"pass\n",
        "notification/service.py": b"pass\n",
        "payment/proof_storage.py": b"pass\n",
        "sandbox_runner/run.py": b"pass\n",
        "sandbox_runner/README.md": b"systemctl restart legacy.service\nsecret=replace-me\n",
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
        "migrations/backtest/0012_expanded_research_receipts.sql": b"SELECT 12;\n",
        "migrations/backtest/0013_expanded_research_invalidations.sql": b"SELECT 13;\n",
        "migrations/backtest/0014_expanded_research_projection_indexes.sql": b"SELECT 14;\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for name, module in (("build_web_release.py", builder), ("verify_web_release.py", verifier)):
        path = root / "ops/scripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(module.__file__).read_bytes())
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
    with tarfile.open(source, "r:gz") as old, target.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in old.getmembers():
                    if member.name == remove:
                        continue
                    payload = old.extractfile(member).read() if member.isfile() else b""
                    new.addfile(member, BytesIO(payload) if member.isfile() else None)
                for name, content in (add or {}).items():
                    info = tarfile.TarInfo(name)
                    info.mode, info.mtime = 0o644, 1
                    info.uid = info.gid = 0
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
    assert data["migrations"]["required_backtest"] == [
        "0012_expanded_research_receipts.sql",
        "0013_expanded_research_invalidations.sql",
        "0014_expanded_research_projection_indexes.sql",
    ]
    assert data["lifecycle"] == {"allowed_actions": ["restart"], "service": "ciclotrade-rewrite-api.service"}


def test_builder_excludes_markdown_runtime_instructions(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, data = _bundle(root, tmp_path)
    assert "sandbox_runner/README.md" not in {item["path"] for item in data["files"]}
    with tarfile.open(artifact, "r:gz") as archive:
        assert "sandbox_runner/README.md" not in archive.getnames()
    assert verifier.verify_release(root, artifact, manifest) == []


def test_release_includes_data_runtime_modules_but_excludes_opend_and_sensitive_data(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, data = _bundle(root, tmp_path)
    paths = {item["path"] for item in data["files"]}
    expected = {
        "data/__init__.py",
        "data/datasource.py",
        "data/akshare_adapter.py",
        "data/polygon_adapter.py",
        "data/wrdata_adapter.py",
        "data/yfinance_adapter.py",
    }
    assert expected <= paths
    assert not {path for path in paths if path.startswith("data/opend_")}
    assert not {path for path in paths if "/cache/" in path or path.endswith(".db") or "payment-proofs" in path}
    with tarfile.open(artifact, "r:gz") as archive:
        archive_paths = set(archive.getnames())
    assert expected <= archive_paths
    assert not {path for path in archive_paths if path.startswith("data/opend_")}
    assert not {path for path in archive_paths if "/cache/" in path or path.endswith(".db") or "payment-proofs" in path}
    assert verifier.verify_release(root, artifact, manifest) == []


def test_builder_requires_expanded_research_backtest_migration(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    required = root / "migrations/backtest/0012_expanded_research_receipts.sql"
    required.unlink()
    _run(root, "add", "-u")
    _run(root, "commit", "-m", "remove required backtest migration")
    with pytest.raises(builder.ReleaseBuildError, match="missing required runtime inputs"):
        builder.build_release(
            root,
            tmp_path / "release.tar.gz",
            tmp_path / "release.json",
            baseline=_run(root, "rev-parse", "HEAD"),
            source_date_epoch=1,
        )


def test_builder_requires_expanded_research_invalidation_migration(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    required = root / "migrations/backtest/0013_expanded_research_invalidations.sql"
    required.unlink()
    _run(root, "add", "-u")
    _run(root, "commit", "-m", "remove required invalidation migration")
    with pytest.raises(builder.ReleaseBuildError, match="missing required runtime inputs"):
        builder.build_release(
            root,
            tmp_path / "release.tar.gz",
            tmp_path / "release.json",
            baseline=_run(root, "rev-parse", "HEAD"),
            source_date_epoch=1,
        )


def test_builder_requires_expanded_research_projection_index_migration(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    required = root / "migrations/backtest/0014_expanded_research_projection_indexes.sql"
    required.unlink()
    _run(root, "add", "-u")
    _run(root, "commit", "-m", "remove required projection index migration")
    with pytest.raises(builder.ReleaseBuildError, match="missing required runtime inputs"):
        builder.build_release(
            root,
            tmp_path / "release.tar.gz",
            tmp_path / "release.json",
            baseline=_run(root, "rev-parse", "HEAD"),
            source_date_epoch=1,
        )


def test_verifier_requires_backtest_migration_in_manifest_and_archive(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["files"] = [
        item for item in data["files"]
        if item["path"] != "migrations/backtest/0014_expanded_research_projection_indexes.sql"
    ]
    _write_manifest(manifest, data)
    violations = verifier.verify_release(root, artifact, manifest)
    assert "manifest is missing required backtest migration" in violations
    assert "archive has paths absent from manifest" in violations

    removed = tmp_path / "missing-backtest.tar.gz"
    _rewrite_tar(artifact, removed, remove="migrations/backtest/0014_expanded_research_projection_indexes.sql")
    _rebind_artifact(removed, manifest)
    violations = verifier.verify_release(root, removed, manifest)
    assert "archive is missing required backtest migration" in violations


def test_verifier_rejects_rebound_archive_with_unapproved_backtest_migration(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    injected = tmp_path / "injected.tar.gz"
    _rewrite_tar(artifact, injected, add={"migrations/backtest/0099_not_required.sql": b"SELECT 99;\n"})
    _rebind_artifact(injected, manifest)
    violations = verifier.verify_release(root, injected, manifest)
    assert "archive contains a path outside the release allowlist" in violations


@pytest.mark.parametrize("path", [
    "migrations/Backtest/0012_expanded_research_receipts.sql",
    "Tests/fixture.py",
    "Cache/data.bin",
    "Logs/app.log",
    "Worker/job.py",
    "Ｔｅｓｔｓ/fixture.py",
    "core\\Tests\\fixture.py",
    "core//Tests/fixture.py",
    "core/./Tests/fixture.py",
])
def test_builder_and_verifier_forbid_casefolded_and_normalized_paths(path: str) -> None:
    assert builder._is_forbidden(path)
    assert verifier._forbidden_path(path)


@pytest.mark.parametrize("path, content", [
    ("core/auth.py", b"token = auth_header\npassword = payload['password']\nsecret = os.getenv('SECRET')\n"),
    ("src/apps/api/earnings_read_model.py", b"secret_sql = 'SELECT secret FROM account'\n"),
    ("core/auth.py", b"secret = 'hardcoded-secret-value'\n"),
    ("core/auth.py", b"password: bytes = b'hardcoded-password'\n"),
    ("core/auth.py", b"api_secret = 'hardcoded-api-secret'\n"),
])
def test_secret_detection_is_ast_aware_for_python(path: str, content: bytes) -> None:
    expected = "hardcoded" in content.decode("utf-8") and "secret_sql" not in content.decode("utf-8")
    assert verifier._contains_secret(path, content) is expected


def test_secret_detection_rejects_private_key_marker_for_every_language() -> None:
    content = b"-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n"
    assert verifier._contains_secret("core/key.py", content)
    assert verifier._contains_secret("config/settings.yaml", content)


@pytest.mark.parametrize("path, content", [
    ("config/settings.yaml", b"api_key: hardcoded-config-value\n"),
    ("src/apps/web/settings.js", b"const token = 'hardcoded-js-value';\n"),
])
def test_secret_detection_remains_conservative_for_non_python_literals(path: str, content: bytes) -> None:
    assert verifier._contains_secret(path, content)


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


def test_builder_rechecks_source_after_manifest_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _source_repo(tmp_path)
    artifact = tmp_path / "release.tar.gz"
    manifest = tmp_path / "release.json"
    original = builder._assert_source_snapshot
    calls = 0

    def mutate_after_manifest(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            (root / "app.py").write_text("changed\n", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_assert_source_snapshot", mutate_after_manifest)
    with pytest.raises(builder.ReleaseBuildError, match="source repository is not clean"):
        builder.build_release(root, artifact, manifest, baseline=_run(root, "rev-parse", "HEAD"), source_date_epoch=1)


def test_rejects_tampered_manifest_archive_and_blob_binding(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["files"][0]["source"] = "0" * 40
    _write_manifest(manifest, data)
    assert any("tracked source" in item for item in verifier.verify_release(root, artifact, manifest))


def test_rejects_rebound_artifact_when_member_differs_from_git_blob(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    rewritten = tmp_path / "rewritten.tar.gz"
    with tarfile.open(artifact, "r:gz") as old, rewritten.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in old.getmembers():
                    content = old.extractfile(member).read()
                    if member.name == "app.py":
                        content = b"tampered = True\n"
                        member.size = len(content)
                    new.addfile(member, BytesIO(content))
    data = verifier.read_manifest(manifest)
    app = next(item for item in data["files"] if item["path"] == "app.py")
    app["sha256"] = hashlib.sha256(b"tampered = True\n").hexdigest()
    app["size"] = len(b"tampered = True\n")
    _write_manifest(manifest, data)
    _rebind_artifact(rewritten, manifest)
    assert "archive member does not match tracked Git blob" in verifier.verify_release(root, rewritten, manifest)


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


def test_rejects_archive_member_order_unicode_collision_and_unsafe_mode(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)

    unordered = tmp_path / "unordered.tar.gz"
    with tarfile.open(artifact, "r:gz") as old, unordered.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in reversed(old.getmembers()):
                    new.addfile(member, BytesIO(old.extractfile(member).read()))
    _rebind_artifact(unordered, manifest)
    assert "archive members are not sorted" in verifier.verify_release(root, unordered, manifest)

    collision = tmp_path / "collision.tar.gz"
    with tarfile.open(artifact, "r:gz") as old, collision.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in old.getmembers():
                    new.addfile(member, BytesIO(old.extractfile(member).read()))
                extra = tarfile.TarInfo("Ａpp.py")
                extra.size, extra.mode, extra.mtime = 4, 0o644, 1
                extra.uid = extra.gid = 0
                extra.uname = extra.gname = ""
                new.addfile(extra, BytesIO(b"pass"))
    _rebind_artifact(collision, manifest)
    assert "archive has casefold or Unicode path collisions" in verifier.verify_release(root, collision, manifest)

    unsafe_mode = tmp_path / "unsafe-mode.tar.gz"
    with tarfile.open(artifact, "r:gz") as old, unsafe_mode.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=1) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as new:
                for member in old.getmembers():
                    if member.name == "app.py":
                        member.mode = 0o600
                    new.addfile(member, BytesIO(old.extractfile(member).read()))
    _rebind_artifact(unsafe_mode, manifest)
    assert "archive member metadata is not deterministic" in verifier.verify_release(root, unsafe_mode, manifest)


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


def test_manifest_integer_fields_reject_booleans(tmp_path: Path) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    data = verifier.read_manifest(manifest)
    data["artifact"]["size"] = True
    data["files"][0]["size"] = True
    data["files"][0]["mode"] = True
    data["source_date_epoch"] = True
    _write_manifest(manifest, data)
    violations = verifier.verify_release(root, artifact, manifest)
    assert "manifest artifact is invalid" in violations
    assert "manifest file size is invalid" in violations
    assert "manifest file mode is invalid" in violations
    assert "manifest SOURCE_DATE_EPOCH is invalid" in violations


def test_verifier_rejects_manifest_or_artifact_path_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _source_repo(tmp_path)
    artifact, manifest, _ = _bundle(root, tmp_path)
    replacement = tmp_path / "replacement.tar.gz"
    replacement.write_bytes(artifact.read_bytes())
    original_members = verifier._archive_members

    def replace_artifact(*args, **kwargs):
        os.replace(replacement, artifact)
        return original_members(*args, **kwargs)

    monkeypatch.setattr(verifier, "_archive_members", replace_artifact)
    assert "artifact changed during verification" in verifier.verify_release(root, artifact, manifest)
    monkeypatch.setattr(verifier, "_archive_members", original_members)

    replacement_manifest = tmp_path / "replacement.json"
    replacement_manifest.write_bytes(manifest.read_bytes())
    original_shape = verifier._validate_manifest_shape

    def replace_manifest(*args, **kwargs):
        os.replace(replacement_manifest, manifest)
        return original_shape(*args, **kwargs)

    monkeypatch.setattr(verifier, "_validate_manifest_shape", replace_manifest)
    assert "manifest changed during verification" in verifier.verify_release(root, artifact, manifest)
