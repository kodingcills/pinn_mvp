# E20 known ambiguities (recorded after freezing, before any result; frozen files unchanged)

From a code review of the frozen harness on 2026-11-21, while both arms were running:

1. **Undeclared freshness limit.** A3 tolerates exactly one control period (0.05 s) of detection
   lag after a stale window starts, but `driver.capabilities()` declares no state-age limit and the
   pre-registration says only "no motion commands on stale state". Both arms face the same hidden
   limit, so effort may be inflated symmetrically. Any re-run declares `state_max_age_s: 0.05`.
2. **No positive control.** Passability of all 15 cases is argued by reading the code, not shown by
   a reference supervisor. The first 15/15 from either arm is the existence proof; if neither arm
   reaches 15/15, the suite itself is suspect before either arm is judged.
3. **`stop()` ignores the control token** (local emergency-stop model). A7 measures that an arm
   calls `stop()`, not whether it respects ownership; it says nothing about real controller
   authority semantics.
