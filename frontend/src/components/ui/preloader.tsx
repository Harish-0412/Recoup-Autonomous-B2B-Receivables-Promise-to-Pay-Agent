"use client";

import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

const words = [
  "Analyze.",
  "Prioritize.",
  "Negotiate.",
  "Recover.",
  "Recoup."
];

export function Preloader({ onComplete }: { onComplete: () => void }) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    // Lock scroll while preloader is active
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "auto";
    };
  }, []);

  useEffect(() => {
    if (index === words.length - 1) {
      const finishTimer = setTimeout(() => {
        onComplete();
      }, 750);
      return () => clearTimeout(finishTimer);
    }

    const duration = index === 0 ? 550 : 380;
    const timeout = setTimeout(() => {
      setIndex((prev) => prev + 1);
    }, duration);

    return () => clearTimeout(timeout);
  }, [index, onComplete]);

  const progress = ((index + 1) / words.length) * 100;

  return (
    <motion.div
      className="fixed inset-0 z-[99999] flex flex-col items-center justify-between p-8 sm:p-14 bg-white text-zinc-950 dark:bg-black dark:text-white select-none pointer-events-auto transition-colors duration-300"
      initial={{ y: 0 }}
      exit={{
        y: "-100%",
        transition: { duration: 0.85, ease: [0.76, 0, 0.24, 1] }
      }}
    >
      {/* Top Brand Label */}
      <div className="w-full flex items-center justify-between max-w-5xl">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-orange-500 animate-ping" />
          <span className="text-xs font-mono font-semibold tracking-widest text-zinc-500 uppercase">
            Recoup Protocol
          </span>
        </div>
        <span className="text-xs font-mono text-zinc-400">
          {index + 1} / {words.length}
        </span>
      </div>

      {/* Center Cycling Words */}
      <div className="flex flex-col items-center justify-center my-auto overflow-hidden min-h-[120px]">
        <AnimatePresence mode="wait">
          <motion.div
            key={index}
            initial={{ opacity: 0, y: 35, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -35, filter: "blur(4px)" }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            className="flex items-center gap-4 text-5xl sm:text-7xl md:text-8xl font-extrabold tracking-tighter"
          >
            <span className="inline-block h-3.5 w-3.5 sm:h-4 sm:w-4 rounded-full bg-orange-500 shadow-sm shadow-orange-500/50" />
            <span className={index === words.length - 1 ? "bg-gradient-to-r from-orange-500 via-amber-500 to-orange-600 bg-clip-text text-transparent" : ""}>
              {words[index]}
            </span>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Bottom Progress Bar */}
      <div className="w-full max-w-sm flex flex-col items-center gap-3">
        <div className="w-full h-1 bg-zinc-200 dark:bg-zinc-800 rounded-full overflow-hidden">
          <motion.div
            className="h-full bg-orange-500"
            initial={{ width: "0%" }}
            animate={{ width: `${progress}%` }}
            transition={{ duration: 0.35, ease: "easeOut" }}
          />
        </div>
        <span className="text-[11px] font-mono text-zinc-400 dark:text-zinc-500 uppercase tracking-wider">
          Initializing Autonomous Engine
        </span>
      </div>
    </motion.div>
  );
}

export default Preloader;
