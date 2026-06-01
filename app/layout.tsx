import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fieldstatic | Electro Shield Tick Defense Spray",
  description:
    "A science-led anti-static tick defense spray for clothing, outdoor gear, and pet-safe routines.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
