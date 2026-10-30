/* The left list panel (and the mobile sheet). Its content follows the search tab:

     no tab   → CINEMAS   all cinemas (filtered by Filters / typed name)
     Movie    → MOVIES    every film, most widely showing first
     Location → LOCATIONS Lahore areas; picking one → "Near <area>" cinemas by distance

   Typing in the search bar filters whichever list is showing. Arrow keys + Enter in
   the search input move a cursor through these rows. */
import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Star, Navigation2, Clock, ChevronLeft, MapPin, Check } from 'lucide-react'
import type { Area, Cinema, CinemaSchedule, LatLng, Movie } from '../types'
import { distanceKm, formatDistance } from '../lib/geo'
import { formatDuration, formatTime, movieLanguageLabel } from '../lib/schedule'

export interface NextShow {
  time: string
  title: string
}

export type BrowseView = 'cinemas' | 'movies' | 'locations' | 'near'

export type BrowseItem =
  | { kind: 'movie'; key: string; movie: Movie; count: number }
  | { kind: 'area'; key: string; area: Area; nearby: number; nearestKm: number | null }
  | { kind: 'cinema'; key: string; cinema: Cinema; km: number | null; times: string[] | null; next: NextShow | undefined }

const NEARBY_KM = 5
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, '')

interface BuildArgs {
  view: BrowseView
  query: string
  movies: Movie[]
  cinemaCount: Map<string, number>
  areas: Area[]
  cinemas: Cinema[]
  matches: CinemaSchedule[]
  filtering: boolean
  nextShow: Map<string, NextShow>
  upcoming: (times: string[]) => string[]
  distanceFrom: LatLng | null
}

/** Rows for the current view, already filtered by the search text and sorted. */
export function buildBrowseItems(a: BuildArgs): BrowseItem[] {
  const q = norm(a.query.trim())

  if (a.view === 'movies') {
    return a.movies
      .filter((m) => !q || norm(m.title).includes(q) || m.genre.some((g) => norm(g).startsWith(q)) || norm(movieLanguageLabel(m)).startsWith(q))
      .map((m) => ({ kind: 'movie', key: `m-${m.id}`, movie: m, count: a.cinemaCount.get(m.id) ?? 0 }))
  }

  const cinemaRows = (list: Cinema[], from: LatLng | null): BrowseItem[] => {
    const matchById = new Map(a.matches.map((m) => [m.cinema.id, m]))
    return list
      .filter((c) => !a.filtering || matchById.has(c.id))
      .map((c) => {
        const match = matchById.get(c.id)
        return {
          kind: 'cinema' as const,
          key: `c-${c.id}`,
          cinema: c,
          km: from ? distanceKm(from, c) : null,
          times: a.filtering && match ? a.upcoming([...new Set(match.showings.flatMap((s) => s.times))].sort()) : null,
          next: a.nextShow.get(c.id),
        }
      })
      .sort((x, y) => (x.km != null && y.km != null ? x.km - y.km : y.cinema.rating - x.cinema.rating))
  }

  if (a.view === 'locations') {
    const areas: BrowseItem[] = a.areas
      .filter((ar) => !q || [ar.name, ...ar.aliases].some((n) => norm(n).includes(q)))
      .map((ar) => {
        const kms = a.cinemas.map((c) => distanceKm(ar, c))
        return { kind: 'area', key: `a-${ar.id}`, area: ar, nearby: kms.filter((k) => k <= NEARBY_KM).length, nearestKm: kms.length ? Math.min(...kms) : null }
      })
    // Typing a cinema's name in Location mode finds the cinema too
    const cinemaHits = q ? cinemaRows(a.cinemas.filter((c) => norm(c.name).includes(q)), null) : []
    return [...areas, ...cinemaHits]
  }

  const list = a.view === 'cinemas' && q ? a.cinemas.filter((c) => norm(c.name).includes(q) || norm(c.area).includes(q)) : a.cinemas
  return cinemaRows(list, a.distanceFrom)
}

interface Props {
  view: BrowseView
  items: BrowseItem[]
  title: string
  hint?: string
  /** "Back to all locations" in the Near view */
  onBack?: () => void
  cursor: number
  showCursor: boolean
  selectedCinemaId: string | null
  pickedMovieId: string | null
  /** True when a distance origin exists, so the first cinema row is labelled "closest" */
  hasOrigin: boolean
  onActivate: (item: BrowseItem) => void
  size?: 'md' | 'lg'
  /** Extra element on the right of the header (e.g. close button on mobile) */
  headerExtra?: React.ReactNode
}

export default function BrowsePanel({ view, items, title, hint, onBack, cursor, showCursor, selectedCinemaId, pickedMovieId, hasOrigin, onActivate, size = 'md', headerExtra }: Props) {
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (showCursor) listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor, showCursor])

  const swapKey = `${view}-${title}`
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-white/[0.06] px-5 pt-4 pb-3">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="-ml-1 mb-2 inline-flex items-center gap-1 rounded-full py-1 pr-2.5 pl-1 text-[11px] font-bold text-sky-200 transition hover:bg-sky-300/10 active:scale-95"
          >
            <ChevronLeft className="size-3.5" /> Back to all locations
          </button>
        )}
        <div className="flex items-start justify-between gap-3">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={swapKey} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.14 }} className="min-w-0">
              <h2 className={`truncate font-display leading-none tracking-wider text-white ${size === 'lg' ? 'text-[28px]' : 'text-2xl'}`}>
                {title} <span className="text-gold-400">{items.length}</span>
              </h2>
              {hint && <p className="mt-1 text-[11px] font-medium text-neutral-500">{hint}</p>}
            </motion.div>
          </AnimatePresence>
          {headerExtra}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {/* mode="wait": the old list leaves before the new one enters — no overlap */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.ul
            key={swapKey}
            ref={listRef}
            role="listbox"
            aria-label={title}
            initial={{ opacity: 0, x: 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -10 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="space-y-1 p-2"
          >
            {items.length === 0 && (
              <li className="px-6 py-12 text-center">
                <p className="font-display text-2xl tracking-wide text-white">No matches</p>
                <p className="mt-1 text-sm text-neutral-500">Try a different search or filter.</p>
              </li>
            )}
            {items.map((item, i) => {
              const active = showCursor && i === cursor
              const selected = (item.kind === 'cinema' && item.cinema.id === selectedCinemaId) || (item.kind === 'movie' && item.movie.id === pickedMovieId)
              const firstCinema = item.kind === 'cinema' && items.findIndex((x) => x.kind === 'cinema') === i
              const disabled = item.kind === 'cinema' && item.cinema.hasRecords === false
              return (
                <li key={item.key} data-index={i} role="option" aria-selected={active || selected}>
                  <button
                    type="button"
                    disabled={disabled}
                    title={disabled && item.kind === 'cinema' ? `Temporarily disabled — no live records for ${item.cinema.name}` : undefined}
                    onClick={() => { if (!disabled) onActivate(item) }}
                    className={`group flex w-full items-center gap-3 rounded-2xl p-3 text-left transition-all duration-200 ${
                      disabled ? 'cursor-not-allowed opacity-40 grayscale' : 'active:scale-[0.99]'
                    } ${selected ? 'bg-gold-400/[0.09] ring-1 ring-gold-400/35' : active ? 'bg-white/[0.07]' : !disabled ? 'hover:bg-white/[0.045]' : ''}`}
                  >
                    {item.kind === 'movie' && <MovieRow item={item} picked={selected} />}
                    {item.kind === 'area' && <AreaRow item={item} />}
                    {item.kind === 'cinema' && <CinemaRow item={item} selected={selected} closest={hasOrigin && firstCinema && item.km != null} />}
                  </button>
                </li>
              )
            })}
          </motion.ul>
        </AnimatePresence>
      </div>
    </div>
  )
}

function MovieRow({ item, picked }: { item: Extract<BrowseItem, { kind: 'movie' }>; picked: boolean }) {
  const { movie, count } = item
  return (
    <>
      <img src={movie.poster} alt="" loading="lazy" draggable={false} className="h-[60px] w-10 shrink-0 rounded-lg object-cover ring-1 ring-white/[0.08]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className={`truncate text-[13px] font-bold leading-tight ${picked ? 'text-gold-100' : 'text-white'}`}>{movie.title}</p>
          {picked ? (
            <Check className="size-4 shrink-0 text-gold-400" />
          ) : movie.rating ? (
            <span className="shrink-0 rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] font-bold text-neutral-400">{movie.rating}</span>
          ) : movie.listingStatus === 'coming_soon' ? (
            <span className="shrink-0 rounded-md border border-gold-300/20 px-1.5 py-0.5 text-[9px] font-bold text-gold-300">SOON</span>
          ) : null}
        </div>
        {(movie.genre.length > 0 || movie.duration > 0 || movie.language) && (
          <p className="mt-1 truncate text-[11px] text-neutral-500">
            {[movie.genre.join(' · '), movie.duration > 0 ? formatDuration(movie.duration) : '', movieLanguageLabel(movie)].filter(Boolean).join(' · ')}
          </p>
        )}
        <p className={`mt-1 text-[11px] font-semibold ${count ? 'text-gold-300/90' : movie.listingStatus === 'coming_soon' ? 'text-gold-300/80' : 'text-neutral-600'}`}>
          {count
            ? `Showing at ${count} ${count === 1 ? 'cinema' : 'cinemas'}`
            : movie.listingStatus === 'coming_soon'
              ? `${movie.releaseDateLabel ? `Release ${movie.releaseDateLabel}` : 'Coming soon'} · showtimes not posted`
              : movie.listingStatus === 'released_no_sessions'
                ? 'Released · no upcoming sessions'
                : movie.hasUpcomingShowtimes
                  ? 'Not showing on this date'
                  : 'No showtimes confirmed'}
        </p>
      </div>
    </>
  )
}

function AreaRow({ item }: { item: Extract<BrowseItem, { kind: 'area' }> }) {
  const { area, nearby, nearestKm } = item
  return (
    <>
      <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-sky-300/[0.08] text-sky-300 ring-1 ring-sky-300/15">
        <MapPin className="size-[18px]" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-bold leading-tight text-white">{area.name}</p>
        <p className={`mt-1 text-[11px] font-semibold ${nearby ? 'text-sky-200/90' : 'text-neutral-500'}`}>
          {nearby
            ? `${nearby} ${nearby === 1 ? 'cinema' : 'cinemas'} nearby`
            : nearestKm != null
              ? `Nearest cinema ${formatDistance(nearestKm)}`
              : 'No cinemas nearby'}
        </p>
      </div>
      <Navigation2 className="size-3.5 shrink-0 rotate-45 text-neutral-600 transition group-hover:text-sky-300" />
    </>
  )
}

function CinemaRow({ item, selected, closest }: { item: Extract<BrowseItem, { kind: 'cinema' }>; selected: boolean; closest: boolean }) {
  const { cinema, km, times, next } = item
  return (
    <>
      <div className="relative size-11 shrink-0 overflow-hidden rounded-xl bg-ink-700 ring-1 ring-white/[0.08]">
        <img src={cinema.image} alt="" loading="lazy" className="size-full object-cover transition-transform duration-500 group-hover:scale-110" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className={`truncate text-[13px] font-bold leading-tight ${selected ? 'text-gold-100' : 'text-white'}`}>{cinema.name}</p>
          <span className="inline-flex shrink-0 items-center gap-0.5 text-[11px] font-semibold text-neutral-400">
            <Star className="size-3 fill-gold-400 text-gold-400" />
            {cinema.rating.toFixed(1)}
          </span>
        </div>
        <p className="mt-1 flex items-center gap-1.5 text-[11px] text-neutral-500">
          <span className="truncate">{cinema.hasRecords === false ? 'No live records yet · temporarily disabled' : cinema.area}</span>
          {km != null && (
            <>
              <span className="text-neutral-700">•</span>
              <span className={`inline-flex shrink-0 items-center gap-1 font-semibold ${closest ? 'text-sky-300' : 'text-neutral-400'}`}>
                <Navigation2 className="size-2.5 rotate-45" />
                {formatDistance(km)}
                {closest && <span className="ml-0.5 text-[9px] font-extrabold uppercase tracking-wider">closest</span>}
              </span>
            </>
          )}
        </p>
        {times ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {times.length ? (
              times.slice(0, 4).map((t) => (
                <span key={t} className="rounded-md bg-gold-400/10 px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-gold-200">
                  {formatTime(t)}
                </span>
              ))
            ) : (
              <span className="text-[10px] font-medium text-neutral-500">All matching shows have started</span>
            )}
            {times.length > 4 && <span className="px-1 text-[10px] font-semibold text-neutral-500">+{times.length - 4}</span>}
          </div>
        ) : (
          <p className="mt-1.5 flex items-center gap-1.5 text-[11px]">
            <Clock className="size-3 shrink-0 text-gold-400/80" />
            {next ? (
              <span className="truncate text-neutral-300">
                <span className="font-bold tabular-nums text-gold-200">{formatTime(next.time)}</span>
                <span className="text-neutral-500"> · {next.title}</span>
              </span>
            ) : (
              <span className="text-neutral-500">No more shows this day</span>
            )}
          </p>
        )}
      </div>
    </>
  )
}
