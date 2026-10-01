import { Providers } from "@/app/components/providers";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Music Player",
  description: "AI-powered music player.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      suppressHydrationWarning
    >
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap"
        />
      </head>
      <body
        className="antialiased"
        style={{ backgroundColor: "var(--color-surface)", color: "var(--color-on-surface)" }}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
