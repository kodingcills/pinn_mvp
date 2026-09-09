"""Execution evidence: an append-only, reconstructable record of one
execution attempt. This is product semantics, not debug logging."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceEvent:
    seq: int
    t_s: float
    kind: str
    payload: dict[str, Any]


@dataclass
class ExecutionEvidence:
    intent_behavior: str | None = None
    intent_summary: dict[str, Any] = field(default_factory=dict)
    _events: list[EvidenceEvent] = field(default_factory=list)
    _seq: int = 0

    def append(self, kind: str, t_s: float, **payload: Any) -> EvidenceEvent:
        event = EvidenceEvent(self._seq, t_s, kind, dict(payload))
        self._seq += 1
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[EvidenceEvent, ...]:
        return tuple(self._events)

    def feasibility_decision(self) -> dict[str, Any] | None:
        """Reconstruct the feasibility decision from the recorded event."""
        for event in self._events:
            if event.kind == "feasibility":
                return {
                    "outcome": event.payload["outcome"],
                    "reasons": list(event.payload.get("reasons", [])),
                    "t_s": event.t_s,
                }
        return None

    def constraint_violations(self) -> list[dict[str, Any]]:
        return [
            {
                "code": e.payload["code"],
                "t_s": e.t_s,
                "peak_value": e.payload["value"],
                "limit": e.payload["limit"],
                "hard": e.payload["hard"],
            }
            for e in self._events
            if e.kind == "constraint_violation"
        ]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "intent_behavior": self.intent_behavior,
            "intent_summary": self.intent_summary,
            "events": [
                {"seq": e.seq, "t_s": e.t_s, "kind": e.kind, "payload": e.payload}
                for e in self._events
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), sort_keys=True, indent=1)
