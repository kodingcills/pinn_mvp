// Release-1 definition of done (docs/eval_program/designs/M6_haplous_studio.md), driven in headless
// Chromium against the real server: `python -m haplous.cli studio --root <temp dir>`.
// Runs exactly one simulation job: 1 scenario x 2 systems (2 short episodes).
//
//   cd studio/web && PYTHON=../../.venv/bin/python npm run e2e
//
// Env: PYTHON (interpreter with the studio extra; default <repo>/.venv/bin/python),
// STUDIO_E2E_SHOTS (screenshot dir; default <tmp>/studio-e2e). Playwright is resolved from
// NODE_PATH (a global install); this script does not download browsers.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const REPO = path.resolve(__dirname, "../../..");
const PYTHON = process.env.PYTHON ?? path.join(REPO, ".venv/bin/python");
const SHOTS = process.env.STUDIO_E2E_SHOTS ?? path.join(os.tmpdir(), "studio-e2e");
const SCENARIO = "insert_dropout_large";
const SYSTEMS = ["haplous_mediated", "g1_incumbent"];
const EVENT = "temporal_hold_entered";
const PROGRAM_STATE = ["TASKS.json", "PROGRAM_STATE.md", "KNOWN_ISSUES.md", "DECISIONS.tsv"].map((f) => path.join(REPO, "docs/eval_program", f));
const REPO_INVESTIGATIONS = path.join(REPO, "docs/eval_program/investigations");

const log = (...a) => console.log(`[e2e ${new Date().toISOString().slice(11, 19)}]`, ...a);
const sha = (f) => (fs.existsSync(f) ? crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex") : null);
const listing = (d) => (fs.existsSync(d) ? fs.readdirSync(d).sort() : []);

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer().listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
    s.on("error", reject);
  });
}

async function waitFor(fn, what, timeoutMs, everyMs = 500) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {}
    if (Date.now() > end) throw new Error(`timed out after ${timeoutMs / 1000}s waiting for ${what}`);
    await new Promise((r) => setTimeout(r, everyMs));
  }
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "studio-e2e-root-"));
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const stateBefore = PROGRAM_STATE.map(sha);
  const investigationsBefore = listing(REPO_INVESTIGATIONS);
  log(`studio root ${root}, port ${port}, screenshots ${SHOTS}`);

  const server = spawn(PYTHON, ["-m", "haplous.cli", "studio", "--port", String(port), "--no-browser", "--root", root], {
    cwd: REPO,
    env: { ...process.env, PYTHONPATH: REPO, MUJOCO_GL: process.env.MUJOCO_GL ?? "osmesa" },
    stdio: ["ignore", "inherit", "inherit"],
  });
  const exited = new Promise((r) => server.on("exit", r));
  let browser;
  try {
    await waitFor(async () => (await fetch(`${base}/api/catalog`)).ok, "the server", 60_000);
    const api = async (p) => (await fetch(base + p)).json();
    const catalog = await api("/api/catalog");
    browser = await chromium.launch();
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })).newPage();
    page.on("pageerror", (e) => log("page error:", e.message));
    const shot = async (name) => {
      const file = path.join(SHOTS, `${name}.png`);
      await page.screenshot({ path: file, fullPage: true });
      log("screenshot", file);
    };

    // ---- compute rule: over the cap needs confirmation; cancel, nothing launches
    await page.goto(`${base}/launch`);
    for (const s of catalog.scenarios) await page.getByLabel(s.id, { exact: true }).check();
    for (const s of catalog.systems) await page.getByLabel(s.id, { exact: true }).check();
    await page.getByRole("button", { name: "Launch", exact: true }).click();
    const alert = page.getByRole("alert");
    await alert.waitFor();
    const perScenario = catalog.systems.reduce((a, s) => a + s.cpu_s_per_episode, 0);
    const expectedMin = (catalog.scenarios.length * perScenario) / 60;
    assert.ok(expectedMin > catalog.cap_cpu_minutes, `full catalog estimate ${expectedMin} must exceed the cap`);
    assert.match(await alert.textContent(), new RegExp(`server estimate ${expectedMin.toFixed(2)} CPU-min exceeds ${catalog.cap_cpu_minutes} CPU-min`));
    await shot("0-over-cap-confirm");
    await alert.getByRole("button", { name: "Cancel" }).click();
    await alert.waitFor({ state: "detached" });
    assert.deepEqual(await api("/api/jobs"), [], "cancel at the confirm step launches nothing");
    log(`over-cap: ${catalog.scenarios.length}x${catalog.systems.length} estimated ${expectedMin.toFixed(2)} CPU-min > cap ${catalog.cap_cpu_minutes}; cancelled, 0 jobs`);

    // ---- 1. launch 1 scenario x 2 systems and watch it complete
    await page.goto(`${base}/launch`);
    await page.getByLabel(SCENARIO, { exact: true }).check();
    for (const s of SYSTEMS) await page.getByLabel(s, { exact: true }).check();
    const perEp = catalog.systems.filter((s) => SYSTEMS.includes(s.id)).reduce((a, s) => a + s.cpu_s_per_episode, 0);
    await page.getByText(`Estimate: ${(perEp / 60).toFixed(2)} CPU-min for 2 episodes`).waitFor();
    await shot("1a-launch-estimate");
    await page.getByRole("button", { name: "Launch", exact: true }).click();
    const launched = await page.getByText(/^Launched /).textContent();
    const jobId = launched.replace(/^Launched |\.$/g, "");
    log(`launched job ${jobId}`);
    const row = page.getByRole("row").filter({ hasText: jobId });
    await row.getByText("succeeded").waitFor({ timeout: 600_000 });
    const job = await api(`/api/jobs/${jobId}`);
    assert.equal(job.status, "succeeded");
    assert.deepEqual(job.episodes.map((e) => [e.system, e.state, e.outcome]), SYSTEMS.map((s) => [s, "VERIFIED", "completed"]));
    await shot("1b-launch-succeeded");
    log("job episodes", JSON.stringify(job.episodes.map(({ run_id, state, cpu_s }) => ({ run_id, state, cpu_s }))));

    // ---- 2. replay the Haplous run: event click moves every chart and the 3D viewer
    const runId = job.run_ids.find((r) => r.endsWith("-haplous_mediated"));
    await row.getByRole("link", { name: runId }).click();
    await page.waitForURL(`**/runs/${encodeURIComponent(runId)}`);
    const sliders = page.getByRole("slider");
    await waitFor(async () => (await sliders.count()) === 3, "three charts", 30_000);
    const detail = await api(`/api/runs/${encodeURIComponent(runId)}`);
    const event = detail.events.find((e) => e.kind === EVENT);
    assert.ok(event, `${runId} logs ${EVENT}`);
    await page.getByRole("button", { name: new RegExp(EVENT) }).first().click();
    const cursors = await page.getByTestId("cursor").evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-t"))));
    assert.deepEqual(cursors, [event.t_s, event.t_s, event.t_s], "every chart cursor at the event's t_s");
    log(`clicked ${EVENT} at t=${event.t_s}; chart cursors ${JSON.stringify(cursors)}`);
    const probe = await fetch(`${base}/rerun-web-viewer/re_viewer_bg.wasm`);
    await probe.body?.cancel();
    const wasm = probe.ok;
    if (wasm) {
      const want = String(Math.round(event.t_s * 1e9));
      const got = await waitFor(() => page.locator(".rerun").getAttribute("data-viewer-time-ns"), "the Rerun viewer seek", 300_000);
      assert.equal(got, want, "Rerun viewer's own time cursor equals the event time");
      await page.waitForTimeout(3000); // let the canvas draw the frame at that time
      log(`Rerun viewer current time ${got} ns`);
    } else log("Rerun wasm not available: 3D seek not checked");
    await shot("2-replay-event-seek");

    // ---- 4. create an investigation from the replay
    await page.getByRole("button", { name: "Create investigation" }).click();
    const dialog = page.getByRole("dialog", { name: "Create investigation" });
    await dialog.getByLabel("Title").fill("E2E hold during dropout");
    await dialog.getByLabel("Note").fill("Written by studio/web/e2e/studio.e2e.cjs.");
    await dialog.getByRole("button", { name: "Write investigation" }).click();
    const written = await dialog.locator(".note.ok code").textContent();
    await shot("4-investigation-written");
    assert.equal(path.dirname(written), path.join(root, "investigations"), "written into the temp root");
    const text = fs.readFileSync(written, "utf8");
    assert.ok(text.includes(`\`${runId}\``), "names the run id");
    assert.ok(text.includes(`--run-id ${runId}-repro --scenario ${SCENARIO} --system haplous_mediated`), "carries the reproduction command");
    assert.ok(text.includes(`t = ${event.t_s.toFixed(4)} s`), "focus time is the event time");
    assert.deepEqual(PROGRAM_STATE.map(sha), stateBefore, "program state files untouched");
    assert.deepEqual(listing(REPO_INVESTIGATIONS), investigationsBefore, "nothing written into the repo's investigations dir");
    log(`investigation ${written}`);

    // ---- 3. compare on the M4 campaign: matrix and CI equal official_metrics.json
    const official = JSON.parse(fs.readFileSync(path.join(REPO, "results/m4/campaign/official_metrics.json"), "utf8"));
    const pair = official.populations.control.paired.haplous_mediated_vs_bare_behavior;
    const d = pair.delta_bounded_success;
    await page.goto(`${base}/compare`);
    await page.getByLabel("campaign").selectOption("m4-campaign");
    await page.getByLabel("population").selectOption("control");
    await page.getByLabel("arm A").selectOption("haplous_mediated");
    await page.getByLabel("arm B").selectOption("bare_behavior");
    await page.getByText(String(d.delta), { exact: true }).waitFor();
    const text_ = (sel) => page.locator(sel).textContent();
    assert.deepEqual(
      [await text_('[data-cell="both"]'), await text_('[data-cell="a_only"]'), await text_('[data-cell="b_only"]'), await text_('[data-cell="neither"]')],
      [pair.both, pair.rescued.length, pair.harmed.length, pair.neither].map(String),
    );
    assert.equal(await text_('[data-stat="n"]'), String(d.n_pairs));
    assert.equal(await text_('[data-stat="diff"]'), String(d.delta));
    assert.equal(await text_('[data-stat="ci95"]'), `[${d.ci_low}, ${d.ci_high}]`);
    await shot("3-compare-control-haplous-vs-bare");
    log(`compare: matrix ${pair.both}/${pair.rescued.length}/${pair.harmed.length}/${pair.neither}, n ${d.n_pairs}, diff ${d.delta}, CI [${d.ci_low}, ${d.ci_high}]`);

    // ---- 5. evidence: LIBERO-Dynamic shows not executed
    await page.goto(`${base}/evidence`);
    const libero = page.getByRole("listitem").filter({ hasText: "LIBERO-Dynamic" });
    assert.equal(await libero.locator(".badge").textContent(), "not executed");
    await shot("5-evidence");
    log("evidence: LIBERO-Dynamic not executed");
    log("PASS");
  } finally {
    await browser?.close();
    server.kill("SIGINT");
    await Promise.race([exited, new Promise((r) => setTimeout(r, 5000))]);
    if (server.exitCode === null) server.kill("SIGKILL");
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
