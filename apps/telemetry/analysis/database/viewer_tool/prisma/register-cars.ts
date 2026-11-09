import prisma from '../src/lib/prisma/telemetry';
import * as dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../../../../../.env') });

async function main() {
  await prisma.$transaction(async db => {
    for (const [car_id, car_name] of [[5, 'Orion'], [6, 'Artemis']] as const) {
      const existing = await db.lut_car.findUnique({ where: { car_id } });
      if (existing && existing.car_name !== car_name) {
        throw new Error(`Car ID ${car_id} already belongs to ${existing.car_name}`);
      }
      await db.lut_car.upsert({ where: { car_id }, create: { car_id, car_name }, update: {} });
    }
  });
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
}).finally(() => prisma.$disconnect());
