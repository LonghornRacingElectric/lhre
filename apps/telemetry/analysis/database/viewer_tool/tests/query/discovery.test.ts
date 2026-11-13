import { expect, it, vi } from "vitest";
import { GET } from "../../src/app/api/cars/[car]/signals/route";
import { gatewayRequest } from "../../src/lib/query/gateway";

vi.mock("../../src/lib/query/gateway", () => ({ gatewayRequest: vi.fn() }));

it("discovers the deployed Artemis catalog and propagates authentication failures", async () => {
  const context = { params: Promise.resolve({ car: "artemis" }) };
  const version = "a".repeat(64), signals = [{ signal: "controls.apps", unit: "%" }];
  vi.mocked(gatewayRequest).mockResolvedValueOnce(Response.json({ schema_version: version, signals }));
  const result = await GET(new Request("http://localhost/api/cars/artemis/signals"), context);
  expect(result.headers.get("X-Schema-Version")).toBe(version);
  expect(await result.json()).toEqual(signals);
  vi.mocked(gatewayRequest).mockResolvedValueOnce(Response.json({ error: { code: "unauthorized" } }, { status: 401 }));
  expect((await GET(new Request("http://localhost/api/cars/artemis/signals"), context)).status).toBe(401);
});
