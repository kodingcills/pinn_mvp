# Design M5-L: backend-neutral episode log, timeline-first viewing

Status: **PROPOSED** 2026-11-13 (operator asked for it). Not locked; recording-only, so it
changes no runtime behaviour. Tasks M5.2-M5.5 in `TASKS.json`.

## Why

MuJoCo is a test bench, not the product. The product is the Haplous runtime (admission,
refusal, hold, revalidation, recovery) and its evidence trail. Robotics engineers mostly read
time-series plots and event timelines (ROS 2 bags / MCAP in Foxglove or Rerun), and only use
3D replay to debug one failure. Real-robot data persuades them more than sim results do,
especially for contact-rich tasks like peg insertion where sim contact is least trustworthy.

## What exists (verified in code)

`benchmarks/recording.py` already writes three separable things:

| File | Content | Backend-specific |
|---|---|---|
| `telemetry.jsonl` | every `MachineObservation` and `ServoCommand`/`CommandOutcome` per tick | no |
| `events.jsonl` | machine events (fault windows) + every Haplous `EvidenceEvent` with sim time | no |
| state stream, `model.mjb`/`.xml` | `mj_getState` per step for 3D replay | MuJoCo |

The neutral parts are produced only because capture lives in `RecordingMachine(MuJoCoMachine)`.

## Design

1. **M5.2 Capture at the machine-session boundary.** A recorder wraps any Haplous machine
   session (`observe/apply/step/stop/ended/fault`, `haplous/machine.py`,
   `haplous/adapters/external.py`) and writes `telemetry.jsonl` + `events.jsonl` + metadata.
   The MuJoCo state stream becomes an optional add-on attached only for MuJoCo backends.
   Identity stays an M0 run. Exit: same episode log from MuJoCo and from one external adapter
   (robosuite or ManiSkill); A/A shows identical observations/commands/outcome with and without
   recording on both; existing M2 gate still 11/11 on committed recordings.
2. **M5.3 `haplous timeline <run>`.** Plots of force, pose and command over time with Haplous
   decisions (hold, revalidate, refuse, recover, abort) and fault windows on the same time
   axis. Rendered from the episode log only, no backend import (same check as M2 item 3).
   Output: Rerun file with plots + event markers, 3D an optional layer; PNG fallback.
3. **M5.4 MCAP export.** JSONL stays canonical (M0-hashed). Exporter writes MCAP with
   per-channel schemas so the log opens in Foxglove next to real-robot bags. Round-trip test.
4. **M5.5 Import real logs.** Turn a ROS 2 bag / MCAP from a real arm into the same episode
   log so timeline and HES metrics run on it. Real data has no evaluator-only truth: grading
   uses recorded outcomes/labels and reports say so. **Blocked on operator:** which robot or
   dataset. No physical execution is involved (offline logs only).
5. **MuJoCo keeps** single-failure 3D debugging and optional re-simulate-and-verify: re-run an
   episode on demand, compare to the stored digest, flag mismatch as drift.

## Compute rule (operator, 2026-11-13)

Prefer the smallest run that answers the question. No campaign on the scale of M4.2b (about
2,000 full MuJoCo episodes) without an explicit estimate of CPU time and operator agreement
first. Tests and demos use a handful of episodes.

## Out of scope

Changing runtime behaviour, physics or controllers; live robot connection; GPU work.
