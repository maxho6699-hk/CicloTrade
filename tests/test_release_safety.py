from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tarfile
from io import BytesIO
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_safety", ROOT / "ops/scripts/verify_release_safety.py")
assert spec and spec.loader
release_safety = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release_safety
spec.loader.exec_module(release_safety)


def _tar(path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "target"
            archive.addfile(info)
    return path


def test_release_surface_skips_legal_opend_source_and_allows_only_api_restart(tmp_path, monkeypatch):
    (tmp_path / "ops/opend").mkdir(parents=True)
    (tmp_path / "ops/opend/control.sh").write_text("systemctl restart futu-opend.service")
    allowed = tmp_path / "ops/restart.sh"
    allowed.parent.mkdir(exist_ok=True)
    allowed.write_text("sudo systemctl restart ciclotrade-rewrite-api.service")
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [tmp_path / "ops/opend/control.sh", allowed])
    assert release_safety.scan_tracked_surface(tmp_path) == []


@pytest.mark.parametrize("command", [
    "systemctl restart futu-opend.service",
    "systemctl reload ciclotrade-rewrite-api.service",
    "systemctl try-reload ciclotrade-rewrite-api.service",
    "systemctl reload-or-restart ciclotrade-rewrite-api.service",
    "systemctl restart legacy-api.service",
    "service worker stop",
    "futu-opend relogin",
    "FutuOpenD login",
    "FutuOpenD.exe login account",
    "sudo -u opend systemctl restart ciclotrade-rewrite-api.service",
    "systemctl --host=remote restart ciclotrade-rewrite-api.service",
    "systemctl -H remote restart ciclotrade-rewrite-api.service",
    "systemctl --host remote restart ciclotrade-rewrite-api.service",
    "echo x | systemctl stop legacy.service",
    "echo $(systemctl reload ciclotrade-rewrite-api.service)",
    "futu opend login account",
    "FutuOpenD.exe --config x login account",
    "ssh prod systemctl restart ciclotrade-rewrite-api.service",
    "env PATH=/tmp systemctl restart ciclotrade-rewrite-api.service",
    'bash -c "systemctl restart ciclotrade-rewrite-api.service"',
])
def test_rejects_opend_and_non_whitelisted_lifecycle_commands(tmp_path, monkeypatch, command):
    source = tmp_path / "ops/deploy.sh"
    source.parent.mkdir(exist_ok=True)
    source.write_text(command)
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path)


def test_policy_prose_does_not_count_as_a_lifecycle_invocation(tmp_path, monkeypatch):
    source = tmp_path / "docs/rewrite/policy.md"
    source.parent.mkdir(parents=True)
    source.write_text("The OpenD lifecycle is prohibited; do not reload legacy services.")
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path) == []


def test_quote_fragmented_and_process_spawn_lifecycle_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "ops/deploy.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text('subprocess.run(["sys" "temctl", "re" "start", "legacy.service"])')
    shell = tmp_path / "ops/deploy.sh"
    shell.write_text('sys"temctl" re"start" legacy.service')
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source, shell])
    violations = release_safety.scan_tracked_surface(tmp_path)
    assert any("process-spawn lifecycle" in item for item in violations)
    assert any("non-whitelisted service action" in item for item in violations)


def test_policy_prose_with_lifecycle_words_is_not_an_invocation(tmp_path, monkeypatch):
    source = tmp_path / "docs/rewrite/policy.md"
    source.parent.mkdir(parents=True)
    source.write_text("Policy prose forbids systemctl restart and any OpenD lifecycle action.")
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path) == []


@pytest.mark.parametrize("contents", [
    b"\xff\xfes\x00y\x00s\x00t\x00e\x00m\x00c\x00t\x00l\x00 \x00r\x00e\x00l\x00o\x00a\x00d\x00 \x00c\x00i\x00c\x00l\x00o\x00t\x00r\x00a\x00d\x00e\x00-\x00r\x00e\x00w\x00r\x00i\x00t\x00e\x00-\x00a\x00p\x00i\x00.\x00s\x00e\x00r\x00v\x00i\x00c\x00e\x00",
    b"systemctl re\x00start ciclotrade-rewrite-api.service",
])
def test_rejects_utf16_and_nul_obfuscation(tmp_path, monkeypatch, contents):
    source = tmp_path / "ops/deploy.sh"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(contents)
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path)


def test_allows_utf16_and_optioned_rewrite_api_restart(tmp_path, monkeypatch):
    source = tmp_path / "ops/restart.sh"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes("sudo systemctl --no-block restart ciclotrade-rewrite-api.service".encode("utf-16"))
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path) == []


def test_rejects_chained_second_lifecycle_command(tmp_path, monkeypatch):
    source = tmp_path / "ops/restart.sh"
    source.parent.mkdir(exist_ok=True)
    source.write_text("systemctl restart ciclotrade-rewrite-api.service && systemctl stop legacy.service")
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path)


def test_rejects_extensionless_shebang_script(tmp_path, monkeypatch):
    source = tmp_path / "ops/release"
    source.parent.mkdir(exist_ok=True)
    source.write_text("#!/bin/sh\nsystemctl reload ciclotrade-rewrite-api.service\n")
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path)


@pytest.mark.parametrize("contents", [
    "```sh\nsystemctl reload ciclotrade-rewrite-api.service\n```",
    "Run `systemctl reload ciclotrade-rewrite-api.service` after packaging.",
])
def test_rejects_copyable_markdown_lifecycle_commands(tmp_path, monkeypatch, contents):
    source = tmp_path / "docs/rewrite/runbook.md"
    source.parent.mkdir(parents=True)
    source.write_text(contents)
    monkeypatch.setattr(release_safety, "tracked_release_surface", lambda _root: [source])
    assert release_safety.scan_tracked_surface(tmp_path)


def test_rejects_forbidden_paths_and_archive_member_path_traversal(tmp_path):
    bad_path = _tar(tmp_path / "bad-path.tar.gz", {"ops/opend/FutuOpenD.xml": b"safe"})
    traversal = _tar(tmp_path / "traversal.tar.gz", {"../futu-opend.service": b"safe"})
    assert release_safety.scan_artifact(bad_path)
    assert release_safety.scan_artifact(traversal)


def test_rejects_archive_commands_and_symbolic_links(tmp_path):
    command = _tar(tmp_path / "command.tar.gz", {"deploy.sh": b"systemctl restart futu-opend.service"})
    link = _tar(tmp_path / "link.tar.gz", {"app.py": b"pass"}, symlink="linked")
    assert release_safety.scan_artifact(command)
    assert release_safety.scan_artifact(link)


def test_rejects_extensionless_and_utf16_archive_members(tmp_path):
    extensionless = _tar(tmp_path / "extensionless.tar.gz", {"release": b"#!/bin/sh\nsystemctl stop legacy.service"})
    utf16 = _tar(tmp_path / "utf16.tar.gz", {"deploy.sh": "systemctl stop legacy.service".encode("utf-16")})
    assert release_safety.scan_artifact(extensionless)
    assert release_safety.scan_artifact(utf16)


def test_rejects_path_traversal_and_opend_entries_in_artifact_manifest(tmp_path):
    manifest = tmp_path / "release.MANIFEST.txt"
    manifest.write_text("a" * 64 + "  ../futu-opend.service\n" + "b" * 64 + "  ops/opend/FutuOpenD.xml\n")
    assert release_safety.scan_manifest(manifest)


def test_manifest_rejects_exact_and_unicode_collisions(tmp_path):
    manifest = tmp_path / "release.MANIFEST.txt"
    manifest.write_text("a" * 64 + "  app.py\n" + "b" * 64 + "  app.py\n" + "c" * 64 + "  Ａpp.py\n")
    violations = release_safety.scan_manifest(manifest)
    assert "manifest has duplicate member paths" in violations
    assert "manifest has casefold or Unicode member path collisions" in violations


def test_rejects_zip_symlink_and_oversized_member_without_extracting(tmp_path, monkeypatch):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        link = zipfile.ZipInfo("linked")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "target")
    assert release_safety.scan_artifact(archive_path)
    monkeypatch.setattr(release_safety, "MAX_MEMBER_BYTES", 1)
    oversized = _tar(tmp_path / "large.tar.gz", {"app.py": b"12"})
    assert release_safety.scan_artifact(oversized)


def test_rejects_archive_special_members_and_path_collisions(tmp_path):
    archive_path = tmp_path / "special.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        directory = tarfile.TarInfo("directory")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        hardlink = tarfile.TarInfo("hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "target"
        archive.addfile(hardlink)
        for name in ("app.py", "Ａpp.py"):
            info = tarfile.TarInfo(name)
            info.size = 4
            archive.addfile(info, BytesIO(b"pass"))
    violations = release_safety.scan_artifact(archive_path)
    assert any("non-regular archive entry" in item for item in violations)
    assert "artifact has casefold or Unicode member path collisions" in violations


def test_tar_scanner_streams_and_fails_before_reading_oversized_member(tmp_path, monkeypatch):
    member = tarfile.TarInfo("bomb")
    member.size = release_safety.MAX_MEMBER_BYTES + 1

    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter((member,))

        def extractfile(self, _member):
            raise AssertionError("bomb payload must not be read")

    path = tmp_path / "unused.tar.gz"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(release_safety.tarfile, "is_tarfile", lambda _path: True)
    monkeypatch.setattr(release_safety.tarfile, "open", lambda *args, **kwargs: FakeArchive())
    assert release_safety.scan_artifact(path)


def test_archive_aggregate_limit_is_checked_before_triggering_member_read(tmp_path, monkeypatch):
    member = tarfile.TarInfo("bomb")
    member.size = 2

    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter((member,))

        def extractfile(self, _member):
            raise AssertionError("aggregate-limit payload must not be read")

    path = tmp_path / "unused.tar.gz"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(release_safety, "MAX_TOTAL_MEMBER_BYTES", 1)
    monkeypatch.setattr(release_safety.tarfile, "is_tarfile", lambda _path: True)
    monkeypatch.setattr(release_safety.tarfile, "open", lambda *args, **kwargs: FakeArchive())
    assert release_safety.scan_artifact(path)


def test_receipt_contract_is_read_only_and_has_no_connection_fields():
    contract = release_safety.receipt_contract()
    assert contract["allowed_action"] == "restart"
    assert contract["read_only_fields"] == ["MainPID", "ActiveEnterTimestamp", "QOTRIGHT"]
    assert {"host", "ip", "account", "secret", "token"}.issubset(contract["forbidden_fields"])
    assert release_safety.ALLOWED_SERVICE == contract["service"]


def test_full_declared_release_surface_passes_static_gate():
    assert release_safety.verify(ROOT) == []
