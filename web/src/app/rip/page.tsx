import { Suspense } from "react";

import { RipDesk } from "@/components/rip-desk";

export default function RipPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-background px-8 py-10 text-sm text-muted-foreground">
          Opening the page…
        </main>
      }
    >
      <RipDesk />
    </Suspense>
  );
}
