"use client";

import type { ReactNode } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/* Small LAPDOG-sized wrappers over the shared shadcn/ui components, so the screens read the same everywhere. */

export interface Choice<V extends string> {
  value: V;
  label: ReactNode;
  title?: string;
  disabled?: boolean;
}

/** A dropdown. Radix selects cannot hold an empty-string value, so use a real value for "none". */
export function Pick<V extends string>({ value, onChange, options, ariaLabel, className, disabled }: {
  value: V;
  onChange: (v: V) => void;
  options: Choice<V>[];
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as V)} disabled={disabled}>
      <SelectTrigger size="sm" aria-label={ariaLabel} className={cn("h-8 w-auto min-w-0 text-xs font-normal tracking-normal text-foreground normal-case", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value} disabled={o.disabled} title={o.title}>{o.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** One choice out of a few. `allowNone` lets a click on the active one turn it off (value is then ""). */
export function Seg<V extends string>({ value, onChange, options, ariaLabel, allowNone = false, className }: {
  value: V | "";
  onChange: (v: V | "") => void;
  options: Choice<V>[];
  ariaLabel: string;
  allowNone?: boolean;
  className?: string;
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={value}
      aria-label={ariaLabel}
      className={className}
      onValueChange={(v) => {
        if (v || allowNone) onChange(v as V | "");
      }}
    >
      {options.map((o) => (
        <ToggleGroupItem key={o.value} value={o.value} title={o.title} disabled={o.disabled} className="px-2.5 text-xs">{o.label}</ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

/** A checkbox with its label, for settings menus. */
export function Chk({ checked, onChange, children, title }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode; title?: string }) {
  return (
    <Label className="chk gap-2 text-xs font-normal" title={title}>
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      {children}
    </Label>
  );
}
