// Run from `postinstall`: bring the auth SQLite file in line with auth.prisma
// (there are no migrations, and the committed dev.db predates User.isAdmin),
// then optionally create a login from VIEWER_USERNAME / VIEWER_PASSWORD.
// Never fails the install: any problem is a warning with the manual command.
import { execSync } from 'child_process';
import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../../../../../.env') });

const run = (cmd: string) => execSync(cmd, { stdio: 'inherit', cwd: path.resolve(__dirname, '..') });

if (!process.env.AUTH_DATABASE_URL) {
  console.warn('AUTH_DATABASE_URL not set (copy .env.example to the repo-root .env); skipping auth DB setup.');
} else {
  try {
    run('npx prisma db push --schema=prisma/auth.prisma --skip-generate');
    if (process.env.VIEWER_USERNAME && process.env.VIEWER_PASSWORD) {
      run('npm run prisma-auth-seed');
    }
  } catch {
    console.warn('Auth DB setup failed; rerun with `npm run prisma-auth-setup`.');
  }
}
