"use client";

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { PortalContainer } from "@/components/ui/portal-container";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * The `.ddv` wrapper every LAPDOG screen sits in. It carries the LAPDOG theme tokens, so popovers, selects and tooltips
 * are portalled back into it and read the same tokens instead of the rest of the site's.
 */
export function DdvRoot({ className, children }: { className?: string; children: ReactNode }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  return (
    <div ref={setEl} className={cn("ddv", className)}>
      <PortalContainer.Provider value={el}>
        <TooltipProvider delayDuration={350}>{children}</TooltipProvider>
      </PortalContainer.Provider>
    </div>
  );
}
