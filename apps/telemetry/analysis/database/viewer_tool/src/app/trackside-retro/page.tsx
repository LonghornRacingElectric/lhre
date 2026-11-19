import type { Metadata } from "next";
import { App } from "@/components/driveday/App";
import { DdvRoot } from "@/components/driveday/DdvRoot";
import "./driveday.css";
import "./lapdog-skin.css";


export const metadata: Metadata = {
  title: "LAPDOG | Trackside Retro",
  description: "Your driveday, replayed. LAPDOG lap analysis, video and telemetry.",
  icons: { icon: "/lapdog-logo.webp" },
};

// Drive day viewer (analysis/driveday_data_viewer). Drive days load from the orion
// database (/api/driveday/db) and are kept in IndexedDB.
export default function TracksideRetroPage() {
  return (
    <DdvRoot className="">
      <App />
    </DdvRoot>
  );
}
