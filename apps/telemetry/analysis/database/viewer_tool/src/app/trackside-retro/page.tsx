import type { Metadata } from "next";
import { App } from "@/components/driveday/App";
import "./driveday.css";


export const metadata: Metadata = {
  title: "LAPDOG | Trackside Retro",
  description: "Your driveday, replayed. LAPDOG lap analysis, video and telemetry.",
  icons: { icon: "/lapdog-logo.webp" },
};

// Imported recordings stay in IndexedDB for subsequent reviews.
export default function TracksideRetroPage() {
  return (
    <div className="ddv">
      <App />
    </div>
  );
}
