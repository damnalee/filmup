import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { CalendarDays, Check, ChevronDown, Moon, SlidersHorizontal, Sun, Sunrise, Sunset } from 'lucide-react'
import type { Cinema, Filters as FilterState, TimeOfDay } from '../types'
import { TIME_OF_DAY, type DateOption } from '../lib/schedule'

const TOD_ICON: Record<TimeOfDay, typeof Sun> = { morning: Sunrise, afternoon: Sun, evening: Sunset, night: Moon }

/* ── Popover: portalled + fixed so it never clips inside the glass bar ── */

function usePopover() {
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!anchor.current?.contains(t) && !panel.current?.contains(t)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const onResize = () => setOpen(false)
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  return { open, setOpen, anchor, panel }
}

function Popover({
  open,
  anchor,
  panel,
  width,
  children,
}: {
  open: boolean
  anchor: React.RefObject<HTMLButtonElement | null>
  panel: React.RefObject<HTMLDivElement | null>
  width: number
  children: ReactNode
}) {
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)

  useLayoutEffect(() => {
    if (!open || !anchor.current) return
    const r = anchor.current.getBoundingClientRect()
    const vw = window.innerWidth
    const w = Math.min(width, vw - 32)
    // Align to the anchor's left edge, but keep a 16px gutter from both screen edges
    const left = Math.max(16, Math.min(r.left, vw - w - 16))
    setPos({ top: r.bottom + 8, left, width: w })
  }, [open, anchor, width])

  return createPortal(
    <AnimatePresence>
      {open && pos && (
        <motion.div
          ref={panel}
          initial={{ opacity: 0, y: -6, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -6, scale: 0.98 }}
          transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
          style={{ top: pos.top, left: pos.left, width: pos.width, transformOrigin: 'top left' }}
          className="glass fixed z-[70] overflow-hidden rounded-2xl"
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}

/* ── Date chip ─────────────────────────────────────────────── */

export function DateChip({ dates, value, onChange }: { dates: DateOption[]; value: string; onChange: (iso: string) => void }) {
  const { open, setOpen, anchor, panel } = usePopover()
  const current = dates.find((d) => d.iso === value)
  const label = current ? (current.label === current.weekday ? `${current.weekday} ${current.day}` : current.label) : 'Date'

  return (
    <>
      <button ref={anchor} type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open} className="chip chip-active">
        <CalendarDays className="size-3.5" />
        <span>{label}</span>
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <Popover open={open} anchor={anchor} panel={panel} width={232}>
        <ul role="listbox" className="p-2">
          {dates.map((d) => (
            <li key={d.iso}>
              <button
                type="button"
                role="option"
                aria-selected={d.iso === value}
                onClick={() => {
                  onChange(d.iso)
                  setOpen(false)
                }}
                className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm text-neutral-300 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                <span>
                  <span className="font-semibold">{d.label}</span>
                  <span className="ml-2 text-neutral-500">
                    {d.weekday === d.label ? '' : `${d.weekday} · `}
                    {d.month} {d.day}
                  </span>
                </span>
                {d.iso === value && <Check className="size-4 text-gold-400" />}
              </button>
            </li>
          ))}
        </ul>
      </Popover>
    </>
  )
}

/* ── Filters button + popover (time of day, genre, cinema) ── */

interface FiltersProps {
  filters: FilterState
  genres: string[]
  cinemas: Cinema[]
  resultCount: number
  onChange: (patch: Partial<FilterState>) => void
  onReset: () => void
}

export function FiltersButton({ filters, genres, cinemas, resultCount, onChange, onReset }: FiltersProps) {
  const { open, setOpen, anchor, panel } = usePopover()
  const count = filters.timesOfDay.length + (filters.genre ? 1 : 0) + (filters.cinemaId ? 1 : 0)
  const toggleTod = (id: TimeOfDay) =>
    onChange({ timesOfDay: filters.timesOfDay.includes(id) ? filters.timesOfDay.filter((t) => t !== id) : [...filters.timesOfDay, id] })

  return (
    <>
      <button
        ref={anchor}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`chip ${count ? 'chip-active' : ''}`}
      >
        <SlidersHorizontal className="size-3.5" />
        <span>Filters</span>
        {count > 0 && <span className="grid size-[18px] place-items-center rounded-full bg-gold-400 text-[10px] font-extrabold text-ink-950">{count}</span>}
      </button>

      <Popover open={open} anchor={anchor} panel={panel} width={344}>
        <div role="dialog" aria-label="Filters" className="max-h-[min(560px,calc(100dvh-140px))] overflow-y-auto p-4">
          <Section title="Time of day">
            <div className="grid grid-cols-2 gap-2">
              {TIME_OF_DAY.map((t) => {
                const Icon = TOD_ICON[t.id]
                const on = filters.timesOfDay.includes(t.id)
                return (
                  <button
                    key={t.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleTod(t.id)}
                    className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-left transition-all active:scale-[0.98] ${
                      on ? 'border-gold-400/50 bg-gold-400/12 text-gold-100' : 'border-white/[0.06] bg-white/[0.03] text-neutral-300 hover:border-white/15 hover:bg-white/[0.06]'
                    }`}
                  >
                    <Icon className={`size-4 shrink-0 ${on ? 'text-gold-300' : 'text-neutral-500'}`} />
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold">{t.label}</span>
                      <span className="block text-[10px] text-neutral-500">{t.hint}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </Section>

          <Section title="Genre">
            <div className="flex flex-wrap gap-2">
              {genres.map((g) => {
                const on = filters.genre === g
                return (
                  <button
                    key={g}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange({ genre: on ? null : g })}
                    className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition-all active:scale-95 ${
                      on ? 'border-gold-400/50 bg-gold-400/15 text-gold-100' : 'border-white/[0.06] bg-white/[0.03] text-neutral-400 hover:border-white/15 hover:text-white'
                    }`}
                  >
                    {g}
                  </button>
                )
              })}
            </div>
          </Section>

          <Section title="Cinema">
            <div className="space-y-1">
              {[{ id: null, name: 'All cinemas', area: '' }, ...cinemas].map((c) => {
                const on = filters.cinemaId === c.id
                return (
                  <button
                    key={c.id ?? 'all'}
                    type="button"
                    onClick={() => onChange({ cinemaId: c.id })}
                    className={`flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition-colors ${on ? 'bg-white/[0.06]' : 'hover:bg-white/[0.04]'}`}
                  >
                    <span className="min-w-0">
                      <span className={`block truncate text-sm font-medium ${on ? 'text-white' : 'text-neutral-300'}`}>{c.name}</span>
                      {c.area && <span className="block text-[11px] text-neutral-500">{c.area}</span>}
                    </span>
                    {on && <Check className="size-4 shrink-0 text-gold-400" />}
                  </button>
                )
              })}
            </div>
          </Section>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-white/[0.06] px-4 py-3">
          <button type="button" onClick={onReset} className="rounded-lg px-2 py-1.5 text-xs font-semibold text-neutral-400 transition hover:text-white">
            Reset all
          </button>
          <button type="button" onClick={() => setOpen(false)} className="rounded-xl bg-gold-400 px-4 py-2 text-xs font-bold text-ink-950 transition hover:bg-gold-300 active:scale-[0.97]">
            Show {resultCount} {resultCount === 1 ? 'cinema' : 'cinemas'}
          </button>
        </div>
      </Popover>
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="[&:not(:first-child)]:mt-5">
      <h3 className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-neutral-500">{title}</h3>
      {children}
    </section>
  )
}
