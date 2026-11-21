"""Unsupervised reference arm for E20: the frozen policy straight to the driver.

It is the A1 non-interference reference and the suite's negative control: it must fail the
fault cases (A2–A7), which shows the suite discriminates. It is not an experiment arm.
"""
from __future__ import annotations

from benchmarks.e20_shared.acceptance import MAX_VALIDITY_S, lifted


def run(driver, policy, max_steps: int) -> dict:
    token = driver.acquire_control()
    events = [{"t_s": driver.now(), "kind": "start"}]
    for seq in range(max_steps):
        try:
            state = driver.read_state()
        except ConnectionError:
            break
        if lifted(state):
            events.append({"t_s": driver.now(), "kind": "outcome", "result": "COMPLETE"})
            return {"outcome": "COMPLETE", "reason": None, "events": events}
        action = policy.act(state)
        driver.send(seq, token, [a * 0.5 for a in action[:6]], float(action[6]), driver.now() + MAX_VALIDITY_S)
        driver.poll_acks()
        driver.step()
    events.append({"t_s": driver.now(), "kind": "outcome", "result": "ABORTED"})
    return {"outcome": "ABORTED", "reason": "step_budget_or_disconnect", "events": events}
