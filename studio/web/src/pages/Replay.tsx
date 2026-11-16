import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError, type RunDetail, type RunSummary, type Vec3 } from "../api";
import { Chart, type Line } from "../Chart";
import { RerunView } from "../RerunView";
import { Gate, Source, outcomeOf, useLoad } from "../ui";

const AXES = ["x", "y", "z"] as const;
const COLORS = ["#2f6fdb", "#c2571a", "#2b8a3e"];
const xyz = (rows: (Vec3 | null)[], prefix: string): Line[] =>
  AXES.map((a, i) => ({ name: `${prefix}${a}`, color: COLORS[i], values: rows.map((p) => (p ? p[i] : null)) }));

export function Replay() {
  const id = useParams().id ?? "";
  const data = useLoad(() => Promise.all([api.run(id), api.runs()]), id);
  return (
    <Gate value={data}>
      {([detail, runs]) => <ReplayView id={id} detail={detail} runs={runs} />}
    </Gate>
  );
}

export function ReplayView({ id, detail, runs }: { id: string; detail: RunDetail; runs: RunSummary[] }) {
  const summary = runs.find((r) => r.id === id);
  const { series, events, windows } = detail;
  const domain: [number, number] = [series.t_s[0] ?? 0, series.t_s[series.t_s.length - 1] ?? 1];
  const ordered = [...events].sort((x, y) => x.t_s - y.t_s); // stable: same-time events keep server order
  const [t, setT] = useState<number | null>(null);
  const [investigating, setInvestigating] = useState(false);
  const chart = { t: series.t_s, windows, domain, cursor: t, onScrub: setT };
  const path = String(detail.metadata.path ?? summary?.path ?? id);

  return (
    <section>
      <header className="page-head">
        <h1 className="mono">{id}</h1>
        <div className="head-meta">
          {summary && (
            <>
              <span className="mono">{summary.scenario_id}</span>
              <span className="mono">{summary.system}</span>
              <span>{outcomeOf(summary)}</span>
              {summary.verified && <span className="badge ok">verified</span>}
            </>
          )}
          <span className="num">t = {t == null ? "—" : `${t.toFixed(4)} s`}</span>
          <button className="primary" onClick={() => setInvestigating(true)}>
            Create investigation
          </button>
        </div>
      </header>

      <div className="replay">
        <div className="replay-main">
          {summary?.has_3d ? (
            <RerunView url={api.rrdUrl(id)} t_s={t} />
          ) : (
            <p className="note">
              No 3D replay for this run: it has no MuJoCo state stream ({summary?.backend ?? "unknown backend"}). Charts and events come from the
              episode log.
            </p>
          )}
          <Chart title="Force z" unit="N" lines={[{ name: "force_z", color: COLORS[0], values: series.force_z_n }]} {...chart} />
          <Chart title="Pose" unit="m" lines={xyz(series.pose_m, "pose_")} {...chart} />
          <Chart title="Command target" unit="m" lines={xyz(series.command_target_pose_m, "target_")} {...chart} />
          <p className="muted small">
            Shaded: fault windows ({windows.map((w) => `${w.name} ${w.start_s}–${w.end_s} s`).join(", ") || "none"}). Gaps: null observations.
            Drag or use arrow keys on a chart to move the shared cursor.
          </p>
        </div>
        <aside className="events">
          <h2>Events ({events.length})</h2>
          <ol>
            {ordered.map((e, i) => (
              <li key={i}>
                <button className={t === e.t_s ? "event active" : "event"} onClick={() => setT(e.t_s)} title={JSON.stringify(e.payload)}>
                  <span className="num">{e.t_s.toFixed(4)}</span>
                  <span className={`src src-${e.source}`}>{e.source}</span>
                  <span className="kind">{e.kind}</span>
                </button>
              </li>
            ))}
          </ol>
        </aside>
      </div>
      <Source>
        <code>GET /api/runs/{id}</code> from <code>{path}</code> (telemetry.jsonl, events.jsonl, metadata.json)
        {summary?.has_3d && (
          <>
            ; 3D from <code>GET /api/runs/{id}/rrd</code> (evaluation.replay)
          </>
        )}
        .
      </Source>
      {investigating && <InvestigationDialog runId={id} t_s={t ?? domain[0]} runs={runs} onClose={() => setInvestigating(false)} />}
      <p>
        <Link to="/">← Runs</Link>
      </p>
    </section>
  );
}

export function InvestigationDialog(props: { runId: string; t_s: number; runs: RunSummary[]; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [t, setT] = useState(String(props.t_s));
  const [compare, setCompare] = useState("");
  const [result, setResult] = useState<{ path?: string; error?: string } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const body = { run_id: props.runId, t_s: Number(t), title, note, ...(compare ? { compare_run_id: compare } : {}) };
      setResult({ path: (await api.investigate(body)).path });
    } catch (err) {
      setResult({ error: err instanceof ApiError ? `HTTP ${err.status}: ${JSON.stringify(err.body)}` : String(err) });
    }
  };
  return (
    <div className="backdrop">
      <form role="dialog" aria-label="Create investigation" className="dialog" onSubmit={submit}>
        <h2>Create investigation</h2>
        <label>
          Title
          <input required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Note
          <textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <label>
          t (s)
          <input type="number" step="any" required value={t} onChange={(e) => setT(e.target.value)} />
        </label>
        <label>
          Compare run (optional)
          <select value={compare} onChange={(e) => setCompare(e.target.value)}>
            <option value="">none</option>
            {props.runs
              .filter((r) => r.id !== props.runId)
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id}
                </option>
              ))}
          </select>
        </label>
        {result?.path && (
          <p className="note ok">
            Written: <code>{result.path}</code>. Commit it and start a Claude session on it.
          </p>
        )}
        {result?.error && <p className="note warn">{result.error}</p>}
        <div className="actions">
          <button type="button" onClick={props.onClose}>
            Close
          </button>
          <button className="primary" type="submit" disabled={!!result?.path}>
            Write investigation
          </button>
        </div>
      </form>
    </div>
  );
}
