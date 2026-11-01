import type { Metadata } from "next";
import { Barlow_Condensed, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { App } from "@/components/driveday/App";
import "./driveday.css";

const disp = Barlow_Condensed({ subsets: ["latin"], weight: ["500", "600", "700"], variable: "--font-disp", display: "swap" });
const body = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-body", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "LAPDOG | Trackside Retro",
  description: "Your driveday, replayed. LAPDOG lap analysis, video and telemetry.",
  icons: { icon: "/lapdog-logo.webp" },
};

// Drive day viewer (analysis/driveday_data_viewer). Drive days load from the orion
// database (/api/driveday/db) and are kept in IndexedDB.
export default function TracksideRetroPage() {
  return (
    <div className={`ddv ${disp.variable} ${body.variable} ${mono.variable}`}>
      <App />
    </div>
  );
}
