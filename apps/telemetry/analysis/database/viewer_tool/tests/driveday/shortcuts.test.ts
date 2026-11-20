import { describe, expect, it } from "vitest";
import { shortcutIgnored, type KeyTarget } from "../../src/lib/driveday/shortcuts";

/* A stand-in for a DOM element: `inside` lists the selectors it or an ancestor matches. */
function el(tagName: string, inside: string[] = [], type?: string): KeyTarget {
  return { tagName, type, closest: (selector) => selector.split(",").some((s) => inside.includes(s.trim())) || null };
}

describe("viewer shortcut guard", () => {
  it("lets shortcuts through from the page itself", () => {
    expect(shortcutIgnored(null, "c")).toBe(false);
    expect(shortcutIgnored(el("DIV"), "c")).toBe(false);
    expect(shortcutIgnored(el("CANVAS"), " ")).toBe(false);
  });

  it("leaves letters typed in an open Radix select to its typeahead", () => {
    for (const key of ["c", "d", "f", "r", " "]) {
      expect(shortcutIgnored(el("DIV", ['[role="option"]', '[role="listbox"]']), key)).toBe(true);
    }
  });

  it("leaves a focused select trigger and popover content alone", () => {
    expect(shortcutIgnored(el("BUTTON", ['[role="combobox"]']), "c")).toBe(true);
    expect(shortcutIgnored(el("BUTTON", ["[data-radix-popper-content-wrapper]"]), "d")).toBe(true);
    expect(shortcutIgnored(el("DIV", ["[data-radix-popper-content-wrapper]"]), "r")).toBe(true);
  });

  it("keeps letter shortcuts on an ordinary button, but not Space or Enter", () => {
    expect(shortcutIgnored(el("BUTTON"), "c")).toBe(false);
    expect(shortcutIgnored(el("BUTTON"), " ")).toBe(true);
    expect(shortcutIgnored(el("BUTTON"), "Enter")).toBe(true);
    expect(shortcutIgnored(el("SUMMARY"), " ")).toBe(true);
  });

  it("leaves text fields alone but not range sliders or checkboxes", () => {
    expect(shortcutIgnored(el("INPUT", [], "text"), "c")).toBe(true);
    expect(shortcutIgnored(el("TEXTAREA"), "n")).toBe(true);
    expect(shortcutIgnored(el("SELECT"), "f")).toBe(true);
    expect(shortcutIgnored(el("INPUT", [], "range"), "ArrowLeft")).toBe(false);
    expect(shortcutIgnored(el("INPUT", [], "checkbox"), "c")).toBe(false);
  });

  it("keeps the areas that own their keys", () => {
    for (const area of [".video-panel", ".pane-header", ".layout-toolbar", ".pane-divider", ".pane-resize", ".timeline-resize"]) {
      expect(shortcutIgnored(el("DIV", [area]), "c")).toBe(true);
    }
  });

  it("gives the pedal trace its arrow and Home/End keys only", () => {
    const pedal = el("svg", [".driver-pedal-svg"]);
    for (const key of ["ArrowLeft", "ArrowRight", "Home", "End"]) expect(shortcutIgnored(pedal, key)).toBe(true);
    expect(shortcutIgnored(pedal, "c")).toBe(false);
  });
});
