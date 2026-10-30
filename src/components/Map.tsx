/* ──────────────────────────────────────────────────────────────
   True-3D map: MapLibre GL + OpenFreeMap vector tiles (no API key).

   - Dark style is restyled at load time and gets extruded buildings
   - Each cinema is a 3D light beam (fill-extrusion) + a floating HTML pin
   - Clustering uses supercluster on HTML markers, recomputed per half-zoom
   - Camera moves are imperative via the MapHandle ref, so App decides
     *when* the camera moves (select → fly → then open the panel)
   ────────────────────────────────────────────────────────────── */
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import MapGL, { Layer, Marker, Source, type MapRef } from 'react-map-gl/maplibre'
import type { FeatureCollection, Point, Polygon } from 'geojson'
import { setWorkerUrl, type ExpressionSpecification, type Map as MLMap } from 'maplibre-gl'
// MapLibre v6 resolves its worker relative to its own module URL, which breaks once Vite
// pre-bundles (dev) or hashes (build) the library. Hand it a URL Vite actually serves.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import Supercluster from 'supercluster'
import { Compass, Home, Minus, Plus, Box, RotateCcw, RotateCw, X } from 'lucide-react'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Cinema, CinemaSchedule, LatLng } from '../types'
import { formatTime } from '../lib/schedule'
import { installRotationGestures } from '../lib/mapGestures'
import { STYLE_URL } from '../lib/mapAssets'

setWorkerUrl(workerUrl)

const START = { longitude: 74.31, latitude: 31.5, zoom: 9.3, pitch: 0, bearing: 0 }
const CLOSE_UP = { center: [74.3466, 31.5155] as [number, number], zoom: 14.6, pitch: 66, bearing: -32 }
const HOME = { center: [74.322, 31.452] as [number, number], zoom: 11.35, pitch: 58, bearing: -18 }

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

export interface MapPadding {
  top: number
  right: number
  bottom: number
  left: number
}

export interface MapHandle {
  /**
   * Cinematic fly-in; `onArrive` fires once the camera settles (not if another move interrupts it).
   * `padding` overrides the current padding — pass the layout the UI is *about* to have (e.g. panel open).
   */
  flyToCinema(cinema: Cinema, onArrive?: () => void, padding?: MapPadding): void
  flyToPoint(point: LatLng, zoom?: number): void
  fitPoints(points: LatLng[]): void
  resetView(): void
}

interface Props {
  ref?: Ref<MapHandle>
  cinemas: Cinema[]
  matches: CinemaSchedule[]
  filtering: boolean
  showTimes: boolean
  upcoming: (times: string[]) => string[]
  selectedId: string | null
  onSelect: (id: string) => void
  userLocation: LatLng | null
  origin: (LatLng & { label: string }) | null
  padding: MapPadding
  /** The map mounts after the intro. true = play the camera swoop; false (intro skipped) = jump straight to the overview */
  startIntro: boolean
  /** Distance of the map controls from the right edge (moves left when the panel opens) */
  controlsRight: number
}

type PinState = 'normal' | 'match' | 'dim' | 'active'

const BEAM: Record<PinState, { color: string; height: number }> = {
  normal: { color: '#f5c26b', height: 520 },
  match: { color: '#ffd27f', height: 760 },
  dim: { color: '#4a4a57', height: 70 },
  active: { color: '#ff3b4e', height: 980 },
}

export default function CinemaMap(props: Props) {
  const { ref, cinemas, matches, filtering, showTimes, upcoming, selectedId, onSelect, userLocation, origin, padding, startIntro, controlsRight } = props
  const mapRef = useRef<MapRef>(null)
  const flyToken = useRef(0)
  const [zoom, setZoom] = useState(START.zoom)
  // The GL map instance once loaded — controls subscribe to it directly so rotating
  // doesn't re-render this component (and every marker) on each frame
  const [glMap, setGlMap] = useState<MLMap | null>(null)
  const hint = useRotateHint(4000)

  const pad = useCallback(
    (extra = 0) => ({ top: padding.top + extra, bottom: padding.bottom + extra, left: padding.left + extra, right: padding.right + extra }),
    [padding],
  )

  const handle = useMemo<MapHandle>(() => {
    const getMap = () => mapRef.current?.getMap()
    const h: MapHandle = {
      flyToCinema(c, onArrive, padding) {
        const map = getMap()
        if (!map) return onArrive?.()
        const token = ++flyToken.current
        // Swing the camera ~40° so every selection feels like a crane shot
        const nextBearing = ((map.getBearing() + 40 + 540) % 360) - 180
        map.flyTo({
          center: [c.lng, c.lat],
          zoom: Math.max(16, map.getZoom()),
          pitch: 64,
          bearing: nextBearing,
          padding: padding ?? pad(),
          duration: reducedMotion() ? 0 : 2200,
          curve: 1.5,
          essential: true,
        })
        map.once('moveend', () => token === flyToken.current && onArrive?.())
      },
      flyToPoint(p, z = 14.2) {
        flyToken.current++
        getMap()?.flyTo({ center: [p.lng, p.lat], zoom: z, pitch: 60, padding: pad(), duration: reducedMotion() ? 0 : 1900, essential: true })
      },
      fitPoints(points) {
        const map = getMap()
        if (!map || !points.length) return
        if (points.length === 1) return h.flyToPoint(points[0], 15)
        flyToken.current++
        const lngs = points.map((p) => p.lng)
        const lats = points.map((p) => p.lat)
        // cameraForBounds adds `padding` on top of the map's stored padding, so set the UI insets
        // first and only pass a small breathing margin — otherwise the insets count twice.
        map.setPadding(pad())
        const cam = map.cameraForBounds(
          [
            [Math.min(...lngs), Math.min(...lats)],
            [Math.max(...lngs), Math.max(...lats)],
          ],
          { padding: 48, maxZoom: 14.5, bearing: map.getBearing() },
        )
        if (cam) map.flyTo({ ...cam, padding: pad(), pitch: 50, duration: reducedMotion() ? 0 : 1700, essential: true })
      },
      resetView() {
        flyToken.current++
        getMap()?.flyTo({ ...HOME, padding: pad(), duration: reducedMotion() ? 0 : 1800, essential: true })
      },
    }
    return h
  }, [pad])
  useImperativeHandle(ref, () => handle, [handle])

  // ── derived state per cinema ────────────────────────────────
  const matchById = useMemo(() => new Map(matches.map((m) => [m.cinema.id, m])), [matches])
  const unavailableIds = useMemo(
    () => new Set(cinemas.filter((cinema) => cinema.hasRecords === false).map((cinema) => cinema.id)),
    [cinemas],
  )
  const stateOf = useCallback(
    (id: string): PinState => unavailableIds.has(id) ? 'dim' : (id === selectedId ? 'active' : filtering ? (matchById.has(id) ? 'match' : 'dim') : 'normal'),
    [selectedId, filtering, matchById, unavailableIds],
  )

  const beams = useMemo<FeatureCollection<Polygon>>(
    () => ({
      type: 'FeatureCollection',
      features: cinemas.flatMap((c) => {
        const s = BEAM[stateOf(c.id)]
        return [
          { type: 'Feature' as const, properties: { kind: 'core', ...s }, geometry: circle(c, 16) },
          { type: 'Feature' as const, properties: { kind: 'glow', ...s, height: s.height * 0.92 }, geometry: circle(c, 48) },
        ]
      }),
    }),
    [cinemas, stateOf],
  )

  const groundPoints = useMemo<FeatureCollection<Point>>(
    () => ({
      type: 'FeatureCollection',
      features: cinemas.map((c) => ({
        type: 'Feature' as const,
        properties: { color: BEAM[stateOf(c.id)].color },
        geometry: { type: 'Point' as const, coordinates: [c.lng, c.lat] },
      })),
    }),
    [cinemas, stateOf],
  )

  // ── clustering ──────────────────────────────────────────────
  const index = useMemo(() => {
    const sc = new Supercluster<{ id: string }>({ radius: 64, maxZoom: 12 })
    sc.load(
      cinemas.map((c) => ({ type: 'Feature' as const, properties: { id: c.id }, geometry: { type: 'Point' as const, coordinates: [c.lng, c.lat] } })),
    )
    return sc
  }, [cinemas])
  const clusters = useMemo(() => index.getClusters([-180, -85, 180, 85], Math.floor(zoom)), [index, zoom])
  const byId = useMemo(() => new Map(cinemas.map((c) => [c.id, c])), [cinemas])
  const showNames = zoom >= 12

  // ── load: restyle + intro ───────────────────────────────────
  const onLoad = () => {
    const map = mapRef.current?.getMap()
    if (!map) return
    if (import.meta.env.DEV) (window as unknown as { __map: MLMap }).__map = map // for debugging in devtools
    cinematize(map)

    // Rotation: right-drag / Ctrl+drag, two-finger twist + tilt, plus our Shift+scroll and Safari gestures
    map.dragRotate.enable()
    map.touchZoomRotate.enable()
    map.touchZoomRotate.enableRotation()
    map.touchPitch.enable()
    installRotationGestures(map, hint.markUsed)
    map.on('rotatestart', (e) => e.originalEvent && hint.markUsed())
    setGlMap(map)
  }

  // Camera fly-in: runs once, after both the intro has finished and the map has loaded.
  // No cleanup on purpose: StrictMode runs effects twice and a cleanup would cancel it.
  const introRan = useRef(false)
  useEffect(() => {
    if (!glMap || introRan.current) return
    introRan.current = true
    const map = glMap
    if (!startIntro || reducedMotion()) return void map.jumpTo({ ...HOME, padding: pad() })

    let interrupted = false
    const stop = () => (interrupted = true)
    map.once('dragstart', stop)
    map.once('wheel', stop)
    map.once('touchstart', stop)
    setTimeout(() => {
      if (interrupted) return
      // Swoop down into the Gulberg skyline, then pull back to reveal every beam
      map.flyTo({ ...CLOSE_UP, duration: 3400, curve: 1.6, essential: true })
      map.once('moveend', () => {
        if (!interrupted) setTimeout(() => !interrupted && map.easeTo({ ...HOME, padding: pad(), duration: 2600, essential: true }), 400)
      })
    }, 200)
  }, [startIntro, glMap, pad])

  return (
    // Fades in once the style has loaded, so the hand-over from the intro is a clean dissolve
    <div className="absolute inset-0 z-0 transition-opacity duration-500 will-change-[opacity]" style={{ opacity: glMap ? 1 : 0 }}>
      <MapGL
        ref={mapRef}
        mapStyle={STYLE_URL}
        initialViewState={START}
        minZoom={9}
        maxZoom={19}
        maxPitch={78}
        dragRotate
        pitchWithRotate
        touchZoomRotate
        touchPitch
        pixelRatio={Math.min(window.devicePixelRatio, 2)}
        attributionControl={{ compact: true }}
        onLoad={onLoad}
        onZoom={(e) => {
          const z = Math.floor(e.viewState.zoom * 2) / 2
          setZoom((prev) => (prev === z ? prev : z))
        }}
        style={{ position: 'absolute', inset: 0 }}
      >
        <Source id="cinema-ground" type="geojson" data={groundPoints}>
          <Layer
            id="cinema-ground-glow"
            type="circle"
            paint={{
              'circle-color': ['get', 'color'],
              'circle-radius': ['interpolate', ['exponential', 2], ['zoom'], 10, 6, 14, 34, 18, 300],
              'circle-blur': 1,
              'circle-opacity': 0.55,
              'circle-pitch-alignment': 'map',
            }}
          />
        </Source>
        <Source id="cinema-beams" type="geojson" data={beams}>
          <Layer
            id="cinema-beam-glow"
            type="fill-extrusion"
            filter={['==', ['get', 'kind'], 'glow']}
            paint={{
              'fill-extrusion-color': ['get', 'color'],
              'fill-extrusion-height': ['get', 'height'],
              'fill-extrusion-opacity': 0.14,
              'fill-extrusion-vertical-gradient': true,
            }}
          />
          <Layer
            id="cinema-beam-core"
            type="fill-extrusion"
            filter={['==', ['get', 'kind'], 'core']}
            paint={{
              'fill-extrusion-color': ['get', 'color'],
              'fill-extrusion-height': ['get', 'height'],
              'fill-extrusion-opacity': 0.75,
              'fill-extrusion-vertical-gradient': true,
            }}
          />
        </Source>

        {clusters.map((f) => {
          const [lng, lat] = f.geometry.coordinates
          if ('cluster' in f.properties && f.properties.cluster) {
            const count = f.properties.point_count
            const clusterId = f.properties.cluster_id
            // Clusters take the strongest state of the cinemas inside them, like single pins do
            const ids = index.getLeaves(clusterId, Infinity).map((leaf) => (leaf.properties as { id: string }).id)
            const enabledCount = ids.filter((id) => !unavailableIds.has(id)).length
            const showing = filtering ? ids.filter((id) => !unavailableIds.has(id) && matchById.has(id)).length : 0
            const state: PinState = selectedId && ids.includes(selectedId) && !unavailableIds.has(selectedId)
              ? 'active'
              : filtering ? (showing ? 'match' : 'dim') : enabledCount ? 'normal' : 'dim'
            const label = filtering
              ? `${showing} of ${enabledCount} available cinemas showing this movie — zoom in`
              : `${enabledCount} cinemas with records; ${count - enabledCount} temporarily disabled — zoom in`
            return (
              <Marker
                key={`cl-${clusterId}`}
                longitude={lng}
                latitude={lat}
                anchor="center"
                style={{ zIndex: state === 'active' ? 30 : state === 'match' ? 20 : state === 'dim' ? 1 : 10 }}
              >
                <button
                  type="button"
                  className={`cm-cluster is-${state}`}
                  aria-label={label}
                  title={label}
                  onClick={() =>
                    mapRef.current?.getMap().flyTo({ center: [lng, lat], zoom: index.getClusterExpansionZoom(clusterId) + 0.5, pitch: 55, duration: 1200 })
                  }
                >
                  {filtering && showing > 0 ? (
                    <>
                      <span className="cm-cluster-count">{showing}</span>
                      <span className="cm-cluster-label">showing</span>
                    </>
                  ) : (
                    count
                  )}
                </button>
              </Marker>
            )
          }
          const c = byId.get((f.properties as { id: string }).id)
          if (!c) return null
          const state = stateOf(c.id)
          const match = matchById.get(c.id)
          const times = showTimes && match ? upcoming([...new Set(match.showings.flatMap((s) => s.times))].sort()) : []
          return (
            <Marker
              key={c.id}
              longitude={c.lng}
              latitude={c.lat}
              anchor="bottom"
              style={{ zIndex: state === 'active' ? 30 : state === 'match' ? 20 : state === 'dim' ? 1 : 10 }}
              onClick={(e) => {
                e.originalEvent.stopPropagation()
                if (c.hasRecords === false) return
                onSelect(c.id)
              }}
            >
              <Pin cinema={c} state={state} times={times} showName={showNames || state === 'active' || (filtering && state === 'match')} disabled={c.hasRecords === false} />
            </Marker>
          )
        })}

        {userLocation && (
          <Marker longitude={userLocation.lng} latitude={userLocation.lat} anchor="center">
            <div className="cm-user" aria-label="Your location" />
          </Marker>
        )}
        {origin && (
          <Marker longitude={origin.lng} latitude={origin.lat} anchor="center">
            <div className="cm-origin">
              <span>{origin.label}</span>
            </div>
          </Marker>
        )}
      </MapGL>

      <Controls map={glMap} offsetRight={controlsRight} onReset={handle.resetView} onRotate={hint.markUsed} />
      <RotateHint visible={hint.visible} offsetRight={controlsRight} onDismiss={hint.markUsed} />
    </div>
  )
}

function Pin({ cinema, state, times, showName, disabled = false }: { cinema: Cinema; state: PinState; times: string[]; showName: boolean; disabled?: boolean }) {
  const shown = times.slice(0, 3)
  return (
    <button type="button" disabled={disabled} title={disabled ? 'Temporarily disabled — no live cinema records yet' : cinema.name} className={`cm-pin is-${state}${showName ? ' has-name' : ''}`} aria-label={cinema.name} aria-disabled={disabled}>
      {shown.length > 0 && (
        <span className="cm-times">
          {shown.map((t) => (
            <span key={t}>{formatTime(t)}</span>
          ))}
          {times.length > shown.length && <span className="more">+{times.length - shown.length}</span>}
        </span>
      )}
      <span className="cm-head">
        {state !== 'dim' && <span className="cm-pulse" />}
        <span className="cm-core">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="7.5" r="1.6" />
            <circle cx="12" cy="16.5" r="1.6" />
            <circle cx="7.5" cy="12" r="1.6" />
            <circle cx="16.5" cy="12" r="1.6" />
          </svg>
        </span>
        <span className="cm-name">{cinema.name}</span>
      </span>
      <span className="cm-stem" />
    </button>
  )
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)

interface ControlsProps {
  map: MLMap | null
  offsetRight: number
  onReset: () => void
  onRotate: () => void
}

function Controls({ map, offsetRight, onReset, onRotate }: ControlsProps) {
  const compass = useRef<SVGSVGElement>(null)
  const [tilted, setTilted] = useState(false)
  // Where a run of rotate-button clicks is heading, so rapid clicks stack cleanly in 45° steps
  const target = useRef<number | null>(null)

  useEffect(() => {
    if (!map) return
    const sync = () => {
      if (compass.current) compass.current.style.transform = `rotate(${-map.getBearing() - 45}deg)`
      const t = map.getPitch() > 20
      setTilted((prev) => (prev === t ? prev : t))
    }
    sync()
    map.on('rotate', sync)
    map.on('pitch', sync)
    return () => {
      map.off('rotate', sync)
      map.off('pitch', sync)
    }
  }, [map])

  const rotateBy = (deg: number) => {
    if (!map) return
    const base = target.current ?? Math.round(map.getBearing() / 45) * 45
    target.current = base + deg
    map.easeTo({ bearing: target.current, duration: 500, easing: easeOut })
    map.once('moveend', () => (target.current = null))
    onRotate()
  }

  const btn = 'grid size-10 place-items-center text-neutral-300 transition-colors duration-150 hover:bg-white/[0.07] hover:text-gold-300 active:bg-white/[0.12]'
  return (
    <div
      className="glass fixed top-1/2 z-20 flex -translate-y-1/2 flex-col overflow-hidden rounded-2xl transition-[right] duration-500"
      style={{ right: offsetRight }}
    >
      <button type="button" className={`${btn} hidden sm:grid`} onClick={() => map?.easeTo({ zoom: map.getZoom() + 1, duration: 300 })} aria-label="Zoom in" title="Zoom in">
        <Plus className="size-4" />
      </button>
      <button type="button" className={`${btn} hidden sm:grid`} onClick={() => map?.easeTo({ zoom: map.getZoom() - 1, duration: 300 })} aria-label="Zoom out" title="Zoom out">
        <Minus className="size-4" />
      </button>
      <span className="mx-2.5 hidden h-px bg-white/[0.07] sm:block" aria-hidden />
      <button type="button" className={btn} onClick={() => rotateBy(45)} aria-label="Rotate left 45°" title="Rotate left">
        <RotateCcw className="size-4" />
      </button>
      <button type="button" className={btn} onClick={() => rotateBy(-45)} aria-label="Rotate right 45°" title="Rotate right">
        <RotateCw className="size-4" />
      </button>
      <button
        type="button"
        className={btn}
        onClick={() => {
          target.current = null
          map?.easeTo({ bearing: 0, duration: 600, easing: easeOut })
        }}
        aria-label="Reset bearing to north"
        title="Reset north"
      >
        <Compass ref={compass} className="size-[18px] text-gold-400" style={{ transform: 'rotate(-45deg)' }} />
      </button>
      <span className="mx-2.5 h-px bg-white/[0.07]" aria-hidden />
      <button type="button" className={`${btn} ${tilted ? 'text-gold-300' : ''}`} onClick={() => map?.easeTo({ pitch: tilted ? 0 : 60, duration: 700 })} aria-label={tilted ? 'Switch to 2D' : 'Switch to 3D'} title="Toggle 3D">
        <Box className="size-4" />
      </button>
      <button type="button" className={btn} onClick={onReset} aria-label="Reset view" title="Reset view">
        <Home className="size-4" />
      </button>
    </div>
  )
}

const HINT_KEY = 'filmup:rotate-hint-seen'

/** One-time rotation hint. Storage can throw (private mode, blocked cookies) — then it just shows each visit. */
function useRotateHint(delayMs: number | null) {
  const [visible, setVisible] = useState(false)
  const done = useRef(false)

  const markUsed = useCallback(() => {
    if (done.current) return
    done.current = true
    setVisible(false)
    try {
      localStorage.setItem(HINT_KEY, '1')
    } catch {
      /* storage unavailable */
    }
  }, [])

  useEffect(() => {
    let seen = false
    try {
      seen = localStorage.getItem(HINT_KEY) === '1'
    } catch {
      /* storage unavailable */
    }
    if (seen) {
      done.current = true
      return
    }
    if (delayMs == null) return
    const show = setTimeout(() => !done.current && setVisible(true), delayMs)
    const hide = setTimeout(markUsed, delayMs + 14000)
    return () => {
      clearTimeout(show)
      clearTimeout(hide)
    }
  }, [delayMs, markUsed])

  return { visible, markUsed }
}

function RotateHint({ visible, offsetRight, onDismiss }: { visible: boolean; offsetRight: number; onDismiss: () => void }) {
  const touch = window.matchMedia('(pointer: coarse)').matches
  // Small screens: a centred pill above the Now Showing strip, so it never sits under the
  // fingers doing the gesture. Larger screens: beside the map controls it refers to.
  const compact = window.innerWidth < 640
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          role="status"
          initial={compact ? { opacity: 0, y: 12, scale: 0.96 } : { opacity: 0, x: 12, scale: 0.96 }}
          animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
          exit={compact ? { opacity: 0, y: 12, scale: 0.96 } : { opacity: 0, x: 12, scale: 0.96 }}
          transition={{ type: 'spring', stiffness: 380, damping: 30 }}
          className={`glass fixed z-20 flex items-center gap-3 rounded-2xl py-2.5 pr-2 pl-3 ${compact ? 'inset-x-3 bottom-[96px] mx-auto w-fit' : 'top-1/2 max-w-[calc(100vw-96px)] -translate-y-1/2'}`}
          style={compact ? undefined : { right: offsetRight + 56 }}
        >
          <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-gold-400/12 text-gold-300">
            <RotateCw className="size-4" />
          </span>
          <span className="min-w-0 text-xs leading-snug">
            <span className="block font-semibold text-white">{touch ? 'Twist with two fingers to rotate' : 'Shift + scroll or right-drag to rotate'}</span>
            <span className="block text-neutral-400">{touch ? 'Drag two fingers up or down to tilt' : 'Or use the rotate buttons'}</span>
          </span>
          <button type="button" onClick={onDismiss} className="grid size-7 shrink-0 place-items-center rounded-full text-neutral-500 transition hover:bg-white/[0.07] hover:text-white" aria-label="Dismiss hint">
            <X className="size-3.5" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** Approximate a circle of `radiusM` metres as a polygon (fine at city scale). */
function circle({ lat, lng }: LatLng, radiusM: number, steps = 28): Polygon {
  const dLat = radiusM / 111_320
  const dLng = radiusM / (111_320 * Math.cos((lat * Math.PI) / 180))
  const ring = Array.from({ length: steps + 1 }, (_, i) => {
    const a = (i / steps) * Math.PI * 2
    return [lng + dLng * Math.cos(a), lat + dLat * Math.sin(a)]
  })
  return { type: 'Polygon', coordinates: [ring] }
}

/**
 * Restyle OpenFreeMap "dark" into a night-city look: blue-black ground, softly
 * glowing amber arterials, readable neighbourhood labels and lit 3D buildings.
 */
function cinematize(map: MLMap) {
  type P = Parameters<MLMap['setPaintProperty']>
  const paint = (id: string, prop: P[1], value: P[2]) => {
    if (map.getLayer(id)) map.setPaintProperty(id, prop, value)
  }
  const hide = (id: string) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', 'none')
  const zoomRamp = (...stops: number[]): ExpressionSpecification => ['interpolate', ['exponential', 1.5], ['zoom'], ...stops]

  // Ground, water, green
  paint('background', 'background-color', '#0b0d13')
  paint('landuse_residential', 'fill-color', '#0f1219')
  paint('water', 'fill-color', '#0d1a2a')
  paint('waterway', 'line-color', '#14283d')
  paint('landuse_park', 'fill-color', '#0f1a16')
  paint('landcover_wood', 'fill-pattern', undefined)
  paint('landcover_wood', 'fill-color', '#0f1a16')
  paint('aeroway-area', 'fill-color', '#141821')

  // Roads: minor streets visible but quiet, arterials warm, motorways brightest
  paint('highway_path', 'line-color', '#1b1f28')
  paint('highway_minor', 'line-color', '#222733')
  paint('highway_minor', 'line-opacity', 1)
  paint('highway_major_casing', 'line-color', '#0b0d13')
  paint('highway_major_inner', 'line-color', '#6b5a3d')
  paint('highway_major_inner', 'line-width', zoomRamp(10, 1, 13, 2.2, 16, 6, 20, 22))
  paint('highway_major_subtle', 'line-color', '#4a3f2d')
  paint('highway_motorway_casing', 'line-color', '#0b0d13')
  paint('highway_motorway_inner', 'line-color', '#d29a4a')
  paint('highway_motorway_subtle', 'line-color', '#8a6531')
  paint('railway', 'line-color', '#2a2e38')
  paint('railway_dashline', 'line-color', '#0b0d13')

  // Soft glow under arterials + motorways (blurred wide lines beneath the crisp ones)
  // stops = [zoom, width, zoom, width, …]; blur is 60% of width at each stop
  const glowFrom = (src: string, id: string, color: string, stops: number[], opacity: number) => {
    const width = zoomRamp(...stops)
    const blur = zoomRamp(...stops.map((v, i) => (i % 2 ? v * 0.6 : v)))
    const base = map.getStyle().layers?.find((l) => l.id === src)
    if (!base || base.type !== 'line' || map.getLayer(id)) return
    map.addLayer(
      {
        id,
        type: 'line',
        source: 'openmaptiles',
        'source-layer': 'transportation',
        minzoom: base.minzoom ?? 0,
        ...(base.filter ? { filter: base.filter } : {}),
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': color, 'line-width': width, 'line-blur': blur, 'line-opacity': opacity },
      },
      src === 'highway_motorway_inner' ? 'highway_motorway_casing' : 'highway_major_casing',
    )
  }
  glowFrom('highway_major_inner', 'glow-major', '#f5c26b', [10, 3, 14, 10, 18, 40], 0.12)
  glowFrom('highway_motorway_inner', 'glow-motorway', '#ffb24d', [8, 4, 13, 16, 18, 60], 0.22)

  // Labels: latin names, neighbourhoods readable, streets understated
  const latinName: ExpressionSpecification = ['coalesce', ['get', 'name:en'], ['get', 'name_en'], ['get', 'name:latin'], ['get', 'name']]
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== 'symbol' || !map.getLayoutProperty(layer.id, 'text-field')) continue
    map.setLayoutProperty(layer.id, 'text-field', latinName)
    map.setLayoutProperty(layer.id, 'text-font', ['Noto Sans Regular'])
    map.setPaintProperty(layer.id, 'text-halo-color', 'rgba(8,10,15,0.92)')
    map.setPaintProperty(layer.id, 'text-halo-width', 1.4)
    map.setPaintProperty(layer.id, 'text-halo-blur', 0.5)
  }
  for (const id of ['place_village', 'place_other', 'place_hamlet']) {
    paint(id, 'text-color', '#6f6a62')
    if (map.getLayer(id)) map.setLayoutProperty(id, 'text-size', 10)
  }
  for (const id of ['place_suburb']) {
    paint(id, 'text-color', '#c9c3b6')
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, 'text-size', ['interpolate', ['linear'], ['zoom'], 10, 11, 14, 14])
      map.setLayoutProperty(id, 'text-transform', 'uppercase')
      map.setLayoutProperty(id, 'text-letter-spacing', 0.12)
      map.setLayoutProperty(id, 'text-font', ['Noto Sans Bold'])
    }
  }
  for (const id of ['place_town', 'place_city', 'place_city_large']) {
    paint(id, 'text-color', '#f1e7d2')
    if (map.getLayer(id)) map.setLayoutProperty(id, 'text-font', ['Noto Sans Bold'])
  }
  paint('highway_name_other', 'text-color', '#8a8478')
  paint('highway_name_motorway', 'text-color', '#d9b27a')
  paint('water_name', 'text-color', '#4f7396')

  // 3D buildings
  hide('building')
  const firstSymbol = map.getStyle().layers?.find((l) => l.type === 'symbol')?.id
  if (!map.getLayer('3d-buildings')) {
    map.addLayer(
      {
        id: '3d-buildings',
        type: 'fill-extrusion',
        source: 'openmaptiles',
        'source-layer': 'building',
        minzoom: 12.5,
        paint: {
          'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 0], 0, '#1f2330', 20, '#272c3b', 60, '#353b4e', 150, '#454c63'],
          // Many Lahore buildings have no height tag — give them a believable minimum
          'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], 12.5, 0, 13.6, ['max', ['coalesce', ['get', 'render_height'], 0], 9]],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
          'fill-extrusion-opacity': 0.94,
          'fill-extrusion-vertical-gradient': true,
        },
      },
      firstSymbol,
    )
  }

  map.setLight({ anchor: 'viewport', color: '#ffe2bd', intensity: 0.45, position: [1.3, 200, 40] })
  try {
    map.setSky({
      'sky-color': '#0a0c14',
      'horizon-color': '#2a2236',
      'fog-color': '#0b0d13',
      'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.5,
      'fog-ground-blend': 0.85,
      'atmosphere-blend': 0,
    })
  } catch {
    /* sky is cosmetic — older GL contexts can skip it */
  }
}
