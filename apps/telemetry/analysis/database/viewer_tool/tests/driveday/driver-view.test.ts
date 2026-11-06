import { describe, expect, it } from "vitest";
import { activeDriverSector, driverPedalsAt, pedalPath } from "../../src/lib/driveday/driver-view";
import type { Lap } from "../../src/lib/driveday/types";
const lap = (times: number[], distances: number[], throttle: number[], brake: number[]) => ({kind:"lap",sec:[1000,2000,3000],dur:6000,ch:{t:times,u:distances,thr:throttle,brkp:brake}} as Lap);
describe("driver comparison",()=>{
  it("compares the same track distance when lap timing differs",()=>{
    const current=lap([0,1000,2000],[0,50,100],[0,80,20],[0,10,90]);
    const best=lap([0,500,1000],[0,50,100],[10,90,30],[0,20,80]);
    expect(driverPedalsAt(current,50)).toEqual({throttle:80,brake:10});
    expect(driverPedalsAt(best,50)).toEqual({throttle:90,brake:20});
  });
  it("keeps a one-sample brake peak in a dense pixel bucket",()=>{
    const source=lap([0,20,40,60,80],[0,.01,.02,.03,100],[0,0,0,0,0],[0,0,100,0,0]);
    const before=[...source.ch.brkp];
    expect(pedalPath(source,"brkp",100)).toContain("0.13,0.00");
    expect(source.ch.brkp).toEqual(before);
  });
  it("leaves a dropout visibly disconnected",()=>{
    const source=lap([0,100,3000,3100],[0,10,90,100],[0,50,100,0],[0,0,0,0]);
    expect(pedalPath(source,"thr",100).match(/M/g)).toHaveLength(2);
  });
  it("changes the highlighted sector at its exact split",()=>{
    const source=lap([0,6000],[0,100],[0,0],[0,0]);
    expect(activeDriverSector(source,999)).toBe(0);
    expect(activeDriverSector(source,1000)).toBe(1);
    expect(activeDriverSector(source,999.9997)).toBe(1);
    expect(activeDriverSector(source,3000)).toBe(2);
    expect(activeDriverSector({...source,kind:"out"},500)).toBeNull();
    expect(activeDriverSector({...source,kind:"partial",sec:[1000,null,null]},1500)).toBe(1);
  });
});
