"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, TrendingUp, Briefcase, History, Settings, Bot, Activity, FlaskConical, Radar } from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/",          label: "Dashboard",   icon: LayoutDashboard },
  { href: "/strategies", label: "Strategies",  icon: TrendingUp },
  { href: "/backtest",  label: "Backtesting", icon: FlaskConical },
  { href: "/scan",      label: "Market Scan", icon: Radar },
  { href: "/portfolio", label: "Portfolio",   icon: Briefcase },
  { href: "/trades",    label: "Trades",      icon: History },
  { href: "/settings",  label: "Settings",    icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-60 bg-gray-950 border-r border-gray-800 flex flex-col min-h-screen fixed left-0 top-0 z-40">
      <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-800">
        <div className="p-2 bg-blue-600 rounded-lg">
          <Bot className="w-4 h-4 text-white" />
        </div>
        <div>
          <p className="font-bold text-white text-sm">AlgoTrader</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
            <span className="text-xs text-gray-400">Paper Trading</span>
          </div>
        </div>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
                active ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-gray-800"
              )}
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-4 py-4 border-t border-gray-800">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Activity className="w-3.5 h-3.5" />
          <span>v1.0.0 · AlgoTrader Pro</span>
        </div>
      </div>
    </aside>
  );
}
