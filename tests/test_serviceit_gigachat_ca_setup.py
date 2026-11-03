"""Offline cryptographic tests for narrow Russian Ministry CA GigaChat TLS."""
from pathlib import Path
import hashlib
import ssl

import certifi
import pytest

from leadpilot.serviceit_gigachat_ca_setup import (
    PIN_SHA256, PUBLIC_CERT, CertificateBlocked, make_bundle, verified_public_root,
)
from leadpilot.ai import config_from_env


def test_official_russian_trusted_root_der_sha256_pin_and_root_signature():
    pem = PUBLIC_CERT.read_bytes()
    der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
    assert hashlib.sha256(der).hexdigest() == PIN_SHA256
    assert verified_public_root(pem) == pem
    assert "d26d2d0231b7" in PIN_SHA256


def test_bad_or_changed_root_certificate_fails_closed():
    original = PUBLIC_CERT.read_bytes()
    with pytest.raises(CertificateBlocked):
        verified_public_root(original.replace(b"MIIFwj", b"MIIFwx", 1))
    with pytest.raises(CertificateBlocked, match="PUBLIC_ROOT_PEM_SHAPE_INVALID"):
        verified_public_root(original + original)
    with pytest.raises(CertificateBlocked):
        verified_public_root(b"not a certificate")


def test_app_local_ca_bundle_only_and_ai_provider_resolves_path(tmp_path):
    dest = tmp_path / "private" / "gigachat-ca.pem"
    make_bundle(dest)
    assert dest.exists()
    contents = dest.read_bytes()
    assert contents.startswith(Path(certifi.where()).read_bytes()[:80])
    assert contents.endswith(PUBLIC_CERT.read_bytes())
    assert contents.count(b"-----BEGIN CERTIFICATE-----") > 1
    cfg = config_from_env({
        "AI_ENABLE": "1", "AI_PROVIDER": "gigachat",
        "GIGACHAT_SCOPE": "GIGACHAT_API_PERS",
        "GIGACHAT_CREDENTIALS": "SYNTHETIC_ONLY",
        "GIGACHAT_CA_BUNDLE_FILE": str(dest),
    })
    assert cfg.ca_bundle == str(dest)
    assert dest.stat().st_mode & 0o777 == 0o600


def test_global_certifi_is_never_modified_and_output_symlink_is_blocked(tmp_path):
    before = Path(certifi.where()).stat().st_size
    target = tmp_path / "actual"
    target.write_text("DO_NOT_TOUCH", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(CertificateBlocked, match="CA_BUNDLE_SYMLINK_FORBIDDEN"):
        make_bundle(link)
    assert target.read_text("utf-8") == "DO_NOT_TOUCH"
    assert Path(certifi.where()).stat().st_size == before


def test_github_actions_uses_pinned_tls_for_model_and_production_event_worker():
    for name in (
        "serviceit-gigachat-synthetic-once.yml",
        "serviceit-bitrix-new-card-ai.yml",
    ):
        wf = Path(".github/workflows") / name
        content = wf.read_text("utf-8")
        assert "GIGACHAT_CA_BUNDLE_FILE: ${{ runner.temp }}/serviceit-gigachat-verified-ca.pem" in content
        assert 'python -m leadpilot.serviceit_gigachat_ca_setup "$GIGACHAT_CA_BUNDLE_FILE"' in content
        assert content.index("serviceit_gigachat_ca_setup") < (
            content.index("serviceit_gigachat_cockpit_preflight") if "synthetic" in name
            else content.index("serviceit_bitrix_dispatch_worker")
        )
        assert "verify=False" not in content
    oauth = Path("leadpilot/serviceit_gigachat_oauth_diag.py").read_text("utf-8")
    assert 'httpx.Client(verify=env.get("GIGACHAT_CA_BUNDLE_FILE") or True' in oauth
    assert "verify=False" not in oauth
