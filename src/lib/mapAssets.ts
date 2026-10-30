/* Map loading, kept out of the main bundle.

   MapLibre is ~1 MB of JS. It's split into its own chunk (loaded via `loadMapModule`) and
   the style JSON + tile metadata are fetched at low priority while the intro plays, so the
   map can be created the moment the intro ends without competing with video playback. */

export const STYLE_URL = 'https://tiles.openfreemap.org/styles/dark'

export const loadMapModule = () => import('../components/Map')

let prefetched = false

/** Warm the HTTP cache and the JS chunk during idle time. Never creates the map. */
export function prefetchMap() {
  if (prefetched) return
  prefetched = true
  const idle: (cb: () => void) => void =
    'requestIdleCallback' in window ? (cb) => window.requestIdleCallback(cb, { timeout: 1500 }) : (cb) => setTimeout(cb, 300)

  idle(() => {
    const low = { priority: 'low' } as RequestInit
    fetch(STYLE_URL, low)
      .then((r) => r.json())
      .then((style: { sources?: Record<string, { url?: string }> }) => {
        const tilejson = style.sources?.openmaptiles?.url
        if (tilejson) fetch(tilejson, low).catch(() => {})
      })
      .catch(() => {})
    void loadMapModule()
  })
}
