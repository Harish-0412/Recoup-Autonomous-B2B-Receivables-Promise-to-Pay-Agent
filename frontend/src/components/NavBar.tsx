"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { ArrowLeft, ShieldAlert } from 'lucide-react';

const navLinks = [
  { href: '/dashboard', label: 'Command Center' },
  { href: '/queue', label: 'Queue' },
  { href: '/inbox', label: 'Inbox' },
  { href: '/policy', label: 'Policy' },
  { href: '/reports/batch', label: 'Batch Audit' },
  { href: '/runs', label: 'Runs' },
  { href: '/models/recovery', label: 'Models' },
  { href: '/simulate', label: 'Simulate' },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 w-full border-b border-zinc-200/60 dark:border-white/10 bg-white/75 dark:bg-black/75 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between h-14">
        {/* Brand */}
        <div className="flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="h-7 w-7 rounded-lg bg-gradient-to-br from-orange-500 to-amber-500 flex items-center justify-center text-black font-bold text-sm shadow-sm group-hover:scale-105 transition-transform">
              R
            </div>
            <div className="flex flex-col">
              <span className="font-bold text-sm tracking-tight text-zinc-900 dark:text-white leading-none">
                Recoup
              </span>
              <span className="text-[10px] text-orange-600 dark:text-orange-400 font-mono leading-none mt-0.5">
                Autonomous AR
              </span>
            </div>
          </Link>

          {/* Navigation Links */}
          <nav className="hidden md:flex items-center gap-1">
            {navLinks.map((link) => {
              const isActive = pathname === link.href || (link.href !== '/' && pathname.startsWith(link.href + '/'));
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "px-3 py-1.5 rounded-lg text-xs font-medium transition-all",
                    isActive
                      ? "bg-zinc-100 dark:bg-white/10 text-zinc-950 dark:text-white font-semibold shadow-xs"
                      : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-50 dark:hover:bg-white/5"
                  )}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Landing Page</span>
          </Link>
        </div>
      </div>
    </header>
  );
}
