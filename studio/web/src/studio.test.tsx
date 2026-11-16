import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { InvestigationDialog, Replay } from "./pages/Replay";
import { Compare } from "./pages/Compare";
import { LaunchForm } from "./pages/Launch";
import { Chart } from "./Chart";
import type { Catalog } from "./api";

const viewer = vi.hoisted(() => ({ setTime: vi.fn(async () => undefined), start: vi.fn(async () => undefined), channel: { sendRrd: vi.fn(), close: vi.fn() } }));
vi.mock("@rerun-io/web-viewer", () => ({
  WebViewer: class {
    start = viewer.start;
    setTime = viewer.setTime;
    openChannel = () => viewer.channel;
    on = (_event: string, cb: () => void) => (setTimeout(cb), () => undefined);
    stop = vi.fn();
  },
}));

const M2 = "m2-heb-insert_dropout_large";

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:id" element={<Replay />} />
        <Route path="/compare" element={<Compare />} />
      </Routes>
    </MemoryRouter>,
  );
}

const cursors = () => screen.getAllByTestId("cursor").map((c) => Number(c.getAttribute("data-t")));
const subpaths = (d: string) => d.split("M").filter(Boolean);

describe("Replay", () => {
  it("clicking an event moves every chart cursor and seeks the Rerun viewer to that sim time", async () => {
    at(`/runs/${M2}`);
    // dropout_window_start in the committed M2 events.jsonl is at t = 1.0 s
    await userEvent.click(await screen.findByRole("button", { name: /dropout_window_start/ }));
    expect(cursors()).toEqual([1, 1, 1]);
    await waitFor(() => expect(viewer.channel.sendRrd).toHaveBeenCalled());
    await waitFor(() => expect(viewer.setTime).toHaveBeenLastCalledWith({ timeline: "sim_time", durationNanos: 1_000_000_000, playState: "paused" }));
  });

  it("scrubbing one chart moves the others", async () => {
    at(`/runs/${M2}`);
    const [force] = await screen.findAllByRole("slider");
    force.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 140, right: 1000, bottom: 140, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(force, { clientX: 1000, buttons: 1 }); // right edge = last sample
    const [a, b, c] = cursors();
    expect(a).toBeCloseTo(1.825, 3);
    expect([b, c]).toEqual([a, a]);
  });

  it("draws null observations as gaps, never as zeros", async () => {
    at(`/runs/${M2}`);
    await screen.findAllByRole("slider");
    // the committed M2 run loses observations from 1.0 s to 1.2 s: force must break into two pieces
    const d = document.querySelector('path[data-series="force_z"]')!.getAttribute("d")!;
    expect(subpaths(d)).toHaveLength(2);
  });

  it("says why a run without a state stream has no 3D view", async () => {
    viewer.start.mockClear();
    at(`/runs/${encodeURIComponent("results/m5/robosuite-log")}`);
    expect(await screen.findByText(/No 3D replay for this run/)).toBeInTheDocument();
    expect(viewer.start).not.toHaveBeenCalled();
  });

  it("investigation dialog posts run, current time, title, note and compare run", async () => {
    at(`/runs/${M2}`);
    await userEvent.click(await screen.findByRole("button", { name: /temporal_hold_entered/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create investigation" }));
    const dialog = screen.getByRole("dialog", { name: "Create investigation" });
    await userEvent.type(within(dialog).getByLabelText("Title"), "Hold during dropout");
    await userEvent.type(within(dialog).getByLabelText("Note"), "check resume");
    await userEvent.selectOptions(within(dialog).getByLabelText("Compare run (optional)"), "m3-heb-slide_disturbance_recoverable_ceiling");
    await userEvent.click(within(dialog).getByRole("button", { name: "Write investigation" }));
    expect(await within(dialog).findByText("docs/eval_program/investigations/2026-11-16-hold-during-dropout.md")).toBeInTheDocument();
    const post = vi.mocked(fetch).mock.calls.find(([url, init]) => url === "/api/investigations" && init?.method === "POST")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({
      run_id: M2,
      t_s: 0.9994999999999453, // temporal_hold_entered t_s in the committed events.jsonl
      title: "Hold during dropout",
      note: "check resume",
      compare_run_id: "m3-heb-slide_disturbance_recoverable_ceiling",
    });
  });
});

describe("Chart", () => {
  it("breaks the line at every null sample", () => {
    const { container } = render(
      <Chart title="f" unit="N" t={[0, 1, 2, 3, 4, 5]} lines={[{ name: "f", color: "red", values: [0, 1, null, null, 2, 3] }]} windows={[]} domain={[0, 5]} cursor={null} onScrub={() => {}} />,
    );
    const pieces = subpaths(container.querySelector("path")!.getAttribute("d")!);
    expect(pieces.map((p) => p.split("L").length)).toEqual([2, 2]);
  });

  it("Home, End, PageUp and PageDown move the cursor within the domain", () => {
    const onScrub = vi.fn();
    render(<Chart title="f" unit="N" t={[0, 10]} lines={[{ name: "f", color: "red", values: [0, 1] }]} windows={[]} domain={[0, 10]} cursor={5} onScrub={onScrub} />);
    const slider = screen.getByRole("slider");
    for (const key of ["Home", "End", "PageUp", "PageDown"]) fireEvent.keyDown(slider, { key });
    expect(onScrub.mock.calls.map(([t]) => t)).toEqual([0, 10, 6, 4]);
  });
});

describe("Investigation dialog", () => {
  it("Escape closes it and Tab stays inside it", async () => {
    const onClose = vi.fn();
    render(<InvestigationDialog runId={M2} t_s={1} runs={[]} onClose={onClose} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Title")).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Write investigation" })).toHaveFocus();
    await userEvent.tab();
    expect(within(dialog).getByLabelText("Title")).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("Compare", () => {
  // expected values are copied from results/m4/campaign/official_metrics.json, control population
  it("renders the server's matrix, n, diff and CI verbatim, with discordant rows", async () => {
    at("/compare");
    await userEvent.selectOptions(await screen.findByLabelText("arm B"), "bare_behavior");
    expect(await screen.findByText("0.09944751381215469")).toBeInTheDocument();
    const cell = (k: string) => document.querySelector(`[data-cell="${k}"]`)!.textContent;
    expect([cell("both"), cell("a_only"), cell("b_only"), cell("neither")]).toEqual(["22", "19", "1", "139"]);
    expect(document.querySelector('[data-stat="n"]')!.textContent).toBe("181");
    expect(document.querySelector('[data-stat="ci95"]')!.textContent).toBe("[0.055248618784530384, 0.14917127071823205]");
    expect(screen.getByText("Discordant scenarios (20)")).toBeInTheDocument();
    expect(screen.getByText("exploratory (not pre-registered)")).toBeInTheDocument();
  });

  it("shows the confirmatory label and handles zero discordant pairs (Haplous vs G1)", async () => {
    at("/compare");
    expect(await screen.findByText(/No discordant pairs/)).toBeInTheDocument();
    expect(screen.getByText(/confirmatory C2: tie/)).toBeInTheDocument();
    expect(document.querySelector('[data-stat="diff"]')!.textContent).toBe("0");
  });
});

describe("Launch", () => {
  const catalog: Catalog = {
    scenarios: [{ id: "insert_nominal", task: "peg_insertion", stratum: "physical", label: "DEV" }],
    systems: [{ id: "haplous_mediated", cpu_s_per_episode: 1.2 }],
    cap_cpu_minutes: 10,
  };

  it("on 409 shows the server estimate and relaunches only after confirmation", async () => {
    const calls: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        const body = JSON.parse(String(init!.body));
        calls.push(body);
        return body.confirm
          ? new Response(JSON.stringify({ job_id: "job-7" }), { status: 200 })
          : // the body studio/app.py sends
            new Response(JSON.stringify({ message: "estimated 12.5 CPU-minutes exceeds the 10 CPU-minute cap; resend with confirm=true", estimate_cpu_minutes: 12.5, cap_cpu_minutes: 10 }), { status: 409 });
      }),
    );
    render(<LaunchForm catalog={catalog} />);
    await userEvent.click(screen.getByLabelText("insert_nominal"));
    await userEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("12.50 CPU-min");
    expect(calls).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Confirm and launch anyway" }));
    expect(await screen.findByText("Launched job-7.")).toBeInTheDocument();
    expect(calls).toEqual([
      { scenario_ids: ["insert_nominal"], systems: ["haplous_mediated"], confirm: false },
      { scenario_ids: ["insert_nominal"], systems: ["haplous_mediated"], confirm: true },
    ]);
  });
});
