"use client";

import React, { useState } from "react";
import { Sidebar, SidebarBody, SidebarLink } from "@/components/ui/sidebar";
import {
  LayoutDashboard,
  ListOrdered,
  Inbox,
  ShieldCheck,
  BarChart3,
  PlayCircle,
  BrainCircuit,
  Sliders,
  Wallet,
  LogOut,
} from "lucide-react";
import Link from "next/link";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import StatusStrip from "@/components/StatusStrip";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  const navLinks = [
    {
      label: "Command Center",
      href: "/dashboard",
      icon: (
        <LayoutDashboard className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Queue",
      href: "/queue",
      icon: (
        <ListOrdered className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Inbox",
      href: "/inbox",
      icon: (
        <Inbox className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Policy",
      href: "/policy",
      icon: (
        <ShieldCheck className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Batch Audit",
      href: "/reports/batch",
      icon: (
        <BarChart3 className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Payments",
      href: "/payments",
      icon: (
        <Wallet className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Runs",
      href: "/runs",
      icon: (
        <PlayCircle className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Models",
      href: "/models",
      icon: (
        <BrainCircuit className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
    {
      label: "Simulate",
      href: "/simulate",
      icon: (
        <Sliders className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
      ),
    },
  ];

  return (
    <div className="h-screen w-full bg-zinc-50 dark:bg-neutral-950 text-zinc-900 dark:text-zinc-100 flex flex-col antialiased overflow-hidden">
      <StatusStrip />

      <div className="flex flex-1 w-full h-full min-h-0 overflow-hidden">
        <Sidebar open={open} setOpen={setOpen}>
          <SidebarBody className="justify-between gap-6 h-full">
            <div className="flex flex-col flex-1 overflow-y-auto overflow-x-hidden">
              {open ? <Logo /> : <LogoIcon />}
              <div className="mt-8 flex flex-col gap-1.5">
                {navLinks.map((link, idx) => (
                  <SidebarLink key={idx} link={link} />
                ))}
              </div>
            </div>
            <div className="mt-auto pt-4 border-t border-neutral-200/60 dark:border-white/5">
              <SidebarLink
                link={{
                  label: "Sign Out",
                  href: "/",
                  icon: (
                    <LogOut className="text-neutral-700 dark:text-neutral-200 h-5 w-5 flex-shrink-0" />
                  ),
                }}
              />
              <div
                className={cn(
                  "mt-4 flex items-center gap-3 px-3 py-2.5 rounded-xl",
                  "bg-white dark:bg-white/5 border border-neutral-200/60 dark:border-white/5"
                )}
              >
                <div className="h-8 w-8 flex-shrink-0 rounded-full bg-gradient-to-br from-orange-500 to-amber-500 flex items-center justify-center text-white font-bold text-xs shadow-sm">
                  H
                </div>
                <motion.div
                  animate={{
                    display: open ? "block" : "none",
                    opacity: open ? 1 : 0,
                  }}
                  className="min-w-0"
                >
                  <p className="text-sm font-semibold text-neutral-800 dark:text-neutral-100 truncate">
                    Harish
                  </p>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400 truncate">
                    Admin
                  </p>
                </motion.div>
              </div>
            </div>
          </SidebarBody>
        </Sidebar>

        <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </div>
    </div>
  );
}

export const Logo = () => {
  return (
    <Link
      href="/"
      className="font-normal flex space-x-2 items-center text-sm text-black dark:text-white py-1 relative z-20"
    >
      <div className="h-8 w-8 bg-gradient-to-br from-orange-500 to-amber-500 rounded-lg flex items-center justify-center text-black font-bold text-sm shadow-sm flex-shrink-0">
        R
      </div>
      <div className="flex flex-col">
        <motion.span
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="font-bold text-base text-zinc-900 dark:text-white whitespace-pre leading-none"
        >
          Recoup
        </motion.span>
        <motion.span
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-[10px] font-mono text-orange-600 dark:text-orange-400 leading-none mt-0.5 font-semibold tracking-wide"
        >
          AUTONOMOUS AR
        </motion.span>
      </div>
    </Link>
  );
};

export const LogoIcon = () => {
  return (
    <Link
      href="/"
      className="font-normal flex items-center justify-center py-1 relative z-20 w-full"
    >
      <div className="h-8 w-8 bg-gradient-to-br from-orange-500 to-amber-500 rounded-lg flex items-center justify-center text-black font-bold text-sm shadow-sm flex-shrink-0">
        R
      </div>
    </Link>
  );
};
