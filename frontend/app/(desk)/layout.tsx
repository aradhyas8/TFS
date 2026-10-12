import type { Metadata } from "next";
import "./desk.css";

export const metadata: Metadata = {
  title: "Analyst",
  description: "Ask anything about investing or your saved portfolio, with the evidence one step away.",
};

export default function DeskLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
