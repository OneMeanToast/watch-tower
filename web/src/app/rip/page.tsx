import { Suspense } from "react";

import { RipDesk } from "@/components/rip-desk";

export default function RipPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-black px-4 py-6 font-mono text-sm text-white/60">
          Opening the rip desk…
        </main>
      }
    >
      <RipDesk />
    </Suspense>
  );
}
