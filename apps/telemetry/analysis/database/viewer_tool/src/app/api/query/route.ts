import { readSignalCatalog } from "../../../lib/signals/catalog";
import { CursorCodec } from "../../../lib/query/cursor";
import { createQueryHandler } from "../../../lib/query/http";
import { PostgresSource } from "../../../lib/query/postgres";
import { createQueryService } from "../../../lib/query/service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = createQueryHandler(createQueryService(new PostgresSource(), readSignalCatalog, new CursorCodec()));
