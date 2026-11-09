import { readSignalCatalog } from "../../../lib/signals/catalog";
import { CursorCodec } from "../../../lib/query/cursor";
import { createQueryHandler } from "../../../lib/query/http";
import { ArtemisSource } from "../../../lib/query/artemis";
import { createQueryService } from "../../../lib/query/service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = createQueryHandler(createQueryService(new ArtemisSource(), readSignalCatalog, new CursorCodec()));
