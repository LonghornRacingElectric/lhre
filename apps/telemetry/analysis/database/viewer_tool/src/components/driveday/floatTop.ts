/** Lowest a floating window may start: just under the fixed top bar (Banner publishes its height as --nav-h). */
export function floatTop(): number {
  const h = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nav-h"));
  return (Number.isFinite(h) ? h : 56) + 4;
}
