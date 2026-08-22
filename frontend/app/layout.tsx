import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Career Agent",
  description:
    "AI-powered job and candidate search with human-in-the-loop review.",
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
