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
    if (index === words.length - 1) {
      setTimeout(() => {
        onComplete();
      }, 1000);
      return;
    }

    const timeout = setTimeout(() => {
      setIndex((prev) => prev + 1);
    }, index === 0 ? 1000 : 350); // First word holds slightly longer, the rest flash quickly

    return () => clearTimeout(timeout);
  }, [index, onComplete]);

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black"
      initial={{ y: 0 }}
      exit={{ y: "-100vh", transition: { duration: 0.8, ease: [0.76, 0, 0.24, 1] } }}
    >
      <AnimatePresence mode="wait">
        <motion.div
          key={index}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className="text-4xl md:text-6xl font-medium tracking-tighter text-white"
        >
          {words[index]}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  );
}
