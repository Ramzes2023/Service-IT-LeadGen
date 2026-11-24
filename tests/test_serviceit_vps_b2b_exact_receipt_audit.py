import hashlib
import pytest
from ops.serviceit_vps_b2b_exact_receipt_audit import emit, proof, source_matches, revision_proof, TARGETS


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
    assert {4677923,4679263,4677837,4674919,4693159,4692643,4692350,4693220,4693314,4692011,4694125,4691253} == set(TARGETS)


def test_workflow_output_filter_is_anchored_and_owner_gated():
    from pathlib import Path
    src = Path(".github/workflows/serviceit-vps-b2b-exact-receipt-readonly.yml").read_text("utf-8")
    assert "github.event.comment.body == 'SERVICEIT_B2B_EXACT_V11_BLOCKER_READONLY_368'" in src
    assert "[0-9]{1,10})$'" in src
    assert "B2B_EXACT_CRM_WRITES=0" in src
    assert "B2B_EXACT_PG_WRITES=0" in src


def test_audit_roles_and_guarded_workflow_are_separate():
    from pathlib import Path
    src = Path("ops/serviceit_vps_b2b_exact_receipt_audit.py").read_text("utf-8")
    coordinator = src.split("def main():", 1)[1]
    assert "from leadpilot.serviceit_vps_b2b_bridge" not in coordinator
    assert "exact_duplicate(" not in coordinator
    assert 'SET TRANSACTION READ ONLY' in coordinator
    workflow = Path(".github/workflows/serviceit-vps-b2b-exact-receipt-readonly.yml").read_text("utf-8")
    assert "serviceit-leadgen-vps-coordinator-1 python -B -" in workflow
    assert "serviceit-b2b-vps-writer-1 python -B - --writer" in workflow
    assert "B2B_EXACT_WRITER_CRM_WRITES=0" in workflow


def test_revision_requires_exact_source_and_current_signed_fields():
    salt = b"a" * 16
    source = "B2B-CENTER-4693314"
    def digest(fields, s):
        return hashlib.sha256(s + fields["x"].encode()).digest()
    fields = {"x": "verified"}
    sig = digest(fields, salt)
    saved = hashlib.sha256(source.encode()).hexdigest()
    revision = (saved, fields, salt, sig)
    assert revision_proof(fields, source, revision, digest)
    assert not revision_proof({"x": "different"}, source, revision, digest)
    assert not revision_proof(fields, "B2B-CENTER-4677923", revision, digest)
    assert not revision_proof(fields, source, (saved, fields, salt, b"x"*32), digest)


def test_v37_patch_witness_is_not_full_dossier_proof():
    from ops.serviceit_vps_b2b_exact_receipt_audit import v37_patch_proof, v37_patch_digest
    source = "B2B-CENTER-4694125"
    saved = hashlib.sha256(source.encode()).hexdigest()
    salt = b"a"*16
    fields = {"report":"verified", "decision":""}
    sig = v37_patch_digest(fields, salt)
    revision = (saved, fields, salt, sig)
    assert v37_patch_proof({"report":"verified","decision":None,"unrelated":"later"}, source, revision, "decision")
    assert not v37_patch_proof({"report":"changed","decision":None}, source, revision, "decision")
    assert not v37_patch_proof({"report":"verified"}, source, revision, "decision")
    assert not v37_patch_proof({"report":"verified","decision":""}, "B2B-CENTER-4691253", revision, "decision")
    assert not v37_patch_proof({"report":"verified","decision":""}, source, (saved,fields,salt,b"x"*32), "decision")
