/**
 * frontend/src/app/layout.tsx
 * Root layout — sets font, dark mode, and places the toast container.
 */
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ToastContainer } from "@/components/ui/Toast";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "FutureEdge — AI Trading System",
  description: "Multi-agent AI trading for Indian equity markets",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans bg-gray-950 text-gray-100 antialiased`}>
        {children}
        <ToastContainer />
      </body>
    </html>
  );
}