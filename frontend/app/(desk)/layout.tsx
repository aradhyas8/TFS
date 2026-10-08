import type { Metadata } from "next";
import { Instrument_Sans, Newsreader } from "next/font/google";
import "./desk.css";

const sans = Instrument_Sans({ subsets: ["latin"], variable: "--font-sans" });
const serif = Newsreader({ subsets: ["latin"], variable: "--font-serif", weight: ["300", "400"], style: ["normal", "italic"] });

export const metadata: Metadata = {
  title: "Analyst",
  description: "Ask about your saved portfolio, with the evidence one step away.",
};

export default function DeskLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <div className={`${sans.variable} ${serif.variable}`}>{children}</div>;
}
