import fs from "node:fs";
import path from "node:path";

type Field = { name: string; sql_type: string; aggregate: "avg" | "last" };
type Pool = { table: string; group: string; frequency_hz: number; fields: Field[] };
type Contract = { storage_id: string; pools: Pool[] };
let cached: Contract | undefined;

export function storageContract(): Contract {
  if (!cached) {
    const filename = process.env.ARTEMIS_STORAGE_CONTRACT_PATH || path.resolve(
      process.cwd(), "../../../../../bazel-bin/lib/codegen/telemetry/storage_contract.json",
    );
    cached = JSON.parse(fs.readFileSync(filename, "utf8")) as Contract;
  }
  return cached;
}
