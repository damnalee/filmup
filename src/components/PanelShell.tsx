import type { ReactNode } from 'react'
import { AnimatePresence, motion, useDragControls, type PanInfo } from 'framer-motion'
import { useMoving } from '../hooks/useMoving'

interface Props {
  /** Changing this key swaps the panel with an exit/enter animation */
  panelKey: string | null
  isDesktop: boolean
  label: string
  onClose: () => void
  /** Render the body; on mobile, `startDrag` lets a hero area act as the sheet's drag handle */
  children: (startDrag?: (e: React.PointerEvent) => void) => ReactNode
}

/** Side panel on desktop, draggable bottom sheet on mobile. Shared by the Cinema and Movie panels. */
export default function PanelShell({ panelKey, isDesktop, label, onClose, children }: Props) {
  const drag = useDragControls()
  const motionState = useMoving()
  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.y > 140 || info.velocity.y > 600) onClose()
  }

  return (
    <AnimatePresence>
      {panelKey &&
        (isDesktop ? (
          <motion.aside
            key={panelKey}
            initial={{ x: '110%', opacity: 0.4 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '110%', opacity: 0.4 }}
            transition={{ type: 'spring', stiffness: 260, damping: 32 }}
            {...motionState}
            className="glass fixed top-4 right-4 bottom-4 z-40 flex w-[440px] flex-col overflow-hidden rounded-3xl"
            aria-label={label}
          >
            {children()}
          </motion.aside>
        ) : (
          <motion.div key={panelKey} className="contents">
            <motion.div
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={onClose}
            />
            <motion.aside
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ type: 'spring', stiffness: 300, damping: 34 }}
              drag="y"
              dragControls={drag}
              dragListener={false}
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.6 }}
              onDragEnd={onDragEnd}
              {...motionState}
              className="glass fixed inset-x-0 bottom-0 z-50 flex max-h-[88dvh] flex-col overflow-hidden rounded-t-[28px] pb-[env(safe-area-inset-bottom)]"
              aria-label={label}
            >
              <div
                onPointerDown={(e) => drag.start(e)}
                className="absolute inset-x-0 top-0 z-20 flex h-7 cursor-grab touch-none justify-center pt-2.5 active:cursor-grabbing"
              >
                <span className="h-1.5 w-11 rounded-full bg-white/30" />
              </div>
              {children((e) => drag.start(e))}
            </motion.aside>
          </motion.div>
        ))}
    </AnimatePresence>
  )
}
