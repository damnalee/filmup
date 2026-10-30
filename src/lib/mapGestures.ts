/* Extra rotation gestures MapLibre doesn't provide out of the box.

   - Shift + wheel / two-finger trackpad scroll → rotate (plain scroll still zooms)
   - Safari trackpad pinch-rotate (non-standard gesture* events) → rotate + zoom

   Right-drag, Ctrl + left-drag and two-finger twist on touchscreens are
   MapLibre's own dragRotate / touchZoomRotate handlers. */
import type { Map as MLMap } from 'maplibre-gl'

/** Safari's GestureEvent isn't in lib.dom */
interface GestureEvent extends UIEvent {
  rotation: number
  scale: number
}

const DEG_PER_PX = 0.25
const MAX_STEP = 18

export function installRotationGestures(map: MLMap, onUserRotate: () => void): () => void {
  const container = map.getContainer()

  // Batch wheel deltas into one bearing change per animation frame (60fps, no event pile-up)
  let pending = 0
  let frame = 0
  const flush = () => {
    frame = 0
    if (pending) map.setBearing(map.getBearing() + pending)
    pending = 0
  }

  const onWheel = (e: WheelEvent) => {
    if (!e.shiftKey) return
    // Capture phase on the container runs before MapLibre's scroll-zoom on the canvas
    e.preventDefault()
    e.stopPropagation()
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1
    // Browsers map Shift + vertical scroll to deltaX; trackpads report both — use the dominant axis
    const delta = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * unit
    pending += Math.max(-MAX_STEP, Math.min(MAX_STEP, delta * DEG_PER_PX))
    if (!frame) frame = requestAnimationFrame(flush)
    onUserRotate()
  }

  // Safari (macOS) trackpad gestures. iOS fires these for touch too, where MapLibre's
  // touchZoomRotate already handles it — so only use them on non-touch devices.
  const useGestureEvents = navigator.maxTouchPoints === 0
  let start = { bearing: 0, zoom: 0 }
  const onGestureStart = (e: Event) => {
    e.preventDefault()
    start = { bearing: map.getBearing(), zoom: map.getZoom() }
  }
  const onGestureChange = (e: Event) => {
    e.preventDefault()
    const g = e as GestureEvent
    map.jumpTo({ bearing: start.bearing - g.rotation, zoom: start.zoom + Math.log2(g.scale) })
    if (Math.abs(g.rotation) > 3) onUserRotate()
  }
  const preventDefault = (e: Event) => e.preventDefault()

  container.addEventListener('wheel', onWheel, { passive: false, capture: true })
  if (useGestureEvents) {
    container.addEventListener('gesturestart', onGestureStart, { passive: false })
    container.addEventListener('gesturechange', onGestureChange, { passive: false })
    container.addEventListener('gestureend', preventDefault, { passive: false })
  }

  return () => {
    cancelAnimationFrame(frame)
    container.removeEventListener('wheel', onWheel, { capture: true })
    container.removeEventListener('gesturestart', onGestureStart)
    container.removeEventListener('gesturechange', onGestureChange)
    container.removeEventListener('gestureend', preventDefault)
  }
}
