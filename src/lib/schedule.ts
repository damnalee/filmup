/* Pure date/time + schedule logic. No React, no DOM — it's shared with
   the Netlify chat function so the bot and the UI always agree. */
import type {
  CinemaData,
  CinemaSchedule,
  Filters,
  Movie,
  MovieShowing,
  RawCinemaData,
  Showtime,
  TimeOfDay,
} from '../types'

const pad = (n: number) => String(n).padStart(2, '0')

export function toISODate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function parseISODate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function addDays(iso: string, days: number): string {
  const d = parseISODate(iso)
  d.setDate(d.getDate() + days)
  return toISODate(d)
}

/** "+2" → today + 2 days, "2026-10-28" → unchanged, anything else → null */
export function resolveDate(value: string, todayISO: string): string | null {
  const rel = /^\+(\d+)$/.exec(value.trim())
  if (rel) return addDays(todayISO, Number(rel[1]))
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  return null
}

export function toMinutes(time: string): number {
  const [h, m] = time.split(':').map(Number)
  return h * 60 + m
}

/** Sort a cinema day with 00:00–05:59 after the previous evening's sessions. */
export function cinemaDaySortMinutes(time: string): number {
  const minutes = toMinutes(time)
  return minutes < 6 * 60 ? minutes + 24 * 60 : minutes
}

/** Deduplicate and sort showtimes in cinema-day order. */
export function sortCinemaDayTimes(times: string[]): string[] {
  return [...new Set(times)].sort((a, b) => cinemaDaySortMinutes(a) - cinemaDaySortMinutes(b))
}

export function formatTime(time: string): string {
  const [h, m] = time.split(':').map(Number)
  const suffix = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return `${h12}:${pad(m)} ${suffix}`
}

export const TIME_OF_DAY: { id: TimeOfDay; label: string; hint: string; from: number; to: number }[] = [
  { id: 'morning', label: 'Morning', hint: 'before 12 PM', from: 0, to: 12 * 60 },
  { id: 'afternoon', label: 'Afternoon', hint: '12 – 5 PM', from: 12 * 60, to: 17 * 60 },
  { id: 'evening', label: 'Evening', hint: '5 – 9 PM', from: 17 * 60, to: 21 * 60 },
  { id: 'night', label: 'Night', hint: 'after 9 PM', from: 21 * 60, to: 24 * 60 },
]

export function timeOfDay(time: string): TimeOfDay {
  const m = toMinutes(time)
  return TIME_OF_DAY.find((t) => m >= t.from && m < t.to)?.id ?? 'night'
}

export function isPastShow(dateISO: string, time: string, now: Date): boolean {
  const actualDateISO = toMinutes(time) < 6 * 60 ? addDays(dateISO, 1) : dateISO
  const today = toISODate(now)
  if (actualDateISO !== today) return actualDateISO < today
  return toMinutes(time) <= now.getHours() * 60 + now.getMinutes()
}

export function movieLanguageLabel(movie: Movie, cinemaId?: string): string {
  const byCinema = movie.languageByCinema ?? {}
  if (cinemaId && Object.prototype.hasOwnProperty.call(byCinema, cinemaId)) return (byCinema[cinemaId] ?? '').trim()
  const languages = [...new Set(Object.values(byCinema).map((value) => value.trim()).filter(Boolean))]
  return languages.length ? languages.join(' · ') : movie.language
}

export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h}h ${pad(m)}m` : `${m}m`
}

export interface DateOption {
  iso: string
  label: string
  weekday: string
  day: string
  month: string
}

export function dateOptions(todayISO: string, count = 7): DateOption[] {
  return Array.from({ length: count }, (_, i) => {
    const iso = addDays(todayISO, i)
    const d = parseISODate(iso)
    const weekday = d.toLocaleDateString('en-US', { weekday: 'short' })
    return {
      iso,
      weekday,
      day: String(d.getDate()),
      month: d.toLocaleDateString('en-US', { month: 'short' }),
      label: i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : weekday,
    }
  })
}

/** Resolve relative dates, drop broken references, sort + dedupe times. */
export function normalizeData(raw: RawCinemaData, todayISO: string): CinemaData {
  const cinemaIds = new Set(raw.cinemas.map((c) => c.id))
  const movieIds = new Set(raw.movies.map((m) => m.id))
  const merged = new Map<string, Showtime>()

  for (const s of raw.showtimes) {
    const date = resolveDate(s.date, todayISO)
    if (!date || !cinemaIds.has(s.cinemaId) || !movieIds.has(s.movieId)) continue
    const key = `${s.cinemaId}|${s.movieId}|${date}`
    const times = [...(merged.get(key)?.times ?? []), ...s.times].filter((t) => /^\d{2}:\d{2}$/.test(t))
    merged.set(key, {
      cinemaId: s.cinemaId,
      movieId: s.movieId,
      date,
      times: sortCinemaDayTimes(times),
    })
  }

  return { cinemas: raw.cinemas, movies: raw.movies, showtimes: [...merged.values()], areas: raw.areas }
}

export function movieMatchesQuery(title: string, query: string): boolean {
  const q = query.trim().toLowerCase()
  return !q || title.toLowerCase().includes(q)
}

/**
 * Apply filters to one day's schedule. Returns only cinemas with at least one
 * matching showing, each with the (time-of-day filtered) times per movie.
 */
export function querySchedule(data: CinemaData, filters: Filters): CinemaSchedule[] {
  const movies = new Map(data.movies.map((m) => [m.id, m]))
  const cinemas = new Map(data.cinemas.map((c) => [c.id, c]))
  const byCinema = new Map<string, CinemaSchedule>()

  for (const s of data.showtimes) {
    if (s.date !== filters.date) continue
    if (filters.cinemaId && s.cinemaId !== filters.cinemaId) continue
    if (filters.movieId && s.movieId !== filters.movieId) continue
    const movie = movies.get(s.movieId)
    const cinema = cinemas.get(s.cinemaId)
    if (!movie || !cinema) continue
    if (filters.genre && !movie.genre.includes(filters.genre)) continue
    if (!filters.movieId && !movieMatchesQuery(movie.title, filters.query)) continue

    const times = filters.timesOfDay.length
      ? s.times.filter((t) => filters.timesOfDay.includes(timeOfDay(t)))
      : s.times
    if (!times.length) continue

    let entry = byCinema.get(s.cinemaId)
    if (!entry) {
      entry = { cinema, showings: [] }
      byCinema.set(s.cinemaId, entry)
    }
    entry.showings.push({ movie, times })
  }

  return [...byCinema.values()]
}

/** Every movie playing at a cinema on a date, unfiltered, earliest first. */
export function cinemaDay(data: CinemaData, cinemaId: string, date: string): MovieShowing[] {
  const movies = new Map(data.movies.map((m) => [m.id, m]))
  return data.showtimes
    .filter((s) => s.cinemaId === cinemaId && s.date === date && s.times.length)
    .flatMap((s) => {
      const movie = movies.get(s.movieId)
      return movie ? [{ movie, times: s.times }] : []
    })
    .sort((a, b) => cinemaDaySortMinutes(a.times[0]) - cinemaDaySortMinutes(b.times[0]))
}

export function hasActiveFilters(f: Filters): boolean {
  return Boolean(f.query.trim() || f.movieId || f.genre || f.cinemaId || f.timesOfDay.length)
}
