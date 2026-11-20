"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** A button that opens a small popover. Closes on an outside click or Escape. */
export function Menu({ label, children, wide = false, right = false, chip = false, onToggle, ariaLabel }: { label: ReactNode; children: ReactNode; wide?: boolean; right?: boolean; chip?: boolean; ariaLabel?: string; onToggle?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const change = (o: boolean) => {
    setOpen(o);
    onToggle?.(o);
  };
  return (
    <Popover open={open} onOpenChange={change}>
      <PopoverTrigger asChild>
        <Button variant="tool" size="ctl" className="menu-trigger" aria-label={ariaLabel}>
          {label}
          <ChevronDown className="size-3.5 opacity-60" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align={right ? "end" : "start"}
        collisionPadding={8}
        className={cn("flex max-h-(--radix-popover-content-available-height) w-auto min-w-56 flex-col gap-2.5 overflow-y-auto p-3", wide && "w-[min(380px,84vw)] flex-row flex-wrap")}
        onClick={(e) => {
          /* picking a chip inside a wide menu closes it */
          if (wide && (e.target as HTMLElement).closest("button")) change(false);
        }}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
