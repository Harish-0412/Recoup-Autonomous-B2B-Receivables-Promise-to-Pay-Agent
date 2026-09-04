import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import ThemeWrapper from "@/components/ThemeWrapper";
import QueryProvider from "@/components/QueryProvider";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Recoup - Autonomous Receivables Agent",
  description: "AI agent that decides who to chase for overdue B2B invoices.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ThemeWrapper>
          <QueryProvider>
            {children}
          </QueryProvider>
        </ThemeWrapper>
      </body>
    </html>
  );
}
