import type { Metadata } from "next";
import { Geist, Inter } from "next/font/google";
import "@/components/arc/foundation.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Personal Investment Analyst",
  description: "A dated view of your whole portfolio, with transparent exposure calculations.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Browser extensions add attributes to <html> before hydration; ignore those (this element only).
  return <html lang="en" data-theme="dark" className={`${geist.variable} ${inter.variable}`} suppressHydrationWarning><body>{children}</body></html>;
}
