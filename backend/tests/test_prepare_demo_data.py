"""prepare_demo_data.py 的纯逻辑测试（不联网、不跑真实生成）。"""
import hashlib
from pathlib import Path

import pytest

import prepare_demo_data as pdd


def test_sha256_file(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"medguard\n")
    assert pdd.sha256_file(target) == hashlib.sha256(b"medguard\n").hexdigest()


def test_verify_sha_rejects_wrong_file(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"not the fixed triples")
    with pytest.raises(SystemExit) as exc:
        pdd.verify_sha(target)
    assert "SHA-256 不符" in str(exc.value)
    assert not isinstance(exc.value, FileNotFoundError)


def test_parser_defaults():
    args = pdd.build_parser().parse_args([])
    assert args.triples == pdd.DEFAULT_TRIPLES
    assert args.output_dir == pdd.DEFAULT_OUTPUT
    assert args.download is False
    assert args.reset_db is False


def test_dry_run_does_not_write(tmp_path, capsys):
    target = tmp_path / "triples.txt"
    code = pdd.main([
        "--triples", str(target), "--download", "--dry-run",
    ])
    assert code == 0
    assert not target.exists()
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert "OpenCMKG 许可" in output


def test_missing_triples_without_download_errors(tmp_path):
    with pytest.raises(SystemExit) as exc:
        pdd.main(["--triples", str(tmp_path / "missing.txt")])
    assert "--download" in str(exc.value)


def test_download_uses_fixed_hash_and_keeps_no_partial(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_bytes(b"fixed demo payload\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(pdd, "TRIPLES_SHA256", digest)

    dest = tmp_path / "downloaded.txt"
    pdd.download(source.as_uri(), dest)

    assert dest.read_bytes() == source.read_bytes()
    assert not (tmp_path / "downloaded.txt.part").exists()


def test_download_rejects_wrong_hash_and_keeps_no_partial(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_bytes(b"wrong payload\n")
    monkeypatch.setattr(pdd, "TRIPLES_SHA256", "0" * 64)

    dest = tmp_path / "downloaded.txt"
    with pytest.raises(SystemExit):
        pdd.download(source.as_uri(), dest)

    assert not dest.exists()
    assert not (tmp_path / "downloaded.txt.part").exists()
