/*
 * Single boundary for cinema data.
 * - VITE_CINEMA_API_URL points to FilmUp's API base, e.g. /api.
 * - Supports FastAPI wrapped responses and raw PostgREST arrays.
 * - There is deliberately no demo-data fallback.
 */
import type { Area, Cinema, Movie, RawCinemaData, RawShowtime } from '../types'
import { sortCinemaDayTimes } from '../lib/schedule'
import { cinemaScheduleDate } from '../lib/cinemaDay'
import cinemasJson from '../data/cinemas.json'
import areasJson from '../data/areas.json'

export interface CinemaDataSource {
  load(): Promise<RawCinemaData>
}

interface ApiShowtime {
  movie_title?: string
  cinema_name?: string | null
  showtime?: string | null
  showtime_lahore?: string | null
  booking_link?: string | null
  ticket_price?: number | string | null
}

interface ApiCatalogueMovie {
  id: string
  source?: string
  cinema_name?: string
  movie_title: string
  listing_status?: string
  display_status?: string
  release_date?: string | null
  source_release_label?: string | null
  poster_url?: string | null
  trailer_url?: string | null
  language?: string | null
  movie_format?: string | null
  availability_message?: string | null
  has_upcoming_showtimes?: boolean
  showtimes?: ApiShowtime[]
}

interface ApiCatalogueResponse {
  count?: number
  movies?: ApiCatalogueMovie[]
}

interface ApiShowtimesResponse {
  count?: number
  showtimes?: ApiShowtime[]
}

const LAHORE_TIME_ZONE = 'Asia/Karachi'
const cinemas = cinemasJson as Cinema[]

function titleKey(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\([^)]*\)/g, ' ')
    .replace(/\b(?:18\s*\+|16\s*\+|13\s*\+|2d|3d|4dx|imax|urdu|english|hindi|punjabi|dubbed|subtitled)\b/gi, ' ')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ')
}

function displayTitle(raw: string): string {
  return raw
    .replace(/^\s*(?:18\s*\+|16\s*\+|13\s*\+)\s*/i, '')
    .replace(/\s*\((?:(?:2d|3d|4dx|imax|urdu|english|hindi|punjabi)\s*)+\)\s*$/i, '')
    .replace(/\s+(?:(?:2d|3d|4dx|imax|urdu|english|hindi|punjabi)\s*)+$/i, '')
    .trim()
}

function stableMovieId(title: string): string {
  return titleKey(title).replace(/\s+/g, '-') || 'unknown-movie'
}

function cinemaIdFromName(name: string | null | undefined): string | null {
  if (!name) return null
  if (/\b(?:multan|sargodha|karachi|islamabad|rawalpindi|faisalabad|gujranwala|gujrat|hyderabad|peshawar|quetta|sialkot|bahawalpur|abbottabad)\b/i.test(name)) return null
  const value = name.toLowerCase().replace(/[^a-z0-9]+/g, '')

  if (value.includes('cuecinema')) return 'cue-cinemas-gulberg'
  if (value.includes('chalocinema')) return 'chalo-cinema-fortress'
  if (value.includes('universalcinema')) return 'universal-cinemas-emporium'
  if (value.includes('cinepax') && value.includes('amanah')) return 'cinepax-amanah-mall'
  if (value.includes('cinepax') && (value.includes('package') || value.includes('mall'))) return 'cinepax-packages-mall'
  if (value.includes('cinestar') && value.includes('xinhua')) return 'cinestar-xinhua-mall'
  if (value.includes('cinestar') && value.includes('township')) return 'cinestar-township'
  if (value.includes('cinestar') && (value.includes('dha') || value.includes('raya'))) return 'cinestar-dha-raya'
  if (value.includes('raiha') || value.includes('cinegold')) return 'raiha-cinegold-plex'
  if (value.includes('dhacinema')) return 'dha-cinema'

  // Prefer an exact name match for other configured venues.
  const exact = cinemas.find((cinema) => cinema.name.toLowerCase().replace(/[^a-z0-9]+/g, '') === value)
  return exact?.id ?? null
}

function lahoreDateAndTime(raw: string): { date: string; time: string } | null {
  const parsed = new Date(raw)
  if (!Number.isFinite(parsed.getTime())) return null
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: LAHORE_TIME_ZONE,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(parsed)
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const year = part('year')
  const month = part('month')
  const day = part('day')
  const hour = part('hour')
  const minute = part('minute')
  if (!year || !month || !day || !hour || !minute) return null
  return { date: `${year}-${month}-${day}`, time: `${hour}:${minute}` }
}

function formatReleaseDate(raw: string | null | undefined): string | undefined {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw.slice(0, 10))) return undefined
  const [year, month, day] = raw.slice(0, 10).split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day, 12))
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric',
  }).format(date)
}

function cleanReleaseLabel(raw: string | null | undefined): string | undefined {
  const cleaned = raw?.replace(/^\s*release\s*:?\s*/i, '').trim()
  return cleaned || undefined
}

function inferListingStatus(
  record: ApiCatalogueMovie,
  hasSessions: boolean,
): Movie['listingStatus'] {
  if (hasSessions) return 'now_showing'
  const releaseDate = record.release_date?.slice(0, 10)
  if (releaseDate && /^\d{4}-\d{2}-\d{2}$/.test(releaseDate)) {
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAHORE_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date())
    return releaseDate > today ? 'coming_soon' : 'released_no_sessions'
  }
  if (record.display_status === 'coming_soon' || record.listing_status === 'coming_soon') return 'coming_soon'
  if (record.display_status === 'released_no_sessions' || record.listing_status === 'released_no_sessions') return 'released_no_sessions'
  return 'unknown'
}

/** Adapts GET /api/movies from the existing FastAPI app to this website's data model. */
export function createCinemaApiSource(baseUrl: string): CinemaDataSource {
  return {
    async load() {
      const apiBase = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl
      const moviesEndpoint = apiBase + '/movies?limit=1000'
      const showtimesEndpoint = apiBase + '/showtimes?limit=2000'

      let moviesResponse: Response
      let showtimesResponse: Response
      try {
        ;[moviesResponse, showtimesResponse] = await Promise.all([
          fetch(moviesEndpoint, { headers: { Accept: 'application/json' } }),
          fetch(showtimesEndpoint, { headers: { Accept: 'application/json' } }),
        ])
      } catch {
        throw new Error("Can't reach the cinema API at " + apiBase + ". Check VITE_CINEMA_API_URL and the backend deployment.")
      }

      if (!moviesResponse.ok) {
        throw new Error('Cinema API returned HTTP ' + moviesResponse.status + ' for ' + moviesEndpoint)
      }
      if (!showtimesResponse.ok) {
        throw new Error('Cinema API returned HTTP ' + showtimesResponse.status + ' for ' + showtimesEndpoint)
      }

      const moviePayload = await moviesResponse.json() as ApiCatalogueResponse | ApiCatalogueMovie[]
      const showtimePayload = await showtimesResponse.json() as ApiShowtimesResponse | ApiShowtime[]
      const catalogueRecords = Array.isArray(moviePayload) ? moviePayload : moviePayload?.movies
      const sharedShowtimes = Array.isArray(showtimePayload) ? showtimePayload : showtimePayload?.showtimes

      if (!Array.isArray(catalogueRecords) || !Array.isArray(sharedShowtimes)) {
        throw new Error('Cinema API returned an unexpected response. Expected movie and showtime arrays.')
      }

      // FastAPI wraps catalogue data while preserving the existing API response shape.
      const showtimesByTitle = new Map<string, ApiShowtime[]>()
      for (const row of sharedShowtimes) {
        if (!row?.movie_title?.trim()) continue
        const key = titleKey(row.movie_title)
        const rows = showtimesByTitle.get(key) ?? []
        rows.push(row)
        showtimesByTitle.set(key, rows)
      }

      // Track which configured venues actually appeared in the live catalogue.
      // A future session also counts as a record if a venue's catalogue card is missing.
      const cinemaIdsWithRecords = new Set<string>()
      // Merge catalogue rows by normalized title. This supports expanding the catalogue
      // to multiple cinema sources without duplicating the same film in the UI.
      const merged = new Map<string, { movie: Movie; raw: ApiCatalogueMovie[]; rawShowtimes: ApiShowtime[] }>()
      for (const record of catalogueRecords) {
        const observedCinemaId = cinemaIdFromName(record?.cinema_name)
        if (observedCinemaId) cinemaIdsWithRecords.add(observedCinemaId)
        if (!record?.movie_title?.trim()) continue
        const title = displayTitle(record.movie_title)
        const key = titleKey(title) || titleKey(record.movie_title)
        if (!key) continue
        const id = stableMovieId(title)
        const releaseDate = record.release_date?.slice(0, 10) || undefined
        const rating = /^(18\s*\+|16\s*\+|13\s*\+)/i.exec(record.movie_title)?.[1]?.replace(/\s+/g, '') ?? ''
        const movie: Movie = {
          id,
          title,
          poster: record.poster_url || '/posters/no-poster.svg',
          genre: [],
          duration: 0,
          language: record.language || '',
          languageByCinema: observedCinemaId ? { [observedCinemaId]: (record.language ?? '').trim() } : {},
          rating,
          synopsis: '',
          director: '',
          cast: [],
          releaseYear: releaseDate ? Number(releaseDate.slice(0, 4)) : 0,
          trailerUrl: record.trailer_url || undefined,
          releaseDate,
          releaseDateLabel: formatReleaseDate(releaseDate) || cleanReleaseLabel(record.source_release_label),
          listingStatus: record.display_status || record.listing_status || 'unknown',
          availabilityMessage: record.availability_message || undefined,
          hasUpcomingShowtimes: false,
        }

        const recordShowtimes = [
          ...(record.showtimes ?? []),
          ...(showtimesByTitle.get(titleKey(record.movie_title)) ?? []),
        ]
        const existing = merged.get(key)
        if (!existing) {
          merged.set(key, { movie, raw: [record], rawShowtimes: recordShowtimes })
          continue
        }
        existing.raw.push(record)
        if (observedCinemaId) {
          const languages = existing.movie.languageByCinema ?? {}
          const incomingLanguage = (record.language ?? '').trim()
          if (!Object.prototype.hasOwnProperty.call(languages, observedCinemaId) || (!languages[observedCinemaId] && incomingLanguage)) {
            existing.movie.languageByCinema = { ...languages, [observedCinemaId]: incomingLanguage }
          }
        }
        existing.rawShowtimes.push(...recordShowtimes)
        if (!existing.movie.poster || existing.movie.poster === '/posters/no-poster.svg') existing.movie.poster = movie.poster
        if (!existing.movie.trailerUrl && movie.trailerUrl) existing.movie.trailerUrl = movie.trailerUrl
        if (!existing.movie.language && movie.language) existing.movie.language = movie.language
        if (!existing.movie.rating && movie.rating) existing.movie.rating = movie.rating
        if (!existing.movie.releaseDate && movie.releaseDate) {
          existing.movie.releaseDate = movie.releaseDate
          existing.movie.releaseDateLabel = movie.releaseDateLabel
          existing.movie.releaseYear = movie.releaseYear
        }
        if (existing.movie.listingStatus !== 'now_showing' && movie.listingStatus === 'now_showing') {
          existing.movie.listingStatus = 'now_showing'
        }
      }

      const scheduleGroups = new Map<string, RawShowtime>()
      for (const [, entry] of merged) {
        const movieId = entry.movie.id
        const seenSessions = new Set<string>()
        for (const row of entry.rawShowtimes) {
          if (!row.showtime) continue
          const cinemaId = cinemaIdFromName(row.cinema_name)
          if (!cinemaId) continue
          const local = lahoreDateAndTime(row.showtime)
          if (!local) continue
          const uniqueSession = `${cinemaId}|${local.date}|${local.time}`
          if (seenSessions.has(uniqueSession)) continue
          seenSessions.add(uniqueSession)
          const scheduleDate = cinemaScheduleDate(local.date, local.time)
          const groupKey = `${cinemaId}|${movieId}|${scheduleDate}`
          const group = scheduleGroups.get(groupKey) ?? {
            cinemaId, movieId, date: scheduleDate, times: [],
          }
          group.times.push(local.time)
          scheduleGroups.set(groupKey, group)
        }

        entry.movie.hasUpcomingShowtimes = seenSessions.size > 0
        const representative: ApiCatalogueMovie = {
          ...entry.raw[0],
          release_date: entry.movie.releaseDate || entry.raw.find((record) => record.release_date)?.release_date,
          source_release_label: entry.raw.find((record) => record.source_release_label)?.source_release_label,
          listing_status: entry.raw.some((record) => record.listing_status === 'coming_soon') ? 'coming_soon' : entry.raw[0].listing_status,
          display_status: entry.raw.some((record) => record.display_status === 'coming_soon') ? 'coming_soon' : entry.raw[0].display_status,
        }
        entry.movie.listingStatus = inferListingStatus(representative, seenSessions.size > 0)
        if (entry.movie.hasUpcomingShowtimes) {
          entry.movie.availabilityMessage = 'Upcoming showtimes are available.'
        } else if (entry.movie.listingStatus === 'coming_soon') {
          entry.movie.availabilityMessage = entry.movie.releaseDateLabel
            ? `Release date: ${entry.movie.releaseDateLabel}. Showtimes have not been posted yet.`
            : 'Coming soon. The release date or showtimes have not been announced.'
        } else if (entry.movie.listingStatus === 'released_no_sessions') {
          entry.movie.availabilityMessage = entry.movie.releaseDateLabel
            ? `Released on ${entry.movie.releaseDateLabel}, but no upcoming showtimes are currently listed.`
            : 'Already released, but no upcoming showtimes are currently listed.'
        } else {
          entry.movie.availabilityMessage = 'No upcoming showtimes are currently confirmed.'
        }
      }

      const showtimes = [...scheduleGroups.values()].map((show) => ({
        ...show,
        times: sortCinemaDayTimes(show.times),
      }))
      const movies = [...merged.values()].map((entry) => entry.movie)
      const cinemaIdsWithSessions = new Set(showtimes.map((show) => show.cinemaId))
      const cinemasWithRecords = cinemas.map((cinema) => ({
        ...cinema,
        hasRecords: cinemaIdsWithRecords.has(cinema.id) || cinemaIdsWithSessions.has(cinema.id),
      }))

      return {
        // Map coordinates remain curated local data; record availability is live.
        cinemas: cinemasWithRecords,
        movies,
        showtimes,
        areas: areasJson as Area[],
      }
    },
  }
}

// Always use the real FastAPI catalogue. There is deliberately no demo-data fallback.
const catalogueApiUrl = (import.meta.env.VITE_CINEMA_API_URL as string | undefined)?.trim() || '/api'
export const dataSource: CinemaDataSource = createCinemaApiSource(catalogueApiUrl)
