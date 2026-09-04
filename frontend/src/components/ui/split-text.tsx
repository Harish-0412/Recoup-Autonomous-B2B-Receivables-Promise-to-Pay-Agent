"use client";

import React, { useRef, useEffect, useState } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText as GSAPSplitText } from "gsap/SplitText";
import { useGSAP } from "@gsap/react";

if (typeof window !== "undefined") {
  gsap.registerPlugin(ScrollTrigger, GSAPSplitText, useGSAP);
}

export interface SplitTextProps {
  text: string;
  className?: string;
  delay?: number;
  duration?: number;
  ease?: string;
  splitType?: "chars" | "words" | "lines" | "chars,words" | string;
  from?: Record<string, any>;
  to?: Record<string, any>;
  threshold?: number;
  rootMargin?: string;
  textAlign?: "left" | "center" | "right" | "justify";
  tag?: "p" | "h1" | "h2" | "h3" | "h4" | "span" | "div";
  onLetterAnimationComplete?: () => void;
  highlightWords?: string[];
  highlightClassName?: string;
}

export const SplitText: React.FC<SplitTextProps> = ({
  text,
  className = "",
  delay = 40,
  duration = 1.1,
  ease = "power3.out",
  splitType = "words",
  from = { opacity: 0, y: 30 },
  to = { opacity: 1, y: 0 },
  threshold = 0.1,
  rootMargin = "-50px",
  textAlign = "center",
  tag = "p",
  onLetterAnimationComplete,
  highlightWords,
  highlightClassName,
}) => {
  const ref = useRef<HTMLElement>(null);
  const animationCompletedRef = useRef(false);
  const onCompleteRef = useRef(onLetterAnimationComplete);
  const [fontsLoaded, setFontsLoaded] = useState(false);

  useEffect(() => {
    onCompleteRef.current = onLetterAnimationComplete;
  }, [onLetterAnimationComplete]);

  useEffect(() => {
    if (typeof document !== "undefined") {
      if (document.fonts && document.fonts.status === "loaded") {
        setFontsLoaded(true);
      } else if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => {
          setFontsLoaded(true);
        });
      } else {
        setFontsLoaded(true);
      }
    }
  }, []);

  useGSAP(
    () => {
      if (!ref.current || !text || !fontsLoaded) return;
      if (animationCompletedRef.current) return;

      const el = ref.current as any;
      if (el._rbsplitInstance) {
        try {
          el._rbsplitInstance.revert();
        } catch (_) {
          /* noop */
        }
        el._rbsplitInstance = null;
      }

      const startPct = (1 - threshold) * 100;
      const marginMatch = /^(-?\d+(?:\.\d+)?)(px|em|rem|%)?$/.exec(rootMargin);
      const marginValue = marginMatch ? parseFloat(marginMatch[1]) : 0;
      const marginUnit = marginMatch ? marginMatch[2] || "px" : "px";
      const sign =
        marginValue === 0
          ? ""
          : marginValue < 0
          ? `-=${Math.abs(marginValue)}${marginUnit}`
          : `+=${marginValue}${marginUnit}`;
      const start = `top ${startPct}%${sign}`;

      let targets: any;
      const assignTargets = (self: any) => {
        if (splitType.includes("chars") && self.chars && self.chars.length) targets = self.chars;
        if (!targets && splitType.includes("words") && self.words && self.words.length) targets = self.words;
        if (!targets && splitType.includes("lines") && self.lines && self.lines.length) targets = self.lines;
        if (!targets) targets = self.chars || self.words || self.lines;
      };

      try {
        const splitInstance = new GSAPSplitText(el, {
          type: splitType,
          smartWrap: true,
          autoSplit: splitType === "lines",
          linesClass: "split-line",
          wordsClass: "split-word",
          charsClass: "split-char",
          reduceWhiteSpace: false,
          onSplit: (self: any) => {
            // Apply highlight classes to targeted words if requested
            if (highlightWords && highlightWords.length > 0 && self.words) {
              const cleanHighlights = highlightWords.map((w) => w.toLowerCase());
              const highlightClasses = (
                highlightClassName || "text-orange-500 dark:text-orange-400 font-bold"
              ).split(" ");

              self.words.forEach((wordEl: HTMLElement) => {
                const wordText = wordEl.textContent?.replace(/[^\w]/g, "").toLowerCase();
                if (wordText && cleanHighlights.includes(wordText)) {
                  wordEl.classList.add(...highlightClasses);
                }
              });
            }

            assignTargets(self);
            if (!targets || targets.length === 0) return;

            const tween = gsap.fromTo(
              targets,
              { ...from },
              {
                ...to,
                duration,
                ease,
                stagger: delay / 1000,
                scrollTrigger: {
                  trigger: el,
                  start,
                  once: true,
                  fastScrollEnd: true,
                  anticipatePin: 0.4,
                },
                onComplete: () => {
                  animationCompletedRef.current = true;
                  onCompleteRef.current?.();
                },
                willChange: "transform, opacity",
                force3D: true,
              }
            );
            return tween;
          },
        });

        el._rbsplitInstance = splitInstance;
      } catch (err) {
        console.warn("GSAP SplitText execution error:", err);
      }

      return () => {
        ScrollTrigger.getAll().forEach((st) => {
          if (st.trigger === el) st.kill();
        });
        try {
          if (el._rbsplitInstance) {
            el._rbsplitInstance.revert();
          }
        } catch (_) {
          /* noop */
        }
        el._rbsplitInstance = null;
      };
    },
    {
      dependencies: [
        text,
        delay,
        duration,
        ease,
        splitType,
        JSON.stringify(from),
        JSON.stringify(to),
        threshold,
        rootMargin,
        fontsLoaded,
        JSON.stringify(highlightWords),
        highlightClassName,
      ],
      scope: ref,
    }
  );

  const renderTag = () => {
    const style: React.CSSProperties = {
      textAlign,
      whiteSpace: "normal",
      wordWrap: "break-word",
      willChange: "transform, opacity",
    };

    const classes = `split-parent ${className}`;
    const Tag = (tag || "p") as any;

    return (
      <Tag ref={ref} style={style} className={classes}>
        {text}
      </Tag>
    );
  };

  return renderTag();
};

export default SplitText;
