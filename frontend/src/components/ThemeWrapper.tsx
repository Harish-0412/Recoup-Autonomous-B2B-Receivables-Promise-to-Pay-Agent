"use client";

import React, { createContext, useContext, useEffect, useState, useMemo } from "react";
import type { PropsWithChildren } from "react";

export interface ThemeContextType {
  theme?: string;
  setTheme: (theme: string) => void;
  resolvedTheme?: string;
  themes: string[];
  systemTheme?: "light" | "dark";
  forcedTheme?: string;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: "light",
  setTheme: () => {},
  resolvedTheme: "light",
  themes: ["light", "dark"],
  systemTheme: "light",
});

export const useTheme = () => useContext(ThemeContext);

export default function ThemeWrapper({ children }: PropsWithChildren<{}>) {
  const [theme, setThemeState] = useState<string>("light");
  const [resolvedTheme, setResolvedTheme] = useState<string>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    try {
      const saved = localStorage.getItem("theme");
      if (saved && (saved === "dark" || saved === "light")) {
        setThemeState(saved);
        setResolvedTheme(saved);
        const root = document.documentElement;
        if (saved === "dark") {
          root.classList.add("dark");
          root.classList.remove("light");
          root.style.colorScheme = "dark";
        } else {
          root.classList.remove("dark");
          root.classList.add("light");
          root.style.colorScheme = "light";
        }
      } else {
        setThemeState("light");
        setResolvedTheme("light");
        const root = document.documentElement;
        root.classList.remove("dark");
        root.classList.add("light");
        root.style.colorScheme = "light";
      }
    } catch (_) {}
  }, []);

  useEffect(() => {
    if (!mounted) return;

    const root = document.documentElement;
    if (theme === "dark") {
      root.classList.add("dark");
      root.classList.remove("light");
      root.style.colorScheme = "dark";
      setResolvedTheme("dark");
    } else {
      root.classList.remove("dark");
      root.classList.add("light");
      root.style.colorScheme = "light";
      setResolvedTheme("light");
    }
  }, [theme, mounted]);

  const setTheme = (newTheme: string) => {
    setThemeState(newTheme);
    setResolvedTheme(newTheme);
    try {
      localStorage.setItem("theme", newTheme);
    } catch (_) {}
  };

  const value = useMemo(
    () => ({
      theme,
      setTheme,
      resolvedTheme,
      themes: ["light", "dark"],
      systemTheme: "light" as const,
    }),
    [theme, resolvedTheme]
  );

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}
