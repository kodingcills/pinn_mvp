import { api } from "../api";
import { Gate, Source, useLoad } from "../ui";

export function Evidence() {
  const evidence = useLoad(api.evidence, "evidence");
  return (
    <section>
      <header className="page-head">
        <h1>Evidence</h1>
      </header>
      <Gate value={evidence}>
        {(ev) => (
          <>
            <h2>Benchmarks</h2>
            <ul className="bench">
              {ev.benchmarks.map((b) => (
                <li key={b.id} className={b.executed ? "" : "not-run"}>
                  <span className={`badge ${b.executed ? "ok" : "warn"}`}>{b.executed ? "executed" : "not executed"}</span> {b.name}
                  {b.reason && <span className="muted"> ({b.reason})</span>}
                </li>
              ))}
            </ul>
            <h2>Tasks</h2>
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th>Commit</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {ev.tasks.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.id}</td>
                    <td className="small" title={t.proof.join("\n")}>
                      {t.title}
                    </td>
                    <td>
                      <span className={`badge ${t.status === "verified" || t.status === "pass" ? "ok" : ""}`}>{t.status}</span>
                    </td>
                    <td className="small clamp" title={t.verdict ?? ""}>
                      {t.verdict ?? <span className="muted">none</span>}
                    </td>
                    <td className="mono">{t.commit?.slice(0, 8) ?? "—"}</td>
                    <td className="small">{t.owner ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2>Known issues</h2>
            <ul>
              {ev.known_issues.map((k) => (
                <li key={k.id}>
                  <span className="badge">{k.status}</span> {k.title}
                </li>
              ))}
            </ul>
            <Source>{ev.source ?? <code>GET /api/evidence</code>}. Proof paths per task are on hover.</Source>
          </>
        )}
      </Gate>
    </section>
  );
}
