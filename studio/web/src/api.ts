// Types for the M6 API contract (docs/eval_program/designs/M6_haplous_studio.md).
// Fields marked "contract addition" are not in the design text; see studio/web/README.md.

export type Vec3 = [number, number, number];

export interface RunSummary {
  id: string;
  source: string;
  backend: string;
  scenario_id: string;
  system: string;
  disposition: string | null;
  bounded_success: boolean | null;
  has_3d: boolean;
  ledger_state: string | null; // contract change (#23): M0 ledger state at listing, not a re-hash; null outside M0
  path: string;
}

export interface RunEvent {
  source: string;
  kind: string;
  tick: number;
  t_s: number;
  payload: Record<string, unknown>;
}

export interface FaultWindow {
  name: string;
  start_s: number;
  end_s: number;
}

export interface RunDetail {
  metadata: Record<string, unknown>;
  series: {
    t_s: number[];
    force_z_n: (number | null)[];
    pose_m: (Vec3 | null)[];
    command_target_pose_m: (Vec3 | null)[];
  };
  events: RunEvent[];
  windows: FaultWindow[];
}

export interface Confirmatory {
  id: string;
  candidate: string;
  reference: string;
  decision: string;
}

export interface Campaign {
  id: string;
  source?: string; // contract addition: artifact path shown as the numbers' source
  populations: { id: string; n_scenarios: number; arms: string[]; confirmatory: Confirmatory[] }[];
}

export interface PairRow {
  scenario_id: string;
  a: boolean;
  b: boolean;
  stratum: string;
  replay_run_id: string | null;
}

export interface Pairs {
  matrix: { both: number; a_only: number; b_only: number; neither: number };
  n: number;
  diff: number;
  ci95: [number, number];
  rows: PairRow[];
  source?: string; // contract addition
}

export interface Catalog {
  scenarios: { id: string; task: string; stratum: string | null; label: string }[];
  systems: { id: string; cpu_s_per_episode: number; cpu_source?: string }[];
  cap_cpu_minutes: number; // contract addition
  source?: string;
}

export interface Job {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: { done: number; total: number };
  run_ids: string[];
  log_tail: string;
}

/** 409 body (FastAPI may wrap it in {detail}). contract addition */
export interface OverCap {
  estimate_cpu_minutes: number;
  cap_cpu_minutes: number;
}

export interface Evidence {
  tasks: { id: string; title: string; status: string; verdict: string | null; commit: string | null; owner: string | null; proof: string[] }[];
  known_issues: { id: string; title: string; status: string }[];
  benchmarks: { id: string; name: string; executed: boolean; reason?: string }[]; // contract addition
  source?: string;
}

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`HTTP ${status}`);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  runs: () => request<RunSummary[]>("GET", "/api/runs"),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${enc(id)}`),
  rrdUrl: (id: string) => `/api/runs/${enc(id)}/rrd`,
  campaigns: () => request<Campaign[]>("GET", "/api/campaigns"),
  pairs: (campaign: string, population: string, a: string, b: string) =>
    request<Pairs>("GET", `/api/campaigns/${enc(campaign)}/pairs?a=${enc(a)}&b=${enc(b)}&population=${enc(population)}`),
  catalog: () => request<Catalog>("GET", "/api/catalog"),
  jobs: () => request<Job[]>("GET", "/api/jobs"),
  launch: (scenario_ids: string[], systems: string[], confirm: boolean) =>
    request<{ job_id: string }>("POST", "/api/jobs", { scenario_ids, systems, confirm }),
  investigate: (body: { run_id: string; t_s: number; title: string; note: string; compare_run_id?: string }) =>
    request<{ path: string }>("POST", "/api/investigations", body),
  evidence: () => request<Evidence>("GET", "/api/evidence"),
};
