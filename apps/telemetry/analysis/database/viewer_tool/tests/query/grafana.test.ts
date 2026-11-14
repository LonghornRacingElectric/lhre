import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { expect, it, vi } from "vitest";

it("offers scalar signals and changes an incompatible Boolean aggregate to last", () => {
  let plugin: any;
  const signals = [
    { signal: "controls.apps", group: "controls", value_type: "float", aggregates: ["mean", "last", "raw"] },
    { signal: "controls.enable", group: "controls", value_type: "bool", aggregates: ["last", "raw"] },
    { signal: "pack.cells", group: "pack", value_type: "float", repeated: true, aggregates: ["last", "raw"] },
  ];
  class Plugin { editor: any; constructor(public source: any) {} setQueryEditor(editor: any) { this.editor = editor; return this; } setConfigEditor() { return this; } }
  const react = { createElement: (type: any, props: any, ...children: any[]) => ({ type, props, children }), useState: vi.fn().mockReturnValueOnce([signals, vi.fn()]).mockReturnValueOnce(["", vi.fn()]).mockReturnValueOnce(["", vi.fn()]), useEffect: vi.fn() };
  const modules = [{ DataSourcePlugin: Plugin }, { DataSourceWithBackend: class {} }, { InlineField: "field", MultiSelect: "multi", Select: "select", Input: "input", Switch: "switch" }, react, {}, {}];
  vm.runInNewContext(readFileSync(path.resolve("../../../stack/kafka/grafana/module.js"), "utf8"), { System: { register: (_: any, define: any) => { const m = define((_name: string, value: any) => { plugin = value; }); m.setters.forEach((set: any, i: number) => set(modules[i])); m.execute(); } } });
  const onChange = vi.fn(), run = vi.fn();
  const tree = plugin.editor({ datasource: {}, query: { agg: "mean", signals: [] }, onChange, onRunQuery: run });
  const inputs = tree.children.flatMap((node: any) => node?.children || []);
  const select = inputs.find((node: any) => node.type === "multi");
  expect(select.props.options.map((o: any) => o.value)).toEqual(["controls.apps", "controls.enable"]);
  select.props.onChange([{ value: "controls.apps" }, { value: "controls.enable" }]);
  expect(onChange).toHaveBeenCalledWith({ agg: "last", signals: ["controls.apps", "controls.enable"] });
  expect(run).toHaveBeenCalledOnce();
});
