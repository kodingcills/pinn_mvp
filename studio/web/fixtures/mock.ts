// Fixture-backed stand-in for the M6.1 server. Used by the Vite dev server and by tests only;
// nothing here is imported by src/, so it never reaches the production bundle.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const dir = import.meta.dirname;
const load = (name: string) => {
  const p = join(dir, name);
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : undefined;
};

export interface MockResponse {
  status: number;
  body: unknown;
}

interface MockJob {
  job_id: string;
  status: string;
  progress: { done: number; total: number };
  run_ids: string[];
  log_tail: string;
  started: number;
}
const jobs: MockJob[] = [];

const ok = (body: unknown): MockResponse => (body === undefined ? { status: 404, body: { detail: "not found" } } : { status: 200, body });

export function mockApi(method: string, rawUrl: string, body?: any): MockResponse {
  const url = new URL(rawUrl, "http://localhost");
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent); // ["api", ...]
  const [, head, id, tail] = parts;
  if (method === "GET" && head === "runs" && !id) return ok(load("runs.json"));
  if (method === "GET" && head === "runs" && id && !tail) return ok(load(`runs/${encodeURIComponent(id)}.json`));
  if (method === "GET" && head === "runs" && tail === "rrd") return { status: 404, body: { detail: "no rrd in fixtures" } };
  if (method === "GET" && head === "campaigns" && !id) return ok(load("campaigns.json"));
  if (method === "GET" && head === "campaigns" && tail === "pairs") {
    const q = url.searchParams;
    return ok(load(`pairs/${id}__${q.get("population")}__${q.get("a")}__${q.get("b")}.json`));
  }
  if (method === "GET" && head === "catalog") return ok(load("catalog.json"));
  if (method === "GET" && head === "evidence") return ok(load("evidence.json"));
  if (method === "GET" && head === "jobs") {
    for (const j of jobs) {
      // fake progress: one episode per second
      j.progress.done = Math.min(j.progress.total, Math.floor((Date.now() - j.started) / 1000));
      j.status = j.progress.done >= j.progress.total ? "succeeded" : "running";
      j.run_ids = j.status === "succeeded" ? ["m2-heb-insert_dropout_large"] : [];
      j.log_tail = `episode ${j.progress.done}/${j.progress.total}`;
    }
    const view = jobs.map(({ started, ...j }) => j);
    return ok(id ? view.find((j) => j.job_id === id) : view);
  }
  if (method === "POST" && head === "jobs") {
    const catalog = load("catalog.json");
    const known = new Set(catalog.scenarios.map((s: { id: string }) => s.id));
    const systems = new Map<string, number>(catalog.systems.map((s: { id: string; cpu_s_per_episode: number }) => [s.id, s.cpu_s_per_episode]));
    if (!body?.scenario_ids?.length || !body?.systems?.length || body.scenario_ids.some((s: string) => !known.has(s)) || body.systems.some((s: string) => !systems.has(s)))
      return { status: 400, body: { detail: "unknown scenario or system" } };
    const cpuS = body.scenario_ids.length * body.systems.reduce((acc: number, s: string) => acc + (systems.get(s) ?? 0), 0);
    const estimate = cpuS / 60;
    if (estimate > catalog.cap_cpu_minutes && !body.confirm)
      return { status: 409, body: { detail: { estimate_cpu_minutes: estimate, cap_cpu_minutes: catalog.cap_cpu_minutes } } };
    const job: MockJob = {
      job_id: `job-${jobs.length + 1}`, status: "queued", started: Date.now(), run_ids: [], log_tail: "",
      progress: { done: 0, total: body.scenario_ids.length * body.systems.length },
    };
    jobs.push(job);
    return { status: 200, body: { job_id: job.job_id } };
  }
  if (method === "POST" && head === "investigations") {
    const slug = String(body?.title ?? "investigation").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return { status: 200, body: { path: `docs/eval_program/investigations/2026-11-16-${slug}.md` } };
  }
  return { status: 404, body: { detail: "no such endpoint" } };
}

export function resetMock() {
  jobs.length = 0;
}
