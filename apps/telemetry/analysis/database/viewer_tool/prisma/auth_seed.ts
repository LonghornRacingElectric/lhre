import { PrismaClient } from '../.prisma/auth-client';
import { hash } from 'bcrypt';
import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../../../../../.env') });

const prisma = new PrismaClient();

async function main() {
  // CLI args win; VIEWER_USERNAME / VIEWER_PASSWORD / VIEWER_ADMIN in the root
  // .env let auth_setup.ts create a login during `npm install`.
  const username = process.argv[2] ?? process.env.VIEWER_USERNAME;
  const password = process.argv[3] ?? process.env.VIEWER_PASSWORD;

  if (!username || !password) {
    console.error('Please provide both a username and a password.');
    process.exit(1);
  }

  // Pass "admin" as the 3rd positional arg (or VIEWER_ADMIN=1) to flag the user as an admin.
  const isAdmin =
    process.argv[4] === 'admin' || ['1', 'true'].includes(process.env.VIEWER_ADMIN ?? '');

  const hashedPassword = await hash(password, 12);
  // Upsert so re-seeding (password reset / admin promotion) is idempotent.
  const user = await prisma.user.upsert({
    where: { username },
    update: { password: hashedPassword, isAdmin },
    create: { username, name: username, password: hashedPassword, isAdmin },
    select: { id: true, username: true, isAdmin: true },
  });
  console.log({ user });
}

main()
  .then(() => prisma.$disconnect())
  .catch(async (e) => {
    console.error(e);
    await prisma.$disconnect();
    process.exit(1);
  });