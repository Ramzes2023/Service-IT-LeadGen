import hashlib
import pytest
from ops.serviceit_vps_b2b_exact_receipt_audit import emit, proof, source_matches, TARGETS


def test_proof_only_reports_status():
    salt = b"a" * 16
    def digest(fields, s):
        return hashlib.sha256(s + fields["x"].encode()).digest()
    sig = digest({"x":"safe"}, salt)
    assert proof({"x":"safe"}, salt, sig, digest) == "YES"
    assert proof({"x":"changed"}, salt, sig, digest) == "NO"
    assert proof(None, None, None, digest) == "NONE"
    assert proof({"x":"safe"}, b"x", sig, digest) == "NO"


def test_source_hash_is_exact():
    source = "B2B-CENTER-4677923"
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert source_matches(source, digest)
    assert not source_matches("B2B-CENTER-4679263", digest)
    assert not source_matches(None, digest)
    assert not source_matches(source, None)


def test_output_is_sanitized(capsys):
    emit("BLOCKER_1_CARD", 29)
    assert capsys.readouterr().out == "B2B_EXACT_BLOCKER_1_CARD=29\n"
    for value in ("secret-token", "https://example.org", "CUSTOMER", "a"*64):
        with pytest.raises(ValueError):
            emit("VALUE", value)
    assert capsys.readouterr().out == ""
    assert {4677923,4679263,4677837,4674919,4693159,4692643,4692350,4693220} == set(TARGETS)
