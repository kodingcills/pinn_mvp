import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { mockApi, resetMock } from "../../fixtures/mock.ts";

beforeEach(() => {
  resetMock();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/rrd")) return new Response(new Uint8Array([0x52, 0x52, 0x46, 0x32]), { status: 200 }); // stand-in bytes for the mocked viewer
      const out = mockApi(init?.method ?? "GET", url, init?.body ? JSON.parse(String(init.body)) : undefined);
      return new Response(JSON.stringify(out.body), { status: out.status, headers: { "Content-Type": "application/json" } });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
