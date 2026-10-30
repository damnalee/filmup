import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { List, X, ChevronsLeft, ChevronsRight } from 'lucide-react'
import type { Area, Cinema, CinemaData, Filters as FilterState, LatLng } from './types'
import { useCinemaData } from './hooks/useCinemaData'
import { useGeolocation } from './hooks/useGeolocation'
import { useIsDesktop } from './hooks/useMediaQuery'
import { useNow } from './hooks/useNow'
import { dateOptions, formatTime, hasActiveFilters, querySchedule, cinemaDaySortMinutes } from './lib/schedule'
import { isPastCinemaShow, toCinemaDate } from './lib/cinemaDay'
import { distanceKm } from './lib/geo'
import type { MapHandle, MapPadding } from './components/Map'
import BrowsePanel, { buildBrowseItems, type BrowseItem, type BrowseView, type NextShow } from './components/BrowsePanel'
import SearchBar, { type SearchMode } from './components/SearchBar'
import { DateChip, FiltersButton } from './components/Filters'
import NowShowing from './components/NowShowing'
import NearMeButton from './components/NearMeButton'
import Intro from './components/Intro'
import Toast, { type ToastData } from './components/Toast'
import { loadMapModule, prefetchMap } from './lib/mapAssets'
import { useMoving } from './hooks/useMoving'

// Heavy / on-demand parts load as separate chunks (MapLibre alone is ~1 MB)
const CinemaMap = lazy(loadMapModule)
const CinemaPanel = lazy(() => import('./components/CinemaPanel'))
const MoviePanel = lazy(() => import('./components/MoviePanel'))

const ease = [0.22, 1, 0.36, 1] as const

/* Layout constants (8px grid). Desktop = ≥1024px. */
const G = 16 // outer gutter
const BAR_H = 56
const LIST_W = 344
const PANEL_W = 440
const STRIP_H = 128

type Origin = LatLng & { label: string }

export default function App() {
  const state = useCinemaData()
  /** Flips when the intro video or title card finishes: the map flies in and the UI enters */
  const [revealed, setRevealed] = useState(false)
  /** false when the intro was skipped (already seen this session): no camera swoop either */
  const [introPlayed, setIntroPlayed] = useState(false)
  const onIntroFinish = useCallback((played: boolean) => {
    setIntroPlayed(played)
    setRevealed(true)
  }, [])
  return (
    <>
      <Intro onStart={prefetchMap} onFinish={onIntroFinish} />
      {/* Static grain, only once the intro is gone (never composited over the video) */}
      {revealed && <div className="grain" aria-hidden />}
      {/* The app UI mounts when the intro ends, so nothing lays out (or re-lays out when fonts
          arrive) underneath the video; its entrance animations then play from mount */}
      {state.status === 'ready' ? (
        revealed && <MapExperience data={state.data} revealed={revealed} introPlayed={introPlayed} />
      ) : state.status === 'error' ? (
        <div className="grid h-full place-items-center p-6 text-center">
          <div>
            <p className="font-display text-4xl tracking-wide text-white">Projector jammed</p>
            <p className="mt-2 text-sm text-neutral-400">{state.error}</p>
          </div>
        </div>
      ) : null}
    </>
  )
}

function MapExperience({ data, revealed, introPlayed }: { data: CinemaData; revealed: boolean; introPlayed: boolean }) {
  const now = useNow()
  const today = toCinemaDate(now)
  const isDesktop = useIsDesktop()
  const geo = useGeolocation()
  const mapRef = useRef<MapHandle>(null)
  const barRef = useRef<HTMLDivElement>(null)

  const initialFilters: FilterState = { date: today, query: '', movieId: null, genre: null, cinemaId: null, timesOfDay: [] }
  const [filters, setFilters] = useState<FilterState>(initialFilters)
  /** null until the user picks a tab: the list panel then shows all cinemas */
  const [searchMode, setSearchMode] = useState<SearchMode | null>(null)
  /** Search text while no tab is picked (filters the cinema list) */
  const [cinemaText, setCinemaText] = useState('')
  /** Keyboard cursor in the list panel (arrow keys in the search input) */
  const [cursor, setCursor] = useState(0)
  const [, setLocationText] = useState('')
  const [origin, setOrigin] = useState<Origin | null>(null)
  /** Highlighted on the map immediately… */
  const [selectedId, setSelectedId] = useState<string | null>(null)
  /** …panel opens once the camera arrives */
  const [panelId, setPanelId] = useState<string | null>(null)
  /** Movie Detail panel */
  const [moviePanelId, setMoviePanelId] = useState<string | null>(null)
  /** Set when a cinema was opened from the Movie panel, so it can offer "Back to movie" */
  const [backToMovieId, setBackToMovieId] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(true) // desktop sidebar
  const [sheetOpen, setSheetOpen] = useState(false) // mobile results sheet
  const [barFocused, setBarFocused] = useState(false)
  const [toast, setToast] = useState<ToastData | null>(null)
  // Create the map a beat after the UI mounts, so the two never land in the same frame
  const [mapReady, setMapReady] = useState(false)
  useEffect(() => {
    if (!revealed) return
    const t = setTimeout(() => setMapReady(true), 250)
    return () => clearTimeout(t)
  }, [revealed])
  const headerMotion = useMoving()
  const listMotion = useMoving()
  const stripMotion = useMoving()
  // Lazy panels mount on first use and stay mounted so their exit animations still play
  const [usedCinemaPanel, setUsedCinemaPanel] = useState(false)
  const [usedMoviePanel, setUsedMoviePanel] = useState(false)

  const patch = useCallback((p: Partial<FilterState>) => setFilters((f) => ({ ...f, ...p })), [])
  const dismissToast = useCallback(() => setToast(null), [])

  useEffect(() => {
    if (panelId) setUsedCinemaPanel(true)
    if (moviePanelId) setUsedMoviePanel(true)
  }, [panelId, moviePanelId])

  // Roll the date forward if the app is left open past midnight
  useEffect(() => {
    setFilters((f) => (f.date < today ? { ...f, date: today } : f))
  }, [today])

  // ── derived data ────────────────────────────────────────────
  const dates = useMemo(() => dateOptions(today), [today])
  const filtering = hasActiveFilters(filters)
  const matches = useMemo(() => querySchedule(data, filters), [data, filters])
  const genres = useMemo(() => [...new Set(data.movies.flatMap((m) => m.genre))].sort(), [data])
  const byId = useMemo(() => new Map(data.cinemas.map((c) => [c.id, c])), [data])
  const movieTitle = useMemo(() => new Map(data.movies.map((m) => [m.id, m.title])), [data])

  const { cinemaCount, nextShow } = useMemo(() => {
    const cinemaCount = new Map<string, number>()
    const nextShow = new Map<string, NextShow>()
    for (const s of data.showtimes) {
      if (s.date !== filters.date) continue
      cinemaCount.set(s.movieId, (cinemaCount.get(s.movieId) ?? 0) + 1)
      const t = s.times.find((x) => !isPastCinemaShow(s.date, x, now))
      const prev = nextShow.get(s.cinemaId)
      if (t && (!prev || cinemaDaySortMinutes(t) < cinemaDaySortMinutes(prev.time))) nextShow.set(s.cinemaId, { time: t, title: movieTitle.get(s.movieId) ?? '' })
    }
    return { cinemaCount, nextShow }
  }, [data, filters.date, now, movieTitle])

  /** Every movie, most widely showing (on the selected date) first */
  const moviesByReach = useMemo(
    () => [...data.movies].sort((a, b) => (cinemaCount.get(b.id) ?? 0) - (cinemaCount.get(a.id) ?? 0) || a.title.localeCompare(b.title)),
    [data, cinemaCount],
  )
  const moviesWithSessions = useMemo(() => new Set(data.showtimes.map((s) => s.movieId)), [data.showtimes])
  const nowShowing = useMemo(
    () => moviesByReach.filter((m) => moviesWithSessions.has(m.id)),
    [moviesByReach, moviesWithSessions],
  )
  const comingSoonMovies = useMemo(
    () => moviesByReach.filter((m) => !moviesWithSessions.has(m.id) && m.listingStatus === 'coming_soon'),
    [moviesByReach, moviesWithSessions],
  )
  const upcoming = useCallback((times: string[]) => times.filter((t) => !isPastCinemaShow(filters.date, t, now)), [filters.date, now])

  const panelCinema = (panelId && byId.get(panelId)) || null
  const panelMovie = data.movies.find((m) => m.id === moviePanelId) ?? null
  const backMovie = data.movies.find((m) => m.id === backToMovieId) ?? null
  const anyPanel = !!panelId || !!moviePanelId
  const panelOpen = isDesktop && (!!panelCinema || !!panelMovie)
  const showTimes = Boolean(filters.movieId || filters.query.trim())
  const distanceFrom: LatLng | null = origin ?? geo.location
  const pickedMovie = data.movies.find((m) => m.id === filters.movieId) ?? null

  // ── layout: what each UI layer covers, so the camera keeps targets visible ──
  const rightInset = panelOpen ? PANEL_W + G * 2 : G
  const paddingFor = useCallback(
    (panel: boolean): MapPadding =>
      isDesktop
        ? { top: G + BAR_H, bottom: STRIP_H + G, left: listOpen ? LIST_W + G * 2 : G, right: panel ? PANEL_W + G * 2 : G }
        : { top: 112, bottom: panel ? Math.round(window.innerHeight * 0.6) : STRIP_H + 24, left: 0, right: 0 },
    [isDesktop, listOpen],
  )
  // On mobile the results sheet covers the lower ~60% just like the detail panel does
  const mapPadding = useMemo(() => paddingFor(anyPanel || (!isDesktop && sheetOpen)), [paddingFor, anyPanel, isDesktop, sheetOpen])

  // Frame the matching cinemas whenever the filtered set changes
  const matchKey = matches.map((m) => m.cinema.id).sort().join(',')
  useEffect(() => {
    if (!filtering || selectedId || !matches.length) return
    mapRef.current?.fitPoints(matches.map((m) => m.cinema))
    // Deliberately keyed on the set of ids, not the array identity
  }, [matchKey, filtering])

  // Frame the user + the three nearest cinemas once a location fix arrives
  useEffect(() => {
    if (!geo.location) return
    const loc = geo.location
    const nearest = [...data.cinemas].sort((a, b) => distanceKm(loc, a) - distanceKm(loc, b)).slice(0, 3)
    mapRef.current?.fitPoints([loc, ...nearest])
    setOrigin(null)
  }, [geo.location, data.cinemas])

  // Esc closes the top-most panel: the cinema first, then the movie
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (panelId || selectedId) {
        setPanelId(null)
        setSelectedId(null)
        setBackToMovieId(null)
      } else setMoviePanelId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [panelId, selectedId])

  // ── actions ─────────────────────────────────────────────────
  const selectCinema = (id: string, fromMovieId: string | null = null) => {
    const cinema = byId.get(id)
    if (!cinema || cinema.hasRecords === false) return
    setSheetOpen(false)
    setMoviePanelId(null)
    setBackToMovieId(fromMovieId)
    setSelectedId(id)
    setPanelId(null)
    mapRef.current?.flyToCinema(cinema, () => setPanelId(id), paddingFor(true))
  }
  const closePanel = () => {
    setPanelId(null)
    setSelectedId(null)
    setBackToMovieId(null)
  }

  /** Movie search + open its detail panel (dropdown, Now Showing strip, posters in the cinema panel) */
  const pickMovie = (id: string | null) => {
    const movie = data.movies.find((m) => m.id === id)
    setSearchMode('movie')
    setLocationText('')
    patch({ movieId: movie?.id ?? null, query: movie?.title ?? '' })
    closePanel()
    setSheetOpen(false)
    setMoviePanelId(movie?.id ?? null)
  }

  const backToMovie = () => {
    if (!backToMovieId) return
    const id = backToMovieId
    closePanel()
    setMoviePanelId(id)
    if (filters.movieId !== id) patch({ movieId: id, query: data.movies.find((m) => m.id === id)?.title ?? '' })
    // The match set hasn't changed, so the auto-framing effect won't fire — frame it here
    setTimeout(() => mapRef.current?.fitPoints(matches.map((m) => m.cinema)), 50)
  }

  const pickArea = (area: Area) => {
    setSearchMode('location')
    setSheetOpen(false)
    setLocationText(area.name)
    setOrigin({ lat: area.lat, lng: area.lng, label: area.name })
    closePanel()
    setMoviePanelId(null)
    mapRef.current?.flyToPoint(area, 14)
    if (!isDesktop) setTimeout(() => setSheetOpen(true), 900)
  }

  /** Tab change swaps the list panel's content; null = back to all cinemas */
  const changeMode = (mode: SearchMode | null) => {
    setSearchMode(mode)
    setMoviePanelId(null)
    setCinemaText('')
    if (mode !== 'movie') patch({ query: '', movieId: null })
    if (mode !== 'location') {
      setLocationText('')
      setOrigin(null)
    }
    if (isDesktop) setListOpen(true)
    else if (mode) setSheetOpen(true)
  }

  const clearSearch = () => {
    if (searchMode === 'movie') {
      setMoviePanelId(null)
      patch({ query: '', movieId: null })
    } else {
      setCinemaText('')
      setLocationText('')
      setOrigin(null)
    }
  }

  const onSearchText = (text: string) => {
    if (searchMode === 'movie') patch({ query: text, movieId: null })
    else {
      setCinemaText(text)
      setLocationText('')
      setOrigin(null)
    }
    if (isDesktop) setListOpen(true)
    else setSheetOpen(true)
  }

  const reset = () => {
    setFilters((f) => ({ ...initialFilters, date: f.date }))
    setOrigin(null)
    setLocationText('')
  }

  const onSelectTime = (cinema: Cinema, movieId: string, time: string) => {
    const day = dates.find((d) => d.iso === filters.date)
    setToast({
      id: Date.now(),
      title: `${movieTitle.get(movieId)} · ${formatTime(time)}`,
      subtitle: `${cinema.name} · ${day?.label === day?.weekday ? `${day?.weekday} ${day?.day} ${day?.month}` : day?.label}`,
      phone: cinema.phone || undefined,
    })
  }

  // ── list panel: content follows the search tab ─────────────
  const browseView: BrowseView = searchMode === 'movie' ? 'movies' : 'cinemas'
  const searchText = searchMode === 'movie' ? filters.query : cinemaText
  // A picked movie's title (or a picked area's name) sits in the input: list everything rather than one row
  const listQuery = browseView === 'cinemas' || (pickedMovie && filters.query === pickedMovie.title) ? '' : searchText
  const browseItems = useMemo(
    () =>
      buildBrowseItems({
        view: browseView,
        query: listQuery,
        movies: browseView === 'movies' ? nowShowing : moviesByReach,
        cinemaCount,
        areas: data.areas,
        cinemas: data.cinemas,
        matches,
        filtering,
        nextShow,
        upcoming,
        distanceFrom,
      }),
    [browseView, listQuery, moviesByReach, nowShowing, cinemaCount, data, matches, filtering, nextShow, upcoming, distanceFrom],
  )
  const dayLabel = dates.find((d) => d.iso === filters.date)?.label ?? ''
  const browseTitle = { cinemas: 'Cinemas', movies: 'Movies', locations: 'Locations', near: `Near ${origin?.label ?? ''}` }[browseView]
  const browseHint = {
    cinemas: distanceFrom ? 'Nearest first' : 'Top rated',
    movies: `Most showing first · ${dayLabel}`,
    locations: 'Lahore areas · pick one to see nearby cinemas',
    near: 'Nearest first',
  }[browseView]

  useEffect(() => setCursor(0), [browseView, listQuery, origin])

  const activate = (item: BrowseItem) => {
    if (item.kind === 'movie') pickMovie(item.movie.id)
    else if (item.kind === 'area') pickArea(item.area)
    else selectCinema(item.cinema.id)
  }
  const navigate = (key: 'up' | 'down' | 'enter' | 'escape') => {
    const n = browseItems.length
    if (key === 'down') setCursor((c) => (n ? (c + 1) % n : 0))
    else if (key === 'up') setCursor((c) => (n ? (c - 1 + n) % n : 0))
    else if (key === 'enter' && browseItems[cursor]) activate(browseItems[cursor])
    if (!isDesktop && (key === 'down' || key === 'up')) setSheetOpen(true)
  }
  const backToLocations = () => {
    setOrigin(null)
    setLocationText('')
  }

  const browsePanel = (size: 'md' | 'lg', headerExtra?: React.ReactNode) => (
    <BrowsePanel
      view={browseView}
      items={browseItems}
      title={browseTitle}
      hint={browseHint}
      onBack={browseView === 'cinemas' ? backToLocations : undefined}
      cursor={cursor}
      showCursor={barFocused}
      selectedCinemaId={selectedId}
      pickedMovieId={filters.movieId}
      hasOrigin={!!distanceFrom}
      onActivate={activate}
      size={size}
      headerExtra={headerExtra}
    />
  )

  const dateAndFilters = (
    <>
      <DateChip dates={dates} value={filters.date} onChange={(date) => patch({ date })} />
      <FiltersButton filters={filters} genres={genres} cinemas={data.cinemas} resultCount={matches.length} onChange={patch} onReset={reset} />
    </>
  )

  return (
    <div className="relative h-full overflow-hidden">
      {/* The map is only created once the intro has finished — nothing competes with video playback */}
      {mapReady && (
        <Suspense fallback={null}>
          <CinemaMap
        ref={mapRef}
        cinemas={data.cinemas}
        matches={matches}
        filtering={filtering}
        showTimes={showTimes}
        upcoming={upcoming}
        selectedId={selectedId}
        onSelect={selectCinema}
        userLocation={geo.location}
        origin={origin}
        padding={mapPadding}
        startIntro={introPlayed}
        controlsRight={isDesktop ? rightInset : 12}
          />
        </Suspense>
      )}
      {revealed && <div className="vignette" aria-hidden />}

      {/* ── Command bar ─────────────────────────────────────── */}
      <motion.header
        initial={{ y: -24, opacity: 0 }}
        animate={revealed ? { y: 0, opacity: 1 } : { y: -24, opacity: 0 }}
        transition={{ delay: 0.35, duration: 0.6, ease }}
        {...headerMotion}
        className="pointer-events-none fixed top-3 right-3 left-3 z-30 lg:top-4 lg:left-4"
        style={isDesktop ? { right: rightInset, transition: 'right 0.5s cubic-bezier(0.22,1,0.36,1)' } : undefined}
      >
        <div
          ref={barRef}
          className={`glass pointer-events-auto flex h-14 w-full items-center gap-2 rounded-2xl p-2 transition-shadow duration-300 lg:max-w-[880px] ${
            barFocused ? 'shadow-[var(--shadow-panel),0_0_0_1px_rgb(245_194_107/0.35),0_0_32px_-8px_rgb(245_194_107/0.45)]' : ''
          }`}
        >
          <Logo />
          <SearchBar
            mode={searchMode}
            onModeChange={changeMode}
            value={searchText}
            onChange={onSearchText}
            onNavigate={navigate}
            onClear={clearSearch}
            onFocusChange={setBarFocused}
          />
          {isDesktop && (
            <>
              <span className="mx-1 h-6 w-px shrink-0 bg-white/[0.08]" aria-hidden />
              <div className="flex shrink-0 items-center gap-2">{dateAndFilters}</div>
            </>
          )}
          <NearMeButton status={geo.status} onLocate={geo.locate} onClear={geo.clear} />
        </div>

        {!isDesktop && (
          <div className="pointer-events-auto mt-2 flex items-center gap-2">
            {dateAndFilters}
            <button type="button" onClick={() => setSheetOpen(true)} className="chip ml-auto glass" aria-label={`Show ${browseTitle.toLowerCase()} list`}>
              <List className="size-3.5" />
              <span>{browseItems.length}</span>
            </button>
          </div>
        )}

        <AnimatePresence>
          {geo.error && (
            <motion.p initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="pointer-events-auto mt-2 inline-block rounded-lg bg-ember-500/15 px-3 py-1.5 text-xs font-medium text-ember-400">
              {geo.error}
            </motion.p>
          )}
        </AnimatePresence>
      </motion.header>

      {/* ── Desktop results list ─────────────────────────────── */}
      {isDesktop && (
        <motion.aside
          initial={{ x: -24, opacity: 0 }}
          animate={{ x: listOpen ? 0 : -(LIST_W + G), opacity: revealed ? 1 : 0 }}
          transition={{ duration: 0.5, ease, opacity: { delay: 0.5 } }}
          {...listMotion}
          className="glass fixed z-20 flex flex-col rounded-2xl"
          style={{ top: G + BAR_H + G, bottom: G, left: G, width: LIST_W }}
        >
          {browsePanel('md')}
          <button
            type="button"
            onClick={() => setListOpen((o) => !o)}
            className="glass absolute top-1/2 -right-3 grid h-12 w-6 -translate-y-1/2 place-items-center rounded-lg text-neutral-400 transition hover:text-gold-300"
            style={{ right: listOpen ? -12 : -28 }}
            aria-label={listOpen ? 'Hide list panel' : 'Show list panel'}
          >
            {listOpen ? <ChevronsLeft className="size-3.5" /> : <ChevronsRight className="size-3.5" />}
          </button>
        </motion.aside>
      )}

      {/* ── Live Now Showing strip ──────────────────────────── */}
      <motion.div
        initial={{ y: 24, opacity: 0 }}
        animate={revealed ? { y: 0, opacity: 1 } : { y: 24, opacity: 0 }}
        transition={{ delay: 0.45, duration: 0.6, ease }}
        {...stripMotion}
        className="pointer-events-none fixed z-30"
        style={
          isDesktop
            ? { bottom: G, left: listOpen ? LIST_W + G * 2 : G, right: rightInset + G, transition: 'left 0.5s cubic-bezier(0.22,1,0.36,1), right 0.5s cubic-bezier(0.22,1,0.36,1)' }
            : { bottom: 'max(12px, env(safe-area-inset-bottom))', left: 12, right: 12 }
        }
      >
        <NowShowing revealed={revealed} movies={nowShowing} comingSoonMovies={comingSoonMovies} cinemaCount={cinemaCount} selectedId={filters.movieId} onSelect={pickMovie} />
      </motion.div>
      {/* ── Mobile results sheet ────────────────────────────── */}
      <AnimatePresence>
        {!isDesktop && sheetOpen && (
          <>
            <motion.div key="list-scrim" className="fixed inset-0 z-40 bg-black/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setSheetOpen(false)} />
            <motion.div
              key="list-sheet"
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ type: 'spring', stiffness: 300, damping: 34 }}
              drag="y"
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.6 }}
              onDragEnd={(_, i) => (i.offset.y > 120 || i.velocity.y > 600) && setSheetOpen(false)}
              className="glass fixed inset-x-0 bottom-0 z-50 flex max-h-[70dvh] flex-col rounded-t-3xl pb-[env(safe-area-inset-bottom)]"
            >
              <div className="flex justify-center pt-2">
                <span className="h-1 w-10 rounded-full bg-white/25" />
              </div>
              {/* Stop pointer events here so scrolling the list doesn't drag the sheet; the handle above still does */}
              <div className="flex min-h-0 flex-1 flex-col" onPointerDownCapture={(e) => e.stopPropagation()}>
                {browsePanel(
                  'lg',
                  <button type="button" onClick={() => setSheetOpen(false)} className="grid size-9 shrink-0 place-items-center rounded-full bg-white/[0.06] text-neutral-300" aria-label="Close list">
                    <X className="size-4" />
                  </button>,
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {(usedCinemaPanel || panelCinema) && (
        <Suspense fallback={null}>
          <CinemaPanel
        cinema={panelCinema}
        data={data}
        filters={filters}
        now={now}
        isDesktop={isDesktop}
        userLocation={distanceFrom}
        onDateChange={(date) => patch({ date })}
        onClose={closePanel}
        onSelectTime={onSelectTime}
        onMovieFilter={pickMovie}
        back={backMovie ? { label: backMovie.title, onBack: backToMovie } : null}
          />
        </Suspense>
      )}

      {(usedMoviePanel || panelMovie) && (
        <Suspense fallback={null}>
          <MoviePanel
        movie={panelMovie}
        data={data}
        date={filters.date}
        now={now}
        isDesktop={isDesktop}
        distanceFrom={distanceFrom}
        onDateChange={(date) => patch({ date })}
        onClose={() => setMoviePanelId(null)}
        onSelectCinema={(id) => selectCinema(id, moviePanelId)}
        onSelectTime={onSelectTime}
          />
        </Suspense>
      )}

      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  )
}

function Logo() {
  return (
    <a href="/" className="group flex shrink-0 items-center gap-2 pr-1" aria-label="FilmUp home">
      <span className="grid size-10 place-items-center rounded-xl bg-gradient-to-b from-white/[0.08] to-white/[0.02] ring-1 ring-gold-400/25">
        <svg viewBox="0 0 24 24" className="size-5 text-gold-400 transition-transform duration-700 group-hover:rotate-180" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="9" />
          <circle cx="12" cy="7.5" r="1.7" fill="currentColor" />
          <circle cx="12" cy="16.5" r="1.7" fill="currentColor" />
          <circle cx="7.5" cy="12" r="1.7" fill="currentColor" />
          <circle cx="16.5" cy="12" r="1.7" fill="currentColor" />
          <circle cx="12" cy="12" r="1" className="fill-ember-500 stroke-none" />
        </svg>
      </span>
      <span className="wordmark hidden text-[22px] lg:block">FilmUp</span>
    </a>
  )
}
