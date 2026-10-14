// vitest: tests/driveday (drive day viewer) and tests/trackside (lap time parser, session names). Run: npm run test:driveday
export default {
  test: { environment: "node", include: ["tests/**/*.test.ts"] },
};
