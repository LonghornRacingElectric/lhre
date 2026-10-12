import type { Metadata } from "next";
import { App } from "@/components/App";

export const metadata: Metadata = { title: "LAPDOG viewer" };

export default function Page() {
  return <App />;
}
