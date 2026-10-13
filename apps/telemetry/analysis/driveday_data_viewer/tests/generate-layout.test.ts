import { it } from "vitest";
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { convertSession } from "../src/lib/convert";
import { trackEdges } from "../src/lib/trackEdges";
it.skipIf(!process.env.DRIVEDAY_LAYOUT)("generate the test layout only from Layout_session.json", () => {
  const j = JSON.parse(readFileSync(process.env.DRIVEDAY_LAYOUT!, "utf8"));
  const results = convertSession(j, "Layout_session.json", []);

  const tracks = results.map(({track}, i) => {
    const { center, ...edges } = trackEdges(track.ref, track.open);
    const cum = [0];
    for(let k=1;k<center.length;k++) cum.push(cum[k-1]+Math.hypot(center[k][0]-center[k-1][0],center[k][1]-center[k-1][1]));
    return {...track, id: `layout-pickle-test-${i+1}`, name: "Pickle · Layout test" + (results.length > 1 ? ` · ${i+1}` : ""), ref:center, cum, RL:cum.at(-1), edges, created: j.savedAt, from: "Layout_session.json"};
  });
  writeFileSync("src/lib/test-layout.json", JSON.stringify(tracks, null, 2)+"\n");
});

it.skipIf(!process.env.DRIVEDAY_LOGS)("checks real log assignment without changing exports", () => {
  const folder = process.env.DRIVEDAY_LOGS!;
  const tracks = JSON.parse(readFileSync("src/lib/test-layout.json", "utf8"));
  for (const name of readdirSync(folder).filter(n => n.endsWith(".json"))) {
    try {
      const result = convertSession(JSON.parse(readFileSync(folder + "/" + name, "utf8")), name, tracks);
      for (const r of result) if (r.isNew) tracks.push(r.track);
      console.log(name, result.map(r => ({ layout:r.track.name, base:!!r.track.edges, open:!!r.track.open })));
    } catch (e) { console.log(name, String(e)); }
  }
});
