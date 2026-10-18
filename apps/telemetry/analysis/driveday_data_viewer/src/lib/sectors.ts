/** Fixed distance gates shared by import and playback; missing crossings stay unavailable. */
export function sectorsOf(u: number[], tt: number[], RL: number, dur: number, complete: boolean, gates = [RL / 3, 2 * RL / 3]): (number | null)[] {
  const cross = (th: number): number | null => {
    if (u[0] === th && tt[0] >= 0) return tt[0];
    for (let k = 1; k < u.length; k++) {
      if (u[k - 1] < th && u[k] >= th) {
        const t = tt[k - 1] + (tt[k] - tt[k - 1]) * (th - u[k - 1]) / (u[k] - u[k - 1]);
        return t >= 0 && t <= dur ? t : null;
      }
    }
    return null;
  };
  const c1 = cross(gates[0]);
  const c2 = cross(gates[1]);
  return [c1, c1 != null && c2 != null && c2 >= c1 ? c2 - c1 : null, complete && c2 != null ? dur - c2 : null];
}
