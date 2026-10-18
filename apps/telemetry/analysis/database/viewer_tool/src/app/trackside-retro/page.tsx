import type { Metadata } from "next";
import { Barlow_Condensed, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { App } from "@/components/driveday/App";
import "./driveday.css";

const disp = Barlow_Condensed({ subsets: ["latin"], weight: ["500", "600", "700"], variable: "--font-disp", display: "swap" });
const body = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-body", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Trackside Retro",
  description: "Lap, sector, corner and battery analysis for an exported Trackside session.",
};

// Drive day viewer (analysis/driveday_data_viewer). Runs are imported in the browser
// and kept in IndexedDB; nothing here talks to the database.
export default function TracksideRetroPage() {
  return (
    <div className={`ddv ${disp.variable} ${body.variable} ${mono.variable}`}>
      <App />
    </div>
  );
}
