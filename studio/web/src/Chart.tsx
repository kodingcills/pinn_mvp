import type { FaultWindow } from "./api";

export interface Line {
  name: string;
  color: string;
  values: (number | null)[];
}

const W = 1000;
const PAD = { l: 64, r: 12, t: 8, b: 20 };

/** SVG path for one series; a null sample ends the current subpath, so missing data draws as a gap. */
export function linePath(t: number[], v: (number | null)[], x: (t: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < t.length; i++) {
    const value = v[i];
    if (value == null) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x(t[i]).toFixed(1)},${y(value).toFixed(1)}`;
    pen = true;
  }
  return d;
}

/** Index of the last sample at or before time `at`. */
export function indexAt(t: number[], at: number): number {
  let lo = 0;
  let hi = t.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (t[mid] <= at) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

const fmt = (v: number | null | undefined) => (v == null ? "gap" : Math.abs(v) < 0.01 && v !== 0 ? v.toExponential(3) : v.toFixed(4));

export function Chart(props: {
  title: string;
  unit: string;
  t: number[];
  lines: Line[];
  windows: FaultWindow[];
  domain: [number, number];
  cursor: number | null;
  onScrub: (t_s: number) => void;
  height?: number;
}) {
  const { t, lines, windows, domain, cursor, onScrub } = props;
  const H = props.height ?? 140;
  const all = lines.flatMap((l) => l.values.filter((v): v is number => v != null));
  let lo = all.length ? Math.min(...all) : 0;
  let hi = all.length ? Math.max(...all) : 1;
  if (hi - lo < 1e-12) [lo, hi] = [lo - 1, hi + 1];
  const span = domain[1] - domain[0] || 1;
  const x = (s: number) => PAD.l + ((s - domain[0]) / span) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const scrubFrom = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / (r.width || 1)) * W; // viewBox x; aspect ratio is not preserved
    const frac = (vx - PAD.l) / (W - PAD.l - PAD.r);
    onScrub(domain[0] + Math.min(1, Math.max(0, frac)) * span);
  };
  const at = cursor == null ? null : indexAt(t, cursor);
  return (
    <figure className="chart">
      <figcaption>
        <span className="chart-title">{props.title}</span>
        <span className="chart-unit">{props.unit}</span>
        {lines.map((l) => (
          <span key={l.name} className="legend">
            <i style={{ background: l.color }} />
            {l.name}
            {at != null && <b className="num">{fmt(l.values[at])}</b>}
          </span>
        ))}
      </figcaption>
      <svg
        role="slider"
        aria-label={`${props.title} time cursor`}
        aria-valuemin={domain[0]}
        aria-valuemax={domain[1]}
        aria-valuenow={cursor ?? domain[0]}
        tabIndex={0}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ height: H }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture?.(e.pointerId);
          scrubFrom(e);
        }}
        onPointerMove={(e) => e.buttons === 1 && scrubFrom(e)}
        onKeyDown={(e) => {
          const now = cursor ?? domain[0];
          const to = { ArrowRight: now + span / 100, ArrowLeft: now - span / 100, PageUp: now + span / 10, PageDown: now - span / 10, Home: domain[0], End: domain[1] }[e.key];
          if (to === undefined) return;
          e.preventDefault();
          onScrub(Math.min(domain[1], Math.max(domain[0], to)));
        }}
      >
        {windows.map((w) => (
          <rect key={`${w.name}${w.start_s}`} className="fault" x={x(w.start_s)} width={Math.max(1, x(w.end_s) - x(w.start_s))} y={PAD.t} height={H - PAD.t - PAD.b}>
            <title>{`${w.name} ${w.start_s}s to ${w.end_s}s`}</title>
          </rect>
        ))}
        <line className="axis" x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} />
        <text className="tick" x={PAD.l - 6} y={PAD.t + 8} textAnchor="end">{fmt(hi)}</text>
        <text className="tick" x={PAD.l - 6} y={H - PAD.b} textAnchor="end">{fmt(lo)}</text>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <text key={f} className="tick" x={x(domain[0] + f * span)} y={H - 4} textAnchor="middle">
            {(domain[0] + f * span).toFixed(2)}s
          </text>
        ))}
        {lines.map((l) => (
          <path key={l.name} data-series={l.name} d={linePath(t, l.values, x, y)} stroke={l.color} fill="none" vectorEffect="non-scaling-stroke" />
        ))}
        {cursor != null && <line className="cursor" data-testid="cursor" data-t={cursor} x1={x(cursor)} x2={x(cursor)} y1={PAD.t} y2={H - PAD.b} />}
      </svg>
    </figure>
  );
}
