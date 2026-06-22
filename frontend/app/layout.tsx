import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/sidebar";
import Providers from "@/components/providers";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AlgoTrader — Paper Trading Bot",
  description: "Algorithmic trading bot with paper trading",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className={`${inter.className} bg-gray-950 text-white antialiased`}>
        <Providers>
          <div className="flex">
            <Sidebar />
            <main className="ml-60 flex-1 min-h-screen bg-gray-950">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
