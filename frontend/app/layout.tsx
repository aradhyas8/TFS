import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Personal Investment Analyst",
  description: "A dated view of your whole portfolio, with transparent exposure calculations.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
