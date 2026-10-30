import { useState } from 'react'

/**
 * Tracks whether a framer-motion element is mid-animation. Panels set `data-moving` while
 * they slide/fade, and CSS turns their backdrop blur off for that time: animating an element
 * that has backdrop-filter is very expensive (especially in Safari).
 */
export function useMoving() {
  const [moving, setMoving] = useState(false)
  return {
    'data-moving': moving || undefined,
    onAnimationStart: () => setMoving(true),
    onAnimationComplete: () => setMoving(false),
  }
}
