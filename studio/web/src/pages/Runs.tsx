import { useState } from "react";
import { Link } from "react-router";
import { api, type RunSummary } from "../api";
import { Gate, Source, outcomeOf, useLoad } from "../ui";

const FILTERS = [
  ["source", (r: RunSummary) => r.source],
  ["backend", (r: RunSummary) => r.backend],
  ["scenario", (r: RunSummary) => r.scenario_id],
  ["system", (r: RunSummary) => r.system],
  ["outcome", outcomeOf],
] as const;

export function Runs() {
  const runs = useLoad(api.runs, "runs");
  const [picked, setPicked] = useState<Record<string, string>>({});
  return (
    <section>
      <header className="page-head">
        <h1>Runs</h1>
      </header>
      <Gate value={runs}>
        {(all) => {
          const shown = all.filter((r) => FILTERS.every(([name, get]) => !picked[name] || get(r) === picked[name]));
          return (
            <>
              <div className="filters">
                {FILTERS.map(([name, get]) => (
                  <label key={name}>
                    {name}
                    <select value={picked[name] ?? ""} onChange={(e) => setPicked({ ...picked, [name]: e.target.value })}>
                      <option value="">all</option>
                      {[...new Set(all.map(get))].sort().map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                  </label>
                ))}
                <span className="muted num">
                  {shown.length} of {all.length}
                </span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Scenario</th>
                    <th>System</th>
                    <th>Backend</th>
                    <th>Outcome</th>
                    <th>Badges</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <Link to={`/runs/${encodeURIComponent(r.id)}`} title={r.path}>
                          {r.id}
                        </Link>
                        <div className="muted small">{r.source}</div>
                      </td>
                      <td className="mono">{r.scenario_id}</td>
                      <td className="mono">{r.system}</td>
                      <td className="small">{r.backend}</td>
                      <td>{outcomeOf(r)}</td>
                      <td>
                        {r.ledger_state === "VERIFIED" && (
                          <span className="badge" title="M0 ledger state. Every artifact is re-hashed (verified) when the run is opened.">
                            ledger: verified
                          </span>
                        )}
                        {r.has_3d && <span className="badge" title="MuJoCo state stream: 3D replay available">3D</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          );
        }}
      </Gate>
      <Source>
        <code>GET /api/runs</code>: results/recordings/registry, results/m5/*-log and Studio jobs. Each run's artifact path is on hover.
      </Source>
    </section>
  );
}
