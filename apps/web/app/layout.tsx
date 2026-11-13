import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Koras | Opportunity Intelligence",
  description: "Private consulting opportunity intelligence. Synthetic local P1 workspace.",
  robots: { index: false, follow: false }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
