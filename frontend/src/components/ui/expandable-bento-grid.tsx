'use client'

import React, { useEffect, useId, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useOutsideClick } from '@/hooks/use-outside-click'
import { X } from 'lucide-react'

export interface BentoItem {
    id: string | number
    title: string
    subtitle?: string
    description?: string
    content: React.ReactNode
    icon?: React.ReactNode
    className?: string
    actionLabel?: string
    actionHref?: string
}

export interface BentoGridProps {
    items: BentoItem[]
    className?: string
}

export function ExpandableBentoGrid({ items, className }: BentoGridProps) {
    const [active, setActive] = useState<BentoItem | null>(null)
    const ref = useRef<HTMLDivElement>(null)
    const id = useId()

    useEffect(() => {
        function onKeyDown(event: KeyboardEvent) {
            if (event.key === 'Escape') {
                setActive(null)
            }
        }

        if (active) {
            document.body.style.overflow = 'hidden'
        } else {
            document.body.style.overflow = 'auto'
        }

        window.addEventListener('keydown', onKeyDown)
        return () => window.removeEventListener('keydown', onKeyDown)
    }, [active])

    useOutsideClick(ref, () => setActive(null))

    return (
        <>
            <AnimatePresence>
                {active && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        className="fixed inset-0 bg-black/60 backdrop-blur-sm h-full w-full z-[10000]"
                    />
                )}
            </AnimatePresence>

            <AnimatePresence>
                {active ? (
                    <div className="fixed inset-0 grid place-items-center z-[10001] p-4 sm:p-6 overflow-y-auto">
                        <motion.div
                            layoutId={`card-${active.title}-${id}`}
                            ref={ref}
                            transition={{ type: "spring", damping: 28, stiffness: 280 }}
                            className="relative w-full max-w-[540px] my-auto flex flex-col bg-white dark:bg-zinc-950 rounded-3xl border border-zinc-200 dark:border-zinc-800 shadow-2xl overflow-hidden"
                        >
                            {/* Close Button */}
                            <button
                                aria-label="Close modal"
                                className="absolute top-4 right-4 z-20 flex items-center justify-center bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 rounded-full h-8 w-8 transition-colors cursor-pointer"
                                onClick={() => setActive(null)}
                            >
                                <X className="h-4 w-4 text-zinc-700 dark:text-zinc-200" />
                            </button>

                            {/* Header Icon Viewport */}
                            <motion.div
                                layoutId={`image-${active.title}-${id}`}
                                transition={{ type: "spring", damping: 28, stiffness: 280 }}
                            >
                                <div className="w-full h-44 sm:h-52 bg-gradient-to-br from-orange-500/15 via-amber-500/5 to-transparent dark:from-orange-500/20 dark:via-amber-500/5 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-center">
                                    {active.icon ? (
                                        <div className="scale-[2.4] text-orange-600 dark:text-orange-400">
                                            {active.icon}
                                        </div>
                                    ) : (
                                        <div className="w-full h-full bg-zinc-100 dark:bg-zinc-800" />
                                    )}
                                </div>
                            </motion.div>

                            <div className="p-6">
                                <div className="flex justify-between items-start gap-4 mb-4">
                                    <div>
                                        <motion.h3
                                            layoutId={`title-${active.title}-${id}`}
                                            transition={{ type: "spring", damping: 28, stiffness: 280 }}
                                            className="font-bold text-xl text-zinc-950 dark:text-white"
                                        >
                                            {active.title}
                                        </motion.h3>
                                        <motion.p
                                            layoutId={`description-${active.title}-${id}`}
                                            transition={{ type: "spring", damping: 28, stiffness: 280 }}
                                            className="text-zinc-600 dark:text-zinc-400 text-sm mt-1"
                                        >
                                            {active.description || active.subtitle}
                                        </motion.p>
                                    </div>

                                    {active.actionLabel && (
                                        <a
                                            href={active.actionHref || "#simulator"}
                                            onClick={() => setActive(null)}
                                            className="px-4 py-2 text-xs rounded-xl font-semibold bg-orange-500 hover:bg-orange-600 text-black transition-colors whitespace-nowrap shadow-sm shrink-0"
                                        >
                                            {active.actionLabel}
                                        </a>
                                    )}
                                </div>

                                <motion.div
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    exit={{ opacity: 0 }}
                                    transition={{ duration: 0.15 }}
                                    className="pt-2 text-zinc-700 dark:text-zinc-300 text-sm leading-relaxed max-h-[240px] overflow-y-auto pr-1"
                                >
                                    {active.content}
                                </motion.div>
                            </div>
                        </motion.div>
                    </div>
                ) : null}
            </AnimatePresence>

            <ul className={`max-w-6xl mx-auto w-full gap-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 items-stretch ${className || ''}`}>
                {items.map((item) => (
                    <motion.div
                        layoutId={`card-${item.title}-${id}`}
                        key={item.id}
                        onClick={() => setActive(item)}
                        transition={{ type: "spring", damping: 28, stiffness: 280 }}
                        className="p-5 flex flex-col justify-between rounded-2xl cursor-pointer bg-zinc-50/80 dark:bg-zinc-950/60 border border-zinc-200 dark:border-white/10 hover:border-orange-500/40 hover:bg-zinc-100/90 dark:hover:bg-zinc-900/80 transition-colors shadow-sm dark:shadow-none min-h-[140px] group"
                    >
                        <div className="flex gap-4 items-start w-full">
                            <motion.div
                                layoutId={`image-${item.title}-${id}`}
                                transition={{ type: "spring", damping: 28, stiffness: 280 }}
                            >
                                <div className="h-12 w-12 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center text-orange-600 dark:text-orange-400 p-2 group-hover:scale-105 transition-transform shrink-0">
                                    {item.icon}
                                </div>
                            </motion.div>
                            <div className="flex-1 min-w-0">
                                <motion.h3
                                    layoutId={`title-${item.title}-${id}`}
                                    transition={{ type: "spring", damping: 28, stiffness: 280 }}
                                    className="font-semibold text-base text-zinc-950 dark:text-white leading-tight mb-1"
                                >
                                    {item.title}
                                </motion.h3>
                                <motion.p
                                    layoutId={`description-${item.title}-${id}`}
                                    transition={{ type: "spring", damping: 28, stiffness: 280 }}
                                    className="text-zinc-600 dark:text-zinc-400 text-xs md:text-sm line-clamp-2 leading-relaxed"
                                >
                                    {item.subtitle}
                                </motion.p>
                            </div>
                        </div>

                        <div className="mt-4 pt-3 border-t border-zinc-200/60 dark:border-white/5 flex items-center justify-between text-[11px] font-medium text-orange-600 dark:text-orange-400">
                            <span>Click to expand architecture & rules</span>
                            <span className="group-hover:translate-x-1 transition-transform">→</span>
                        </div>
                    </motion.div>
                ))}
            </ul>
        </>
    )
}

export default ExpandableBentoGrid
