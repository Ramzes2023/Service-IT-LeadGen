"""Regression for non-deceptive V37 rollback source attestations (offline)."""
from ops.serviceit_vps_b2b_v37_consistency_emergency_rollback import (
    FILES, expected_sources,
)


def test_source_attestation_matches_runtime_shas_positional_tuple():
    fresh=("a"*64,"b"*64,"c"*64)
    live, baseline=expected_sources(fresh,"d"*64)
    assert len(FILES)==3
    assert type(live) is tuple and type(baseline) is tuple
    assert live == fresh
    assert baseline == ("d"*64,"MISSING","MISSING")
    simulated_runtime_live=tuple(x for x in fresh)
    simulated_runtime_baseline=("d"*64,"MISSING","MISSING")
    assert simulated_runtime_live==live
    assert simulated_runtime_baseline==baseline
    assert simulated_runtime_live != baseline


def test_hash_checks_must_not_compare_a_tuple_with_name_value_mapping():
    fresh=("a"*64,"b"*64,"c"*64)
    live,baseline=expected_sources(fresh,"d"*64)
    assert isinstance(live,tuple)
    assert isinstance(baseline,tuple)
    assert live != dict(zip(FILES,fresh))
    assert baseline != dict(zip(FILES,baseline))
