// Lightweight presence/leadership for multi-client trackside-live.
//
// Every browser runs lap detection on the same stream, so if they all wrote the
// shared session we'd get duplicate laps. Instead one client is the LEADER
// (owns the session: logs laps, sets targets, writes the session-cache) and the
// rest are MIRRORS (read the shared doc). By default the first still-alive client
// by claim time is leader; a mirror can REQUEST control and the leader can GRANT
// it (manual transfer), which pins a designated leader until it leaves. Capped
// at MAX_CLIENTS.
//
// LOCK: the leader can lock leadership to itself so it never hops while their
// phone sleeps — a locked leader keeps its seat while silent, and everyone else
// stays a mirror. If the locked leader is away (silent > TTL), any client can
// explicitly TAKE control; unlock returns to the hop-on-leave behaviour.
//
// State is in-process (single `next start` instance) — fine for a trackside box.
// Clients heartbeat ~every 4s; a client is dropped after TTL_MS of silence.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";

type Client = { id: string; firstSeen: number; lastSeen: number; name: string };
type PresenceState = { clients: Map<string, Client>; requests: Map<string, number>; designated: string | null; locked: boolean };

const TTL_MS = 12_000;
const MAX_CLIENTS = 3;

const g = globalThis as unknown as { __livePresenceV2?: PresenceState };
const state: PresenceState = g.__livePresenceV2 ?? (g.__livePresenceV2 = {
  clients: new Map(), requests: new Map(), designated: null, locked: false,
});
const { clients, requests } = state;

const away = (c: Client | undefined, now: number) => !c || now - c.lastSeen > TTL_MS;

function prune(now: number) {
  for (const [id, c] of clients) if (away(c, now) && !(state.locked && id === state.designated)) clients.delete(id);
  for (const id of requests.keys()) if (!clients.has(id)) requests.delete(id);
  if (state.designated && !clients.has(state.designated)) { state.designated = null; state.locked = false; } // demote if it left
}

function leaderId(): string | null {
  // A live designated leader (from a manual transfer) wins; else earliest claim.
  if (state.designated && clients.has(state.designated)) return state.designated;
  let leader: Client | null = null;
  for (const c of clients.values()) {
    if (!leader || c.firstSeen < leader.firstSeen || (c.firstSeen === leader.firstSeen && c.id < leader.id)) leader = c;
  }
  return leader?.id ?? null;
}

function snapshot(clientId: string, now: number) {
  const leader = leaderId();
  const isLeader = leader === clientId;
  return {
    role: isLeader ? "leader" : "mirror",
    leader,
    clients: clients.size,
    max: MAX_CLIENTS,
    // Pending control requests (id + display name) — for the leader's popup.
    requests: [...requests.keys()].map((id) => ({ id, name: clients.get(id)?.name || '' })),
    // Whether THIS client has an outstanding request.
    requested: requests.has(clientId),
    locked: state.locked,
    leaderName: (leader && clients.get(leader)?.name) || '',
    // Locked leader silent > TTL — mirrors may take control.
    leaderAway: state.locked && away(leader ? clients.get(leader) : undefined, now),
  };
}

export async function POST(req: NextRequest) {
  const now = Date.now();
  const body = (await req.json().catch(() => ({}))) as {
    clientId?: string; name?: string; leave?: boolean;
    action?: "request" | "grant" | "deny" | "force" | "claim_starter" | "lock" | "unlock" | "take";
    target?: string;
  };
  const clientId = typeof body.clientId === "string" ? body.clientId : "";
  if (!clientId) return NextResponse.json({ error: "clientId required" }, { status: 400 });
  const name = (typeof body.name === "string" ? body.name : "").slice(0, 40);

  prune(now);

  if (body.leave) {
    requests.delete(clientId);
    if (state.locked && state.designated === clientId) {
      const c = clients.get(clientId);
      if (c) c.lastSeen = 0; // keep the locked seat, but show it as away now
    } else {
      clients.delete(clientId);
      if (state.designated === clientId) state.designated = null;
    }
    return NextResponse.json({ role: "left", clients: clients.size, max: MAX_CLIENTS, leader: leaderId() });
  }

  const existing = clients.get(clientId);
  if (existing) {
    existing.lastSeen = now;
    if (name) existing.name = name;
  } else if (clients.size < MAX_CLIENTS) {
    clients.set(clientId, { id: clientId, firstSeen: now, lastSeen: now, name });
  } else {
    return NextResponse.json({ role: "full", clients: clients.size, max: MAX_CLIENTS, leader: leaderId(), requests: [], requested: false });
  }

  const leader = leaderId();
  const leaderAway = state.locked && away(leader ? clients.get(leader) : undefined, now);
  if (body.action === "force") {
    // Admin override: seize leadership instantly, even over an active leader.
    // Verified server-side via the next-auth session token — a non-admin POST
    // (even hand-crafted) is ignored.
    const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET }).catch(() => null);
    if (token?.isAdmin) {
      state.designated = clientId;
      requests.delete(clientId);
    }
  } else if (body.action === "request") {
    // A non-leader asks for control.
    if (clientId !== leader) requests.set(clientId, now);
  } else if (body.action === "grant" && typeof body.target === "string") {
    // Only the current leader may transfer; pin the target as designated leader.
    if (clientId === leader && clients.has(body.target)) {
      state.designated = body.target;
      requests.delete(body.target);
    }
  } else if (body.action === "deny" && typeof body.target === "string") {
    if (clientId === leader) requests.delete(body.target);
  } else if (body.action === "lock") {
    if (clientId === leader) { state.designated = clientId; state.locked = true; }
  } else if (body.action === "unlock") {
    if (clientId === leader) state.locked = false;
  } else if (body.action === "take") {
    // Escape hatch for a locked leader whose phone died; the lock carries over.
    if (leaderAway) { state.designated = clientId; requests.delete(clientId); }
  } else if (body.action === "claim_starter" && (!state.locked || leaderAway)) {
    // The user who originally started the active session reclaims control on
    // rejoin. The frontend gates this with sessionInfo.starterId ===
    // clientId, so only that user's browser will fire it. We trust the action
    // at trackside scale (no banking-grade auth needed here).
    state.designated = clientId;
    requests.delete(clientId);
  }

  return NextResponse.json(snapshot(clientId, now));
}
