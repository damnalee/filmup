import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Play, Clock, Languages, CalendarDays, Clapperboard, Navigation2, ChevronRight } from 'lucide-react'
import type { Cinema, CinemaData, LatLng, Movie } from '../types'
import { dateOptions, formatDuration, toMinutes, movieLanguageLabel } from '../lib/schedule'
import { isPastCinemaShow, toCinemaDate } from '../lib/cinemaDay'
import { distanceKm, formatDistance } from '../lib/geo'
import DateSelector from './DateSelector'
import ShowtimeChips from './ShowtimeChips'
import PanelShell from './PanelShell'
import AdvanceBookingSignup from './AdvanceBookingSignup'

interface Props {
  movie: Movie | null
  data: CinemaData
  date: string
  now: Date
  isDesktop: boolean
  /** Sort by distance from here when known (user location or a searched area) */
  distanceFrom: LatLng | null
  onDateChange: (iso: string) => void
  onClose: () => void
  onSelectCinema: (cinemaId: string) => void
  onSelectTime: (cinema: Cinema, movieId: string, time: string) => void
}

const SYNOPSIS_CLAMP = 160
const list = { hidden: {}, show: { transition: { staggerChildren: 0.05, delayChildren: 0.12 } } }
const item = { hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] as const } } }

export default function MoviePanel(props: Props) {
  const { movie, isDesktop, onClose } = props
  return (
    <PanelShell panelKey={movie ? `movie-${movie.id}` : null} isDesktop={isDesktop} label={`${movie?.title ?? 'Movie'} details`} onClose={onClose}>
      {(startDrag) => movie && <Body {...props} movie={movie} onHeroPointerDown={startDrag} />}
    </PanelShell>
  )
}

function Body({
  movie,
  data,
  date,
  now,
  distanceFrom,
  onDateChange,
  onClose,
  onSelectCinema,
  onSelectTime,
  onHeroPointerDown,
}: Props & { movie: Movie; onHeroPointerDown?: (e: React.PointerEvent) => void }) {
  const [expanded, setExpanded] = useState(false)
  const days = useMemo(() => dateOptions(toCinemaDate(now)), [now])

  const showings = useMemo(() => {
    const cinemas = new Map(data.cinemas.map((c) => [c.id, c]))
    const rows = data.showtimes
      .filter((s) => s.movieId === movie.id && s.date === date && s.times.length)
      .flatMap((s) => {
        const cinema = cinemas.get(s.cinemaId)
        if (!cinema) return []
        const next = s.times.find((t) => !isPastCinemaShow(date, t, now))
        return [{ cinema, times: s.times, next, km: distanceFrom ? distanceKm(distanceFrom, cinema) : null }]
      })
    // Nearest first when we know where the viewer is; otherwise soonest upcoming show (finished days last)
    return rows.sort((a, b) =>
      a.km != null && b.km != null
        ? a.km - b.km
        : (a.next ? toMinutes(a.next) : 9999) - (b.next ? toMinutes(b.next) : 9999),
    )
  }, [data, movie.id, date, now, distanceFrom])

  const long = movie.synopsis.length > SYNOPSIS_CLAMP
  const synopsis = !long || expanded ? movie.synopsis : `${movie.synopsis.slice(0, SYNOPSIS_CLAMP).replace(/\s+\S*$/, '')}…`
  const hasAnyShowtimes = data.showtimes.some((show) => show.movieId === movie.id && show.times.length > 0)
  const availabilityMessage = movie.listingStatus === 'coming_soon'
    ? 'Showtimes have not been posted yet.'
    : movie.listingStatus === 'released_no_sessions'
      ? 'No upcoming showtimes are currently listed.'
      : movie.availabilityMessage || (movie.releaseDateLabel
        ? 'Showtimes have not been posted yet.'
        : 'No upcoming showtimes are currently confirmed.')

  return (
    <>
      {/* Hero: blurred poster backdrop + crisp poster */}
      <div className="relative shrink-0 overflow-hidden" onPointerDown={onHeroPointerDown} style={{ touchAction: onHeroPointerDown ? 'none' : undefined }}>
        <img src={movie.poster} alt="" aria-hidden draggable={false} className="absolute inset-0 size-full scale-125 object-cover opacity-60 blur-2xl" />
        <div className="absolute inset-0 bg-gradient-to-b from-ink-950/30 via-ink-900/60 to-ink-900" />
        <button
          type="button"
          onClick={onClose}
          onPointerDown={(e) => e.stopPropagation()}
          className="absolute top-4 right-4 z-10 grid size-9 place-items-center rounded-full bg-black/50 text-white backdrop-blur transition hover:rotate-90 hover:bg-black/70"
          aria-label="Close"
        >
          <X className="size-4" />
        </button>

        <div className="relative flex items-end gap-4 px-5 pt-10 pb-5">
          <motion.img
            key={movie.id}
            src={movie.poster}
            alt={`${movie.title} poster`}
            draggable={false}
            initial={{ opacity: 0, y: 16, rotate: -2 }}
            animate={{ opacity: 1, y: 0, rotate: 0 }}
            transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
            className="aspect-[2/3] w-[112px] shrink-0 rounded-xl object-cover shadow-[0_20px_40px_-12px_rgb(0_0_0/0.8)] ring-1 ring-white/15 sm:w-[124px]"
          />
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="min-w-0 pb-1"
          >
            {movie.rating && <span className="inline-block rounded-md border border-white/25 bg-black/30 px-1.5 py-0.5 text-[10px] font-bold text-white">{movie.rating}</span>}
            <h2 className="mt-2 font-display text-[40px] leading-[0.9] tracking-wide text-white drop-shadow-lg">{movie.title}</h2>
            {!!movie.genre.length && <p className="mt-2 text-xs font-semibold text-gold-300">{movie.genre.join(' · ')}</p>}
            {(movie.duration > 0 || movieLanguageLabel(movie) || movie.releaseDateLabel || movie.releaseYear > 0) && (
              <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-medium text-neutral-300">
                {movie.duration > 0 && <span className="inline-flex items-center gap-1"><Clock className="size-3" />{formatDuration(movie.duration)}</span>}
                {movieLanguageLabel(movie) && <span className="inline-flex items-center gap-1"><Languages className="size-3" />{movieLanguageLabel(movie)}</span>}
                {(movie.releaseDateLabel || movie.releaseYear > 0) && <span className="inline-flex items-center gap-1"><CalendarDays className="size-3" />{movie.releaseDateLabel || movie.releaseYear}</span>}
              </p>
            )}
          </motion.div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="space-y-4 px-5 pt-1">
          {movie.trailerUrl && (
            <a
              href={movie.trailerUrl}
              target="_blank"
              rel="noreferrer"
              className="group flex items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-gold-300 to-gold-500 px-4 py-3 text-sm font-bold text-ink-950 shadow-glow transition hover:brightness-110 active:scale-[0.98]"
            >
              <Play className="size-4 fill-current transition-transform group-hover:scale-110" />
              Watch Trailer
            </a>
          )}

          {movie.synopsis && (
            <div>
              <p className="text-sm leading-relaxed text-neutral-300">{synopsis}</p>
              {long && (
                <button type="button" onClick={() => setExpanded((e) => !e)} className="mt-1 text-xs font-bold text-gold-300 transition hover:text-gold-200">
                  {expanded ? 'Show less' : 'Read more'}
                </button>
              )}
            </div>
          )}

          {(movie.director || movie.cast.length > 0) && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-3 text-xs">
              {movie.director && <><dt className="font-semibold text-neutral-500">Director</dt><dd className="font-semibold text-neutral-200">{movie.director}</dd></>}
              {movie.cast.length > 0 && <><dt className="font-semibold text-neutral-500">Cast</dt><dd className="text-neutral-300">{movie.cast.join(', ')}</dd></>}
            </dl>
          )}

          {!hasAnyShowtimes && (
            <div className="rounded-2xl border border-gold-300/20 bg-gold-300/[0.06] px-4 py-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gold-300">
                {movie.listingStatus === 'coming_soon' ? 'Coming soon' : movie.listingStatus === 'released_no_sessions' ? 'Released · no sessions' : 'Showtimes not confirmed'}
              </p>
              <p className="mt-2 text-sm leading-relaxed text-neutral-200">{availabilityMessage}</p>
              {movie.listingStatus === 'coming_soon' && (
                <AdvanceBookingSignup movieTitle={movie.title} cinemas={data.cinemas} />
              )}
            </div>
          )}
        </div>

        {hasAnyShowtimes && (
          <div className="sticky top-0 z-10 mt-5 border-y border-white/5 bg-ink-900/85 px-4 py-2 backdrop-blur-xl">
            <DateSelector options={days} value={date} onChange={onDateChange} layoutId="movie-date" />
          </div>
        )}

        {hasAnyShowtimes && <div className="px-4 pt-4 pb-8">
          <div className="mb-3 flex items-baseline justify-between px-1">
            <h3 className="font-display text-2xl tracking-wider text-white">
              Showing at <span className="text-gold-400">{showings.length}</span> {showings.length === 1 ? 'cinema' : 'cinemas'}
            </h3>
            <span className="text-[11px] font-medium text-neutral-500">{distanceFrom ? 'Nearest first' : 'Soonest first'}</span>
          </div>

          <AnimatePresence mode="wait">
            <motion.ul key={`${movie.id}-${date}`} variants={list} initial="hidden" animate="show" exit={{ opacity: 0, transition: { duration: 0.15 } }} className="space-y-2">
              {showings.length ? (
                showings.map(({ cinema, times, km }) => (
                  <motion.li key={cinema.id} variants={item}>
                    <div className="group rounded-2xl border border-white/[0.06] bg-white/[0.03] p-3 transition-colors hover:border-white/12 hover:bg-white/[0.05]">
                      <button type="button" onClick={() => onSelectCinema(cinema.id)} className="flex w-full items-center gap-3 text-left" aria-label={`Open ${cinema.name} on the map`}>
                        <img src={cinema.image} alt="" loading="lazy" className="size-11 shrink-0 rounded-xl object-cover ring-1 ring-white/[0.08]" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-bold text-white">{cinema.name}</span>
                          <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-neutral-500">
                            {cinema.area}
                            {km != null && (
                              <>
                                <span className="text-neutral-700">•</span>
                                <span className="inline-flex items-center gap-1 font-semibold text-sky-300">
                                  <Navigation2 className="size-2.5 rotate-45" />
                                  {formatDistance(km)}
                                </span>
                              </>
                            )}
                          </span>
                        </span>
                        <ChevronRight className="size-4 shrink-0 text-neutral-600 transition group-hover:translate-x-0.5 group-hover:text-gold-300" />
                      </button>
                      <div className="mt-3">
                        <ShowtimeChips date={date} times={times} now={now} size="sm" onSelect={(t) => onSelectTime(cinema, movie.id, t)} />
                      </div>
                    </div>
                  </motion.li>
                ))
              ) : (
                <li className="flex flex-col items-center rounded-2xl border border-white/[0.06] bg-white/[0.03] px-6 py-10 text-center">
                  <Clapperboard className="mb-3 size-8 text-neutral-600" />
                  <p className="font-semibold text-neutral-300">Not showing on this day</p>
                  <p className="mt-1 text-sm text-neutral-500">Pick another date above.</p>
                </li>
              )}
            </motion.ul>
          </AnimatePresence>
        </div>}
      </div>
    </>
  )
}
