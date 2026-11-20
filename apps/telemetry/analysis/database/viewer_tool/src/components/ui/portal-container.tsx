"use client"

import * as React from "react"

/**
 * Radix portals render into document.body by default. A page with its own theme (LAPDOG, under `.ddv`) sets this to
 * its wrapper element, so popovers, selects and tooltips inherit that theme's tokens instead of the site's.
 */
export const PortalContainer = React.createContext<HTMLElement | null>(null)

export function usePortalContainer(): HTMLElement | undefined {
  return React.useContext(PortalContainer) ?? undefined
}
