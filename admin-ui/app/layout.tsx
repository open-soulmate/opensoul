import type { Metadata } from "next";
import "./globals.css";
import { AppWrapper } from "./app-wrapper";

export const metadata: Metadata = {
  title: "OpenSoul 管理后台",
  description: "OpenSoul Administration Panel",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="antialiased">
        <AppWrapper>{children}</AppWrapper>
      </body>
    </html>
  );
}
