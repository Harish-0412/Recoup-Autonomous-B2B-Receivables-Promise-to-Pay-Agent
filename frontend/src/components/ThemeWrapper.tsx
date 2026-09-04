"use client";

import dynamic from "next/dynamic";
import type { PropsWithChildren } from "react";

// Dynamically import next-themes with SSR disabled to avoid the
// "Encountered a script tag while rendering React component" warning.
// next-themes injects an inline <script> for FOUC prevention which
// React's client renderer rejects — lazy-loading it client-only sidesteps this.
const NextThemesProvider = dynamic(
  () => import("next-themes").then((mod) => mod.ThemeProvider),
  { ssr: false }
);

export default function ThemeWrapper({ children }: PropsWithChildren<{}>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange={false}
    >
      {children}
    </NextThemesProvider>
  );
}
