import { PrismaClient } from "../../../.prisma/telemetry-client";

declare global {
  var telemetry_prisma: PrismaClient | undefined;
}

if (!global.telemetry_prisma) {
  global.telemetry_prisma = new PrismaClient();
}

const telemetry_prisma: PrismaClient = global.telemetry_prisma!;


export default telemetry_prisma;
