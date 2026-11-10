import { PrismaClient } from "../../../.prisma/orion-client";
import { getSettings } from "@/lib/motec/config";
declare global { var orion_prisma: PrismaClient | undefined; }
if (!global.orion_prisma) {
  const settings = getSettings("orion");
  const url = new URL(`postgresql://${settings.dbHost}:${settings.dbPort}/${settings.dbName}`);
  url.username = settings.dbUser; url.password = settings.dbPassword;
  url.searchParams.set("sslmode", settings.dbSslmode);
  global.orion_prisma = new PrismaClient({ datasources: { db: { url: process.env.ORION_DATABASE_URL || url.toString() } } });
}
const client: PrismaClient = global.orion_prisma!;
export default client;
