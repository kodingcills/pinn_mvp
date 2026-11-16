import { useState } from "react";
import { Link } from "react-router";
import { api, type Campaign, type Pairs } from "../api";
import { Gate, Source, useLoad } from "../ui";

export function Compare() {
  const campaigns = useLoad(api.campaigns, "campaigns");
  return <Gate value={campaigns}>{(list) => (list.length ? <ComparePicker campaigns={list} /> : <p className="note">No campaigns found.</p>)}</Gate>;
}

function ComparePicker({ campaigns }: { campaigns: Campaign[] }) {
  const [cid, setCid] = useState(campaigns[0].id);
  const campaign = campaigns.find((c) => c.id === cid) ?? campaigns[0];
  const [pid, setPid] = useState(campaign.populations[0]?.id ?? "");
  const population = campaign.populations.find((p) => p.id === pid) ?? campaign.populations[0];
  const arms = population?.arms ?? [];
  const [a, setA] = useState(arms.includes("haplous_mediated") ? "haplous_mediated" : arms[0]);
  const [b, setB] = useState(arms.includes("g1_incumbent") ? "g1_incumbent" : arms[1]);
  const pairs = useLoad(() => api.pairs(campaign.id, population.id, a, b), `${campaign.id}|${population?.id}|${a}|${b}`);
  const confirmatory = population?.confirmatory.find((c) => (c.candidate === a && c.reference === b) || (c.candidate === b && c.reference === a));

  const pick = (label: string, value: string, set: (v: string) => void, options: string[]) => (
    <label>
      {label}
      <select value={value} onChange={(e) => set(e.target.value)}>
        {options.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    </label>
  );

  return (
    <section>
      <header className="page-head">
        <h1>Compare</h1>
      </header>
      <div className="filters">
        {pick("campaign", campaign.id, setCid, campaigns.map((c) => c.id))}
        {pick("population", population?.id ?? "", setPid, campaign.populations.map((p) => p.id))}
        {pick("arm A", a, setA, arms)}
        {pick("arm B", b, setB, arms)}
        {confirmatory ? (
          <span className="badge ok" title={`${confirmatory.id}: ${confirmatory.candidate} vs ${confirmatory.reference}`}>
            confirmatory {confirmatory.id}: {confirmatory.decision}
          </span>
        ) : (
          <span className="badge warn">exploratory (not pre-registered)</span>
        )}
      </div>
      {a === b ? (
        <p className="note">Pick two different arms.</p>
      ) : (
        <Gate value={pairs}>{(p) => <PairsView pairs={p} a={a} b={b} />}</Gate>
      )}
      <Source>
        <code>GET /api/campaigns/{campaign.id}/pairs</code> from {pairs.state === "ready" && pairs.data.source ? pairs.data.source : campaign.source ?? "committed campaign metrics"}.
        Numbers are shown exactly as the server returns them.
      </Source>
    </section>
  );
}

export function PairsView({ pairs, a, b }: { pairs: Pairs; a: string; b: string }) {
  const { matrix, n, diff, ci95, rows } = pairs;
  const discordant = rows.filter((r) => r.a !== r.b);
  return (
    <>
      <div className="stats">
        <table className="matrix" aria-label="paired outcome matrix">
          <thead>
            <tr>
              <th />
              <th>B ok ({b})</th>
              <th>B fail</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>A ok ({a})</th>
              <td data-cell="both" className="num">{matrix.both}</td>
              <td data-cell="a_only" className="num">{matrix.a_only}</td>
            </tr>
            <tr>
              <th>A fail</th>
              <td data-cell="b_only" className="num">{matrix.b_only}</td>
              <td data-cell="neither" className="num">{matrix.neither}</td>
            </tr>
          </tbody>
        </table>
        <dl className="kv">
          <dt>n (pairs)</dt>
          <dd className="num" data-stat="n">{String(n)}</dd>
          <dt>diff (A − B, bounded success)</dt>
          <dd className="num" data-stat="diff">{String(diff)}</dd>
          <dt>95% CI</dt>
          <dd className="num" data-stat="ci95">
            [{String(ci95[0])}, {String(ci95[1])}]
          </dd>
        </dl>
      </div>
      <h2>Discordant scenarios ({discordant.length})</h2>
      {discordant.length === 0 ? (
        <p className="note">No discordant pairs: {a} and {b} agree on every one of the {String(n)} scenarios.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Stratum</th>
              <th>A</th>
              <th>B</th>
              <th>Replay</th>
            </tr>
          </thead>
          <tbody>
            {discordant.map((r) => (
              <tr key={r.scenario_id}>
                <td className="mono">{r.scenario_id}</td>
                <td className="small">{r.stratum}</td>
                <td>{r.a ? "ok" : "fail"}</td>
                <td>{r.b ? "ok" : "fail"}</td>
                <td>{r.replay_run_id ? <Link to={`/runs/${encodeURIComponent(r.replay_run_id)}`}>{r.replay_run_id}</Link> : <span className="muted">not recorded</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
