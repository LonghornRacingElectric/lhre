// vitest: tests/driveday (drive day viewer) and tests/trackside (lap time parser, session names). Run: npm run test:driveday
import { fileURLToPath } from "node:url";

export default {
  // "@/" is the app's import root (tsconfig paths), so tests can load modules that use it, such as the API route.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { environment: "node", include: ["tests/**/*.test.ts"] },
};
