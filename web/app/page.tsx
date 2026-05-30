import { redirect } from "next/navigation";

// Phase 1 has no real "home" — drop the user straight into /log.
export default function Home() {
  redirect("/log");
}
