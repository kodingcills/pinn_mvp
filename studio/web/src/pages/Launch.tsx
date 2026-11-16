import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, ApiError, type Catalog, type Job, type OverCap } from "../api";
import { Gate, Source, useLoad } from "../ui";

export function Launch() {
  const catalog = useLoad(api.catalog, "catalog");
  return (
    <section>
      <header className="page-head">
        <h1>Launch</h1>
      </header>
      <Gate value={catalog}>{(c) => <LaunchForm catalog={c} />}</Gate>
      <JobList />
    </section>
  );
}

type Submit = { state: "idle" } | { state: "over_cap"; overCap: OverCap } | { state: "error"; message: string } | { state: "launched"; jobId: string };

export function LaunchForm({ catalog }: { catalog: Catalog }) {
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [systems, setSystems] = useState<string[]>(["haplous_mediated"]);
  const [submit, setSubmit] = useState<Submit>({ state: "idle" });
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const perEpisode = catalog.systems.filter((s) => systems.includes(s.id)).reduce((acc, s) => acc + s.cpu_s_per_episode, 0);
  const estimateMin = (scenarios.length * perEpisode) / 60;

  const launch = async (confirm: boolean) => {
    try {
      setSubmit({ state: "launched", jobId: (await api.launch(scenarios, systems, confirm)).job_id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const body = e.body as { detail?: OverCap } & OverCap;
        setSubmit({ state: "over_cap", overCap: body.detail ?? body });
      } else setSubmit({ state: "error", message: e instanceof ApiError ? `HTTP ${e.status}: ${JSON.stringify(e.body)}` : String(e) });
    }
  };

  return (
    <div className="launch">
      <fieldset>
        <legend>Scenarios ({scenarios.length} selected)</legend>
        <table>
          <thead>
            <tr>
              <th />
              <th>Scenario</th>
              <th>Task</th>
              <th>Stratum</th>
              <th>Label</th>
            </tr>
          </thead>
          <tbody>
            {catalog.scenarios.map((s) => (
              <tr key={s.id}>
                <td>
                  <input type="checkbox" aria-label={s.id} checked={scenarios.includes(s.id)} onChange={() => setScenarios(toggle(scenarios, s.id))} />
                </td>
                <td className="mono">{s.id}</td>
                <td className="small">{s.task}</td>
                <td className="small">{s.stratum}</td>
                <td>
                  <span className="badge">{s.label}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </fieldset>
      <fieldset>
        <legend>Systems</legend>
        {catalog.systems.map((s) => (
          <label key={s.id} className="check">
            <input type="checkbox" aria-label={s.id} checked={systems.includes(s.id)} onChange={() => setSystems(toggle(systems, s.id))} />
            <span className="mono">{s.id}</span>
            <span className="muted num" title={s.cpu_source}>{s.cpu_s_per_episode.toFixed(2)} CPU-s/episode</span>
          </label>
        ))}
        <p className="estimate">
          Estimate: <b className="num">{estimateMin.toFixed(2)} CPU-min</b> for {scenarios.length * systems.length} episodes (cap{" "}
          <span className="num">{catalog.cap_cpu_minutes}</span> CPU-min). The server's estimate is authoritative.
        </p>
        <button className="primary" disabled={!scenarios.length || !systems.length} onClick={() => launch(false)}>
          Launch
        </button>
        {submit.state === "over_cap" && (
          <div className="note warn" role="alert">
            Over the compute cap: server estimate <b className="num">{submit.overCap.estimate_cpu_minutes.toFixed(2)} CPU-min</b> exceeds{" "}
            <span className="num">{submit.overCap.cap_cpu_minutes}</span> CPU-min.
            <div className="actions">
              <button onClick={() => setSubmit({ state: "idle" })}>Cancel</button>
              <button className="primary" onClick={() => launch(true)}>
                Confirm and launch anyway
              </button>
            </div>
          </div>
        )}
        {submit.state === "error" && <p className="note warn">{submit.message}</p>}
        {submit.state === "launched" && <p className="note ok">Launched {submit.jobId}.</p>}
        <Source>{catalog.source ?? <code>GET /api/catalog</code>}</Source>
      </fieldset>
    </div>
  );
}

function JobList() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () =>
      api.jobs().then(
        (j) => live && (setJobs(j), setError(null)),
        (e: unknown) => live && setError(String(e)),
      );
    void tick();
    const timer = setInterval(tick, 2000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);
  return (
    <div>
      <h2>Jobs</h2>
      {error && <p className="note warn">Job poll failed: {error}</p>}
      {jobs && jobs.length === 0 && <p className="muted">No jobs yet.</p>}
      {jobs && jobs.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Runs</th>
              <th>Log</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.job_id}>
                <td className="mono">{j.job_id}</td>
                <td>
                  <span className={`badge ${j.status === "succeeded" ? "ok" : j.status === "failed" ? "warn" : ""}`}>{j.status}</span>
                </td>
                <td>
                  <progress max={j.progress.total} value={j.progress.done} /> <span className="num">{j.progress.done}/{j.progress.total}</span>
                </td>
                <td>
                  {j.run_ids.map((r) => (
                    <div key={r}>
                      <Link to={`/runs/${encodeURIComponent(r)}`}>{r}</Link>
                    </div>
                  ))}
                </td>
                <td>
                  <code className="small">{j.log_tail}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Source>
        <code>GET /api/jobs</code>, polled every 2 s. Each finished episode is an M0 run.
      </Source>
    </div>
  );
}
