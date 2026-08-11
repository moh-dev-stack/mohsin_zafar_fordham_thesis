"""Tests for dqa.audit: hash-chained append-only log detects tampering."""

from __future__ import annotations

from dqa.audit import GENESIS_PARENT, AuditChain, AuditRecord


def _three_entry_chain() -> AuditChain:
    chain = AuditChain()
    chain.append("load", {"resource_type": "Patient", "n": 100})
    chain.append("project", {"manifest": "completeness", "width": "minimal"})
    chain.append("verdict", {"dimension": "completeness", "pass": True})
    return chain


def test_three_appends_replay_clean() -> None:
    chain = _three_entry_chain()
    assert len(chain) == 3
    assert chain.verify() is True
    records = chain.records
    assert records[0].parent_hash == GENESIS_PARENT
    assert [r.sequence for r in records] == [0, 1, 2]
    # Each record's parent is the previous record's hash.
    assert records[1].parent_hash == records[0].this_hash
    assert records[2].parent_hash == records[1].this_hash


def test_mutating_one_byte_of_payload_breaks_replay() -> None:
    chain = _three_entry_chain()
    assert chain.verify() is True

    original = chain.records[1]
    tampered_payload = dict(original.payload)
    # Flip a single byte: "minimal" -> "ninimal".
    tampered_payload["width"] = "n" + original.payload["width"][1:]
    chain._records[1] = original.model_copy(update={"payload": tampered_payload})

    assert chain.verify() is False


def test_mutating_one_byte_of_hash_breaks_replay() -> None:
    chain = _three_entry_chain()
    original = chain.records[2]
    last_char = original.this_hash[-1]
    flipped = "0" if last_char != "0" else "1"
    chain._records[2] = original.model_copy(update={"this_hash": original.this_hash[:-1] + flipped})

    assert chain.verify() is False


def test_deleting_a_record_breaks_replay() -> None:
    chain = _three_entry_chain()
    del chain._records[1]
    assert chain.verify() is False


def test_persisted_chain_is_written_as_jsonl(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    chain = AuditChain(persist_path=path)
    chain.append("load", {"n": 1})
    chain.append("verdict", {"pass": False})
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert chain.verify() is True


def test_compute_hash_is_stable_for_identical_inputs() -> None:
    record = _three_entry_chain().records[0]
    recomputed = AuditRecord.compute_hash(
        record.sequence, record.timestamp, record.stage, record.payload, record.parent_hash
    )
    assert recomputed == record.this_hash


# ------------------------------------------------- the CLI wiring, added 2026-08-06


def test_run_cli_writes_a_verifiable_chain(tmp_path) -> None:
    """`--audit-log` must produce a chain that replays independently.

    The chain is re-verified here by recomputing every hash from the record's
    own fields rather than by calling ``AuditChain.verify``, so this test would
    still fail if ``verify`` were changed to return True unconditionally.
    """
    import json
    from datetime import datetime

    from dqa.run import main

    out = tmp_path / "run.json"
    log = tmp_path / "audit.jsonl"
    assert main(
        [
            "--dataset", "eicu-demo",
            "--limit", "12",
            "--no-cache",
            "--out", str(out),
            "--audit-log", str(log),
        ]
    ) == 0

    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) >= 3
    assert records[0]["stage"] == "run_start"
    assert records[-1]["stage"] == "run_complete"
    assert records[0]["parent_hash"] == GENESIS_PARENT

    parent = GENESIS_PARENT
    for index, record in enumerate(records):
        assert record["sequence"] == index
        assert record["parent_hash"] == parent
        assert record["this_hash"] == AuditRecord.compute_hash(
            record["sequence"],
            datetime.fromisoformat(record["timestamp"]),
            record["stage"],
            record["payload"],
            parent,
        )
        parent = record["this_hash"]


def test_a_tampered_payload_breaks_the_chain(tmp_path) -> None:
    """Editing any payload in place must invalidate that record's hash."""
    import json
    from datetime import datetime

    from dqa.run import main

    log = tmp_path / "audit.jsonl"
    main(
        [
            "--dataset", "eicu-demo",
            "--limit", "12",
            "--no-cache",
            "--out", str(tmp_path / "run.json"),
            "--audit-log", str(log),
        ]
    )
    records = [json.loads(line) for line in log.read_text().splitlines()]
    victim = records[1]
    victim["payload"]["scores"] = {"f1_plausibility": 1.0}

    recomputed = AuditRecord.compute_hash(
        victim["sequence"],
        datetime.fromisoformat(victim["timestamp"]),
        victim["stage"],
        victim["payload"],
        victim["parent_hash"],
    )
    assert recomputed != victim["this_hash"]
