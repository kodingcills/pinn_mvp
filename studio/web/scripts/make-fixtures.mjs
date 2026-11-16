// Derives contract-shaped fixtures (docs/eval_program/designs/M6_haplous_studio.md) from committed results.
// Run from studio/web: node scripts/make-fixtures.mjs
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../..");
const out = join(here, "../fixtures");
const json = (p) => JSON.parse(readFileSync(join(repo, p), "utf8"));
const jsonl = (p) => readFileSync(join(repo, p), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const write = (name, data) => {
  mkdirSync(dirname(join(out, name)), { recursive: true });
  writeFileSync(join(out, name), JSON.stringify(data, null, name.startsWith("runs/") ? 0 : 2) + "\n");
};

// Mirrors evaluation/timeline.py load(): fault windows from machine events.
function windowsOf(events, endS) {
  const windows = [];
  for (const e of events) {
    if (e.source !== "machine") continue;
    if (e.kind.endsWith("_window_start")) {
      windows.push({ name: e.kind.replace(/_window_start$/, ""), start_s: e.payload.start_s, end_s: e.payload.end_s });
    } else if (e.kind === "machine_fault") {
      const open = windows.find((w) => w.name === "machine_fault" && w.end_s === endS);
      if (e.payload.fault != null && !open) windows.push({ name: "machine_fault", start_s: e.t_s, end_s: endS });
      else if (e.payload.fault == null && open) open.end_s = e.t_s;
    }
  }
  return windows;
}

// Mirrors studio/data.py series(): one entry per observation row (null observation kept as null),
// command target = the last command logged before that row.
function detail(logDir) {
  const meta = json(`${logDir}/metadata.json`);
  const rows = jsonl(`${logDir}/telemetry.jsonl`);
  const events = jsonl(`${logDir}/events.jsonl`);
  const dt = meta.timestep_s;
  const series = { t_s: [], force_z_n: [], pose_m: [], command_target_pose_m: [] };
  let target = null;
  for (const r of rows) {
    if (r.kind === "command") target = r.command.target_pose_m;
    else if (r.kind === "observation") {
      const o = r.observation;
      series.t_s.push(r.tick * dt);
      series.force_z_n.push(o ? (o.force_z_n ?? null) : null);
      series.pose_m.push(o ? (o.pose_m ?? null) : null);
      series.command_target_pose_m.push(target);
    }
  }
  return {
    metadata: meta,
    series,
    events: events.map(({ source, kind, tick, t_s, payload }) => ({ source, kind, tick, t_s, payload })),
    windows: windowsOf(events, meta.n_ticks * dt),
  };
}

// ---- runs
const runs = [];
for (const id of ["m2-heb-insert_dropout_large", "m3-heb-slide_disturbance_recoverable_ceiling"]) {
  const path = `results/recordings/registry/runs/${id}`;
  const d = detail(`${path}/artifacts`);
  write(`runs/${id}.json`, d);
  runs.push({
    id, source: "recordings", backend: `mujoco-${d.metadata.mujoco_version}`, scenario_id: d.metadata.scenario_id,
    system: d.metadata.system, disposition: d.metadata.outcome.disposition, bounded_success: d.metadata.outcome.bounded_success,
    has_3d: true, verified: true, path,
  });
}
for (const path of ["results/m5/reassemble-log", "results/m5/robosuite-log"]) { // sorted, as the server lists them
  const d = detail(path);
  write(`runs/${encodeURIComponent(path)}.json`, d);
  runs.push({
    id: path, source: "episode_log", backend: d.metadata.backend, scenario_id: d.metadata.task_id,
    system: d.metadata.system, disposition: d.metadata.outcome.disposition, bounded_success: d.metadata.outcome.bounded_success ?? null,
    has_3d: false, verified: false, path,
  });
}
write("runs.json", runs);

// ---- campaigns and pairs
const metrics = json("results/m4/campaign/official_metrics.json");
const raw = jsonl("results/m4/campaign/raw_rows.jsonl");
const replays = jsonl("results/m4/campaign/replay_index.jsonl");
const populations = Object.entries(metrics.populations).map(([pid, p]) => ({
  id: pid,
  n_scenarios: p.n_scenarios,
  arms: Object.keys(p.arms),
  confirmatory: Object.values(p.confirmatory).map((c) => ({
    id: c.id, candidate: c.candidate, reference: c.reference, decision: c.decision, field: c.field,
  })),
}));
const CAMPAIGN = "m4-campaign"; // studio/data.py campaign_dirs(): <results subdir>-<campaign dir>
write("campaigns.json", [{
  id: CAMPAIGN, scoring_epoch: metrics.scoring_epoch, source: "results/m4/campaign/official_metrics.json", populations,
}]);

for (const [pid, p] of Object.entries(metrics.populations)) {
  const rowsOf = (arm) => new Map(raw.filter((r) => r.arm === arm && r.experiment_id.endsWith(`campaign-${pid}`)).map((r) => [r.scenario_id, r]));
  for (const [key, pr] of Object.entries(p.paired)) {
    const [a, b] = key.split("_vs_");
    const ra = rowsOf(a);
    const rb = rowsOf(b);
    const rows = [...ra.keys()].sort().map((sid) => {
      const x = ra.get(sid);
      const replayOf = (arm) => replays.find((r) => r.population === pid && r.scenario_id === sid && r.arm === arm);
      const replay = replayOf(a) ?? replayOf(b);
      return {
        scenario_id: sid, a: x.bounded_success, b: rb.get(sid).bounded_success,
        stratum: x.tags.find((t) => t in (p.strata ?? {})) ?? null, replay_run_id: replay?.m0_run_id ?? null,
      };
    });
    const matrix = { both: pr.both, a_only: pr.rescued.length, b_only: pr.harmed.length, neither: pr.neither };
    // the fixture must agree with the committed metrics it claims to come from
    assert.equal(rows.filter((r) => r.a && r.b).length, matrix.both, `${pid} ${key} both`);
    assert.equal(rows.filter((r) => r.a && !r.b).length, matrix.a_only, `${pid} ${key} a_only`);
    assert.equal(rows.filter((r) => !r.a && r.b).length, matrix.b_only, `${pid} ${key} b_only`);
    assert.equal(rows.length, pr.delta_bounded_success.n_pairs, `${pid} ${key} n`);
    const d = pr.delta_bounded_success;
    write(`pairs/${CAMPAIGN}__${pid}__${a}__${b}.json`, {
      campaign_id: CAMPAIGN, population: pid, a, b, field: d.field,
      matrix, n: d.n_pairs, diff: d.delta, ci95: [d.ci_low, d.ci_high], method: d.method, rows,
      source: "results/m4/campaign/official_metrics.json",
    });
  }
}

// ---- catalog
const cost = json("results/m4/campaign/cost.json");
const FALLBACK = "fallback: one insert_nominal x haplous_mediated episode measured via studio.jobs (2.99 s)";
write("catalog.json", {
  scenarios: JSON.parse(readFileSync(join(here, "heb_scenarios.json"), "utf8")),
  // mirrors studio/jobs.py cpu_per_episode(): M4 measured mean per arm, else the measured fallback
  systems: ["haplous_mediated", "g1_incumbent", "bare_behavior", "competent_supervisor", "direct_minimal"].map((id) => {
    const c = cost.cpu_s_per_episode_by_arm[id];
    return c
      ? { id, cpu_s_per_episode: c.mean, cpu_source: `results/m4/campaign/cost.json mean, n=${c.n}` }
      : { id, cpu_s_per_episode: 3.0, cpu_source: FALLBACK };
  }),
  cap_cpu_minutes: 10,
  source: `benchmarks.heb.tasks.scenarios_by_id(); CPU-s per episode per system: measured mean from results/m4/campaign/cost.json where M4 ran the arm, else ${FALLBACK}`,
});

// ---- evidence
const tasks = json("docs/eval_program/TASKS.json").tasks.filter(Boolean);
write("evidence.json", {
  tasks: tasks.map((t) => ({
    id: t.id, title: t.title, status: t.status, verdict: t.verdict ?? null, commit: t.commit ?? null,
    owner: t.owner ?? null, proof: t.proof ?? [],
  })),
  known_issues: [
    { id: "R05-float-digit", title: "3 tests in tests/test_r05.py: locked N5 covariance scalar differs in last float digit", status: "pre-existing" },
    { id: "rollout-capture", title: "test_capture_localization_error_within_clearance: 0.50303 mm vs 0.5 mm threshold", status: "pre-existing" },
    { id: "hes-non-interference", title: "Committed hes qualify exits 1: non_interference gate 0.786 < 0.95", status: "open" },
  ],
  benchmarks: [
    { id: "heb", name: "HEB (pegsim MuJoCo)", executed: true },
    { id: "m4-campaign", name: "M4 confirmatory campaign (control + stress)", executed: true },
    { id: "robosuite", name: "robosuite 1.5.2 Lift (episode log)", executed: true },
    { id: "reassemble", name: "REASSEMBLE real-robot log import", executed: true },
    { id: "libero-dynamic", name: "LIBERO-Dynamic / VLA", executed: false, reason: "needs GPU and operator approval" },
  ],
  source: "docs/eval_program/TASKS.json, docs/eval_program/KNOWN_ISSUES.md",
});
console.log(`fixtures written to ${out}`);
