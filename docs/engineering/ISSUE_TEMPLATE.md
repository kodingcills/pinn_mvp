# Haplous engineering issue template

**Title:** `[runtime|eval|studio|integration|packaging|science] concrete behavior and consequence`

## Product problem / why Haplous owns it
<One short paragraph: impact on machine execution, integrator experience, product correctness or experimental evidence. State why not upstream policy/OEM/controller.>

## Evidence and classification
- Classification: REPRODUCED DEFECT / VALIDATED LIMITATION / INVESTIGATION NEEDED / FEATURE OPPORTUNITY / EVALUATOR DEFECT / EXTERNAL DEPENDENCY
- Exact HEAD/version:
- Current observed behavior:
- Expected behavior and violated requirement:
- Reproduce (command, scenario, task, seed, logs, artifacts):
- Existing issue/decision link:
- Is this a claim about physics, software semantics, portability, or commercial value?

## Minimal implementation and exclusions
- Proposed code change and module ownership:
- Alternatives considered, including competent incumbent behavior:
- **In scope:**
- **Explicitly out of scope:**
- Dependencies/blockers and machine/control authority assumptions:

## Definition of done
- [ ] Unit/property test covering the exact failure/requirement
- [ ] Negative or injected-fault test that would fail a broken implementation
- [ ] Integrated runtime/simulation/external-adapter test, where relevant
- [ ] Behavior/performance compared with previous Haplous and competent incumbent where relevant
- [ ] Baseline compatibility and migration checked, or intentional break documented
- [ ] Raw artifacts and reproducible commands linked; graders independent of runtime
- [ ] Independent verifier records PASS/FAIL against exact commit SHA
- [ ] Limitations, stop/revert rule, and scoring-version changes documented
- [ ] PR linked and issue closed only when checks demonstrably pass

**Priority:** P0 / P1 / P2 / P3  
**Parent epic:**  
**Blocked by:**  
**Assigned owner / independent verifier:**  
**Estimated bounded investigation or implementation scope:**  
