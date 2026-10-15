import type { Metadata } from "next";
import { Hanken_Grotesk, Newsreader } from "next/font/google";
import "@/components/arc/foundation.css";
import "./theme.css";

const hanken = Hanken_Grotesk({ subsets: ["latin"], variable: "--font-hanken" });
const newsreader = Newsreader({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-newsreader" });

export const metadata: Metadata = {
  title: "Personal Investment Analyst",
  description: "A dated view of your whole portfolio, with transparent exposure calculations.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Browser extensions add attributes to <html> before hydration; ignore those (this element only).
  return <html lang="en" data-theme="light" className={`${hanken.variable} ${newsreader.variable}`} suppressHydrationWarning><body>{children}</body></html>;
}
