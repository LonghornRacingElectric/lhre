import { PrismaClient } from "../../../.prisma/artemis-client";
import { getSettings } from "@/lib/motec/config";

declare global {
  var artemis_prisma: PrismaClient | undefined;
}

if (!global.artemis_prisma) {
  const settings = getSettings("artemis");
  const url = new URL(`postgresql://${settings.dbHost}:${settings.dbPort}/${settings.dbName}`);
  url.username = settings.dbUser;
  url.password = settings.dbPassword;
  url.searchParams.set("sslmode", settings.dbSslmode);
  global.artemis_prisma = new PrismaClient({
    datasources: { db: { url: process.env.ARTEMIS_DATABASE_URL || url.toString() } },
  });
}

const artemisPrisma: PrismaClient = global.artemis_prisma!;
export default artemisPrisma;
