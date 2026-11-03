"""Pinned app-local TLS trust bundle for GigaChat OAuth and completion.

Only the public Russian Trusted Root CA from the reviewed certificate fixture
is added to normal certifi roots. Its DER SHA-256 is pinned independently of
the file's origin. Nothing is installed into the operating system, no network
download occurs at runtime and TLS verification stays enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile

import certifi
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import NameOID

PIN_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
PUBLIC_CERT = Path(__file__).resolve().parents[1] / "certificates" / "russian_trusted_root_ca.pem"


class CertificateBlocked(ValueError):
    pass


def verified_public_root(cert_bytes: bytes, *, now: datetime | None = None) -> bytes:
    """Refuse any CA with a different DER fingerprint, identity or validity."""
    if (not isinstance(cert_bytes, bytes) or len(cert_bytes) > 10000
            or cert_bytes.count(b"-----BEGIN CERTIFICATE-----") != 1
            or cert_bytes.count(b"-----END CERTIFICATE-----") != 1):
        raise CertificateBlocked("PUBLIC_ROOT_PEM_SHAPE_INVALID")
    try:
        der = ssl.PEM_cert_to_DER_cert(cert_bytes.decode("ascii"))
        if hashlib.sha256(der).hexdigest() != PIN_SHA256:
            raise CertificateBlocked("PUBLIC_ROOT_FINGERPRINT_MISMATCH")
        cert = x509.load_der_x509_certificate(der)
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        current = now or datetime.now(timezone.utc)
        if (len(cn) != 1 or cn[0].value != "Russian Trusted Root CA"
                or cert.issuer != cert.subject or not basic.ca
                or not (cert.not_valid_before_utc <= current <= cert.not_valid_after_utc)):
            raise CertificateBlocked("PUBLIC_ROOT_IDENTITY_OR_DATE_INVALID")
        # Independently check this trust anchor's own signature.
        cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                                 padding.PKCS1v15(), cert.signature_hash_algorithm)
        return cert_bytes
    except CertificateBlocked:
        raise
    except Exception:
        raise CertificateBlocked("PUBLIC_ROOT_UNVERIFIED") from None


def make_bundle(destination: Path, *, public_root: Path = PUBLIC_CERT,
                normal_ca: Path | None = None) -> None:
    """Create one verified application-local bundle, atomically, no global trust."""
    if not isinstance(destination, Path) or not destination.is_absolute():
        raise CertificateBlocked("CA_BUNDLE_OUTPUT_PATH_INVALID")
    if destination.is_symlink():
        raise CertificateBlocked("CA_BUNDLE_SYMLINK_FORBIDDEN")
    try:
        root = verified_public_root(public_root.read_bytes())
        base = (normal_ca or Path(certifi.where())).read_bytes()
        if b"-----BEGIN CERTIFICATE-----" not in base or len(base) > 500_000:
            raise CertificateBlocked("SYSTEM_CA_BUNDLE_INVALID")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".serviceit-ca-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(base.rstrip() + b"\n" + root)
            os.chmod(tmp, 0o600)
            os.replace(tmp, destination)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except CertificateBlocked:
        raise
    except (OSError, ValueError):
        raise CertificateBlocked("CA_BUNDLE_BUILD_FAILED") from None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(json.dumps({"status": "GIGACHAT_CA_BLOCKED", "reason": "ONE_OUTPUT_REQUIRED"}))
        return 2
    try:
        make_bundle(Path(args[0]))
        output = {"status": "GIGACHAT_CA_PINNED_READY", "tls_verify": True,
                  "global_ca_changes": 0, "crm_writes": 0}
        code = 0
    except CertificateBlocked as exc:
        output = {"status": "GIGACHAT_CA_BLOCKED", "reason": str(exc),
                  "global_ca_changes": 0, "crm_writes": 0}
        code = 2
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
