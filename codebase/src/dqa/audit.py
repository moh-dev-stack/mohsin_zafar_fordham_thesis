"""Append-only cryptographically chained audit log.

Each record carries the SHA-256 hash of the previous record so any
in-place modification or deletion is detectable by replay. The genesis
record is anchored at chain creation time.

Storage: in-memory list plus optional JSONL persistence. A production
deployment would back this with a tamper-resistant store
(PostgreSQL append-only table with trigger-blocked updates, or
write-once object storage).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GENESIS_PARENT = "0" * 64


class AuditRecord(BaseModel):
    """One audit-log entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    timestamp: datetime
    stage: str = Field(min_length=1)
    payload: dict[str, Any]
    parent_hash: str = Field(min_length=64, max_length=64)
    this_hash: str = Field(min_length=64, max_length=64)

    @staticmethod
    def compute_hash(
        sequence: int, timestamp: datetime, stage: str, payload: dict[str, Any], parent_hash: str
    ) -> str:
        canonical = json.dumps(
            {
                "sequence": sequence,
                "timestamp": timestamp.isoformat(),
                "stage": stage,
                "payload": payload,
                "parent_hash": parent_hash,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditChain:
    """In-memory chain. Persists to JSONL if a path is given."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._records: list[AuditRecord] = []
        self._persist_path = persist_path

    def append(self, stage: str, payload: dict[str, Any]) -> AuditRecord:
        sequence = len(self._records)
        timestamp = datetime.now(tz=UTC)
        parent = self._records[-1].this_hash if self._records else GENESIS_PARENT
        this_hash = AuditRecord.compute_hash(sequence, timestamp, stage, payload, parent)
        record = AuditRecord(
            sequence=sequence,
            timestamp=timestamp,
            stage=stage,
            payload=payload,
            parent_hash=parent,
            this_hash=this_hash,
        )
        self._records.append(record)
        if self._persist_path is not None:
            with self._persist_path.open("a") as fh:
                fh.write(record.model_dump_json() + "\n")
        return record

    def verify(self) -> bool:
        """Walk the chain and confirm every link is intact."""
        expected_parent = GENESIS_PARENT
        for record in self._records:
            recomputed = AuditRecord.compute_hash(
                record.sequence,
                record.timestamp,
                record.stage,
                record.payload,
                record.parent_hash,
            )
            if record.parent_hash != expected_parent:
                return False
            if recomputed != record.this_hash:
                return False
            expected_parent = record.this_hash
        return True

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[AuditRecord]:
        return iter(self._records)

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        """Return an immutable snapshot of the chain for inspection."""
        return tuple(self._records)
