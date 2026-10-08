import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Personal Investment Analyst",
  description: "A dated view of your whole portfolio, with transparent exposure calculations.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Browser extensions add attributes to <html> before hydration; ignore those (this element only).
  return <html lang="en" suppressHydrationWarning><body>{children}</body></html>;
}
