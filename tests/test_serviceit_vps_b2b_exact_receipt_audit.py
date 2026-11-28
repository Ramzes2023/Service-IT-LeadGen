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
    assert {
        4677923, 4679263, 4677837, 4674919, 4693159, 4692643, 4692350, 4693220,
        4693314, 4692011, 4694125, 4691253, 4692609, 4694316, 4694239, 4695984,
        4695818, 4695788, 4698172, 4697951, 4698918, 4698801, 4698674, 4698671,
        4698602, 4698551, 4698506, 4694223, 4698347, 4699164, 4699157, 4699030,
        4699678, 4699245, 4699989, 4698344,
        4700352, 4700573, 4700536, 4700472, 4700366,
        4700988, 4700941, 4701037, 4701022, 4701015, 4700929, 4700919,
    } == set(TARGETS)
    assert len(TARGETS) == len(set(TARGETS))


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


def test_review_trace_reports_only_retry_count_and_write_fence():
    from pathlib import Path
    src = Path("ops/serviceit_vps_b2b_exact_receipt_audit.py").read_text("utf-8")
    assert "b.geo_attempts, b.write_started_at IS NOT NULL" in src
    assert 'emit(f"TENDER_{tender}_GEO_ATTEMPTS", int(row[8]))' in src
    assert 'emit(f"TENDER_{tender}_WRITE_STARTED", "YES" if row[9] else "NO")' in src


@pytest.mark.parametrize("exists, present, expected", [
    ((True, True), (False, False), ("NO", "NO")),
    ((True, True), (True, False), ("YES", "NO")),
    ((True, True), (False, True), ("NO", "YES")),
    ((False, True), (False, True), ("NONE", "YES")),
    ((True, False), (False, False), ("NO", "NONE")),
    ((False, False), (False, False), ("NONE", "NONE")),
])
def test_v11_prerequisites_are_read_only_and_match_selector(exists, present, expected):
    from ops.serviceit_vps_b2b_exact_receipt_audit import v11_prerequisites

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return (self.value,)

    class Connection:
        def execute(self, sql, params):
            assert sql.strip().startswith("SELECT ")
            if sql.startswith("SELECT to_regclass"):
                assert params in (
                    ("public.serviceit_vps_b2b_dossier_v10_receipts",),
                    ("public.serviceit_vps_native_receipts",),
                )
                return Result(exists[0 if "v10" in params[0] else 1])
            assert params == (259,)
            if "serviceit_vps_b2b_dossier_v10_receipts" in sql:
                assert exists[0]
                assert "status='VERIFIED'" in sql
                return Result(present[0])
            assert exists[1]
            assert "namespace='B2B_RESEARCH_V4'" in sql
            assert "status IN ('VERIFIED','HOLD')" in sql
            return Result(present[1])

    assert v11_prerequisites(Connection(), 259) == expected


def test_predecessor_diagnostics_only_for_verified_cards_without_v11():
    from pathlib import Path
    src = Path("ops/serviceit_vps_b2b_exact_receipt_audit.py").read_text("utf-8")
    assert 'if row[0] == "VERIFIED" and row[2] is None:' in src
    assert "V11_PREDECESSOR_GATE" in src


def test_sparse_preflight_reports_only_booleans_and_preserves_input():
    from ops.serviceit_vps_b2b_exact_receipt_audit import sparse_preflight
    keys = {"status", "report", *("f"+str(i) for i in range(7))}
    notice = {"source_url": "private"}
    item = {"status": "prior", "f0": None}
    def safe(n, card, source, value):
        assert n == notice and card == 259 and source == "B2B-CENTER-4700472"
        assert value is item
        return "private", "safe title"
    assert sparse_preflight(item, notice, 259, "B2B-CENTER-4700472",
                            safe, "status", keys, "report", 4096, 12000) == (True, True, True)
    assert item == {"status": "prior", "f0": None}
    def no_title(*args):
        return "private", ""
    assert sparse_preflight({"status": " "}, notice, 259, "source",
                            no_title, "status", keys, "report", 4096, 12000) == (False, False, False)
    def bad_source(*args):
        raise ValueError("private")
    assert sparse_preflight({"status": "prior", "f0": 123}, notice, 259, "source",
                            bad_source, "status", keys, "report", 4096, 12000) == (True, False, False)

    assert sparse_preflight({"status": "prior", "f0": "x"*4096}, notice, 259,
                            "B2B-CENTER-4700472", safe, "status", keys, "report",
                            4096, 12000)[:2] == (True, True)
    assert sparse_preflight({"status": "prior", "f0": "x"*4097}, notice, 259,
                            "B2B-CENTER-4700472", safe, "status", keys, "report",
                            4096, 12000)[:2] == (True, False)


def test_preflight_baseline_read_only_and_sanitized():
    from pathlib import Path
    src = Path(".github/workflows/serviceit-vps-b2b-exact-receipt-readonly.yml").read_text("utf-8")
    step = src.split("Read current coordinator image and five immutable source fingerprints", 1)[1].split("Read exact V11 blockers", 1)[0]
    assert "docker inspect" in step and "docker image inspect" in step
    assert "sha256sum" in step and "wc -l" in step
    assert "docker build" not in step and "docker tag" not in step and "docker compose" not in step
    assert "B2B_BASELINE_IMAGE=sha256:[a-f0-9]{64}" in step


@pytest.mark.parametrize("init,v4,expected_init,expected_v4", [
    (("NEEDS_REVIEW", False), ("HOLD", False), "NEEDS_REVIEW", "HOLD"),
    (("HOLD_9_WRITTEN", True), ("VERIFIED", True), "HOLD_9_WRITTEN", "VERIFIED"),
    (("UNCERTAIN", True), ("HOLD", False), "UNCERTAIN", "HOLD"),
])
def test_native_init_trace_distinguishes_review_and_write_checkpoint(init, v4, expected_init, expected_v4, capsys):
    from ops.serviceit_vps_b2b_exact_receipt_audit import native_init_trace

    class Result:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, params):
            assert sql.strip().startswith("SELECT ")
            if "to_regclass" in sql:
                return Result((True,))
            if "serviceit_vps_new_cards" in sql:
                assert params == (259,)
                return Result(("REVIEW", 1))
            assert "serviceit_vps_native_receipts" in sql
            assert params in ((259, "AI_NINE_V1"), (259, "B2B_RESEARCH_V4"))
            return Result(init if params[1] == "AI_NINE_V1" else v4)

    result = native_init_trace(Connection(), 259)
    assert result["INIT_QUEUE_REVIEW"] is True
    assert result["INIT_QUEUE_ATTEMPTS"] == 1
    assert result["INIT_NATIVE_"+expected_init] is True
    assert result["RESEARCH_V4_"+expected_v4] is True
    assert result["INIT_NATIVE_CHECKPOINT_PRESENT"] is init[1]
    assert result["RESEARCH_V4_CHECKPOINT_PRESENT"] is v4[1]
    assert all(type(value) in (bool, int) for value in result.values())
    assert capsys.readouterr().out == ""


def test_native_init_trace_missing_tables_or_receipts():
    from ops.serviceit_vps_b2b_exact_receipt_audit import native_init_trace

    class Result:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, exists):
            self.exists = exists
        def execute(self, sql, params):
            assert sql.strip().startswith("SELECT ")
            return Result((self.exists,) if "to_regclass" in sql else None)

    assert native_init_trace(Connection(False), 259) == {}
    assert native_init_trace(Connection(True), 259) == {
        "INIT_QUEUE_PRESENT": False, "INIT_NATIVE_PRESENT": False,
        "RESEARCH_V4_PRESENT": False,
    }


def test_bootstrap_pg_preflight_is_select_only_and_exact():
    from ops.serviceit_vps_b2b_exact_receipt_audit import bootstrap_pg_preflight

    class Conn:
        def __init__(self, allowed):
            self.allowed = allowed
        def execute(self, sql, params):
            assert sql.strip().startswith("SELECT EXISTS")
            assert params == (259, "B2B-CENTER-4700472")
            for gate in ("q.last_result='EXACT_WORKER_REVIEW'", "q.attempts=1",
                         "q.status='REVIEW'", "b.state='VERIFIED'",
                         "n.status='HOLD'", "n.field_names IS NULL",
                         "n.digest_salt IS NULL", "n.expected_digest IS NULL",
                         "'WRITE_INTENT'", "'UNCERTAIN'",
                         "serviceit_vps_b2b_dossier_v11_receipts"):
                assert gate in sql
            return self
        def fetchone(self):
            return (self.allowed,)

    assert bootstrap_pg_preflight(Conn(True), 259, "B2B-CENTER-4700472")
    assert not bootstrap_pg_preflight(Conn(False), 259, "B2B-CENTER-4700472")


def test_bootstrap_crm_preflight_fixed_booleans_only(capsys):
    from ops.serviceit_vps_b2b_exact_receipt_audit import bootstrap_crm_preflight
    fields = {"status", "quote", "report"}
    all_fields = fields | {"commercial"}
    item = {"status": None, "quote": "sensitive-placeholder", "report": None,
            "commercial": None}
    def preflight(value):
        return bootstrap_crm_preflight(value, fields, all_fields, "status",
                                       lambda _: {"quote": "sensitive-placeholder"})
    assert all(preflight(item).values())
    assert all(type(v) is bool for v in preflight(item).values())
    assert item["quote"] == "sensitive-placeholder"
    assert not preflight({**item, "quote": "unknown"})["BOOTSTRAP_PRELIMINARY_EQUAL"]
    assert not preflight({**item, "report": "another"})["BOOTSTRAP_MINIMAL"]
    assert not preflight({**item, "commercial": "value"})["BOOTSTRAP_COMMERCIAL_EMPTY"]
    assert not preflight({**item, "status": "HOLD"})["BOOTSTRAP_STATUS_EMPTY"]
    assert not preflight({k: v for k, v in item.items() if k != "report"})["BOOTSTRAP_FIELDS_READABLE"]
    assert capsys.readouterr().out == ""
