/** The part of a key event's target the shortcut guard reads. A DOM Element fits; tests pass a stub. */
export interface KeyTarget {
  tagName: string;
  type?: string;
  closest(selector: string): unknown;
}

/* Open Radix selects and popovers (and a focused select trigger, which does typeahead) own the keyboard. Their
   handlers don't stop propagation, so without this a letter typed to jump to an option also fires a shortcut. */
const RADIX_KEYBOARD = '[role="combobox"], [role="listbox"], [role="option"], [data-radix-popper-content-wrapper]';
const OWN_KEYS = ".video-panel, .pane-header, .layout-toolbar, .pane-divider, .pane-resize, .timeline-resize";

/** True when a key pressed on `t` belongs to the control under focus, so the viewer's shortcuts leave it alone. */
export function shortcutIgnored(t: KeyTarget | null, key: string): boolean {
  if (!t) return false;
  if (t.closest(".driver-pedal-svg") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return true;
  if (t.closest(OWN_KEYS) || t.closest(RADIX_KEYBOARD)) return true;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && t.type !== "range" && t.type !== "checkbox") return true;
  return (t.tagName === "BUTTON" || t.tagName === "SUMMARY") && (key === " " || key === "Enter");
}
