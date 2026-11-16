import { useEffect, useRef, useState } from "react";
import type { WebViewer, WebViewerOptions } from "@rerun-io/web-viewer";

// The replay .rrd (evaluation/replay.py) logs a "sim_time" duration timeline.
const TIMELINE = "sim_time";

/** Embedded Rerun viewer whose time cursor follows `t_s`. */
export function RerunView({ url, t_s }: { url: string; t_s: number | null }) {
  const el = useRef<HTMLDivElement>(null);
  const viewer = useRef<Promise<WebViewer | null> | null>(null);
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
        // seeks before the recording is open are rejected, so "ready" means open
        const opened = new Promise<void>((resolve) => v!.on("recording_open", () => resolve()));
        const channel = v.openChannel(url);
        channel.sendRrd(bytes);
        channel.close();
        await opened;
        return v;
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      });
    viewer.current = ready;
    return () => {
      void ready.then(() => v?.stop());
    };
  }, [url]);

  useEffect(() => {
    if (t_s == null) return;
    void viewer.current?.then((v) =>
      v?.setTime({ timeline: TIMELINE, durationNanos: Math.round(t_s * 1e9), playState: "paused" }).catch((e: unknown) => console.warn("rerun setTime", e)),
    );
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
