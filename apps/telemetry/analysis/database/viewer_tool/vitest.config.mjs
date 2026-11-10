// vitest: tests/driveday (drive day viewer) and tests/trackside (lap time parser, session names). Run: npm run test:driveday
import { fileURLToPath } from "node:url";

export default {
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { environment: "node", include: ["tests/**/*.test.ts"] },
};
