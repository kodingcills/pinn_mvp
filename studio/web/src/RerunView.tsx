import { useEffect, useRef, useState } from "react";
import type { WebViewer, WebViewerOptions } from "@rerun-io/web-viewer";

// The replay .rrd (evaluation/replay.py) logs a "sim_time" duration timeline.
const TIMELINE = "sim_time";
const LOAD_TIMEOUT_MS = 60_000;

/** Resolves once the active recording has the timeline and nothing is still loading. */
async function loaded(v: WebViewer): Promise<void> {
  const end = Date.now() + LOAD_TIMEOUT_MS;
  for (;;) {
    const state = await v.getState();
    const active = state.recordings.find((r) => r.storeId === state.activeStoreId);
    if (active?.timelines.some((t) => t.name === TIMELINE) && state.loading.length === 0) return;
    if (Date.now() > end) throw new Error(`the recording's ${TIMELINE} timeline did not load within ${LOAD_TIMEOUT_MS / 1000} s`);
    await new Promise((r) => setTimeout(r, 200));
  }
}

/** Embedded Rerun viewer whose time cursor follows `t_s`. */
export function RerunView({ url, t_s }: { url: string; t_s: number | null }) {
  const el = useRef<HTMLDivElement>(null);
  const viewer = useRef<Promise<WebViewer | null> | null>(null);
  const wanted = useRef<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let v: WebViewer | null = null;
    const options = {
      hideWelcomeScreen: true,
      checkForUpdatesOnStartup: false,
      width: "100%",
      height: "100%",
      // undocumented but supported in 0.43.1: where re_viewer_bg.wasm is fetched from
      baseUrl: new URL("/rerun-web-viewer/", window.location.href).href,
    } as WebViewerOptions;
    const ready = import("@rerun-io/web-viewer")
      .then(async ({ WebViewer }) => {
        v = new WebViewer();
        // The viewer only opens HTTP URLs whose path ends in ".rrd", so fetch the bytes and stream them in.
        const [res] = await Promise.all([fetch(url), v.start(null, el.current, options)]);
        if (!res.ok) throw new Error(`GET ${url} answered ${res.status}`);
        const bytes = new Uint8Array(await res.arrayBuffer());
        const channel = v.openChannel(url);
        channel.sendRrd(bytes);
        channel.close();
        // "recording_open" fires before the timelines arrive, and setTime rejects until they do
        await loaded(v);
        if (el.current) el.current.dataset.viewerReady = "true";
        return v;
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      });
    viewer.current = ready;
    void ready.then(seek);
    return () => {
      void ready.then(() => v?.stop());
    };
  }, [url]);

  // Seeks to the latest wanted time, whenever it runs; a no-op until the recording has loaded.
  async function seek(v: WebViewer | null) {
    const t = wanted.current;
    if (v == null || t == null) return;
    try {
      await v.setTime({ timeline: TIMELINE, durationNanos: Math.round(t * 1e9), playState: "paused" });
      // the viewer's own cursor after the seek, on the DOM for end-to-end checks
      const state = await v.getState();
      const time = state.recordings.find((r) => r.storeId === state.activeStoreId)?.currentTime?.time;
      if (time != null && el.current) el.current.dataset.viewerTimeNs = String(time);
    } catch (e) {
      console.warn("rerun setTime", e);
    }
  }

  useEffect(() => {
    wanted.current = t_s;
    void viewer.current?.then((v) => (v && wanted.current === t_s ? seek(v) : undefined));
  }, [t_s]);

  return (
    <div className="rerun" ref={el}>
      {error && (
        <p className="note warn">
          Rerun viewer did not start: {error}. The wasm is served from <code>/rerun-web-viewer/re_viewer_bg.wasm</code>; charts and events below
          do not depend on it.
        </p>
      )}
    </div>
  );
}
