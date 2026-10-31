"""TLS trust regression on the EIS HTTPX transport; no live requests."""
import ssl
from pathlib import Path

import pytest

from leadpilot import eis_rss


def test_windows_root_store_creates_secure_context():
    ctx = eis_rss._tls_verifier(None, windows_store=True)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_non_windows_without_extra_ca_keeps_certifi():
    assert eis_rss._tls_verifier(None, windows_store=False) is True


def test_missing_or_malformed_bundle_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="EIS_CA_BUNDLE_INVALID"):
        eis_rss._tls_verifier(str(tmp_path / "missing.cer"), windows_store=True)
    bad = tmp_path / "root.crt"
    bad.write_bytes(b"FAKE CERTIFICATE")
    with pytest.raises(ValueError, match="EIS_CA_BUNDLE_INVALID"):
        eis_rss._tls_verifier(str(bad), windows_store=True)


def test_os_context_used_by_default_on_windows(monkeypatch):
    class DummyClient:
        def __init__(self, **options):
            assert options["verify"].check_hostname is True
            assert options["verify"].verify_mode == ssl.CERT_REQUIRED
            assert options["follow_redirects"] is False
            assert options["trust_env"] is False
            raise RuntimeError("EIS_CLIENT_PROBE_STOP")

    monkeypatch.setattr(eis_rss.os, "name", "nt")
    monkeypatch.setattr(eis_rss.httpx, "Client", DummyClient)
    with pytest.raises(RuntimeError, match="EIS_CLIENT_PROBE_STOP"):
        eis_rss.fetch(
            url="https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C"
        )


def test_explicit_verified_pem_certificate_supported(tmp_path):
    import certifi
    import re
    text = Path(certifi.where()).read_text(encoding="ascii")
    match = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, flags=re.S)
    assert match is not None
    path = tmp_path / "official_like.pem"
    path.write_text(match.group(0) + "\n", encoding="ascii")
    context = eis_rss._tls_verifier(str(path), windows_store=False)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_explicit_verified_der_certificate_supported(tmp_path):
    import certifi
    import re
    text = Path(certifi.where()).read_text(encoding="ascii")
    match = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, flags=re.S)
    assert match is not None
    path = tmp_path / "official_like.crt"
    path.write_bytes(ssl.PEM_cert_to_DER_cert(match.group(0)))
    context = eis_rss._tls_verifier(str(path), windows_store=True)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
