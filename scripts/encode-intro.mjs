#!/usr/bin/env node
/* Encode an intro video for the web.

   Usage:  npm run intro -- <input-video> [--mobile-crop]

   Writes to public/intro/:
     intro-1080.mp4   H.264 + AAC, faststart        (desktop, budget ~5 MB)
     intro-1080.webm  VP9 + Opus                     (desktop, budget ~5 MB)
     intro-720.mp4    H.264, 720p (or 720x1280 centre crop with --mobile-crop)  (mobile, ~2.5 MB)
     intro-720.webm   VP9 mobile version
     poster.jpg / poster-mobile.jpg   first frame, shown while the video loads
     manifest.json    what the app reads — no code changes needed to swap footage

   Requires ffmpeg + ffprobe on PATH. Keep the source file out of git (see .gitignore). */
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdirSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const args = process.argv.slice(2)
const input = args.find((a) => !a.startsWith('--'))
const mobileCrop = args.includes('--mobile-crop')
if (!input) {
  console.error('Usage: npm run intro -- <input-video> [--mobile-crop]')
  process.exit(1)
}

const OUT = join(process.cwd(), 'public', 'intro')
mkdirSync(OUT, { recursive: true })

const run = (cmd, a) => execFileSync(cmd, a, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
const ff = (a) => run('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...a])

// ── probe ──
const probe = JSON.parse(run('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', input]))
const video = probe.streams.find((s) => s.codec_type === 'video')
const audio = probe.streams.find((s) => s.codec_type === 'audio')
const duration = Number(probe.format.duration)
if (!video) throw new Error('No video stream found')

// "Meaningful" audio: present and not near-silent (volumedetect reports on stderr)
let hasAudio = false
if (audio) {
  const { stderr } = spawnSync('ffmpeg', ['-hide_banner', '-i', input, '-af', 'volumedetect', '-vn', '-f', 'null', '-'], { encoding: 'utf8' })
  const max = /max_volume: (-?[\d.]+) dB/.exec(stderr ?? '')
  hasAudio = max ? Number(max[1]) > -40 : true
}

console.log(`Source: ${video.width}x${video.height} ${video.codec_name}, ${duration.toFixed(1)}s, audio: ${audio ? (hasAudio ? 'yes' : 'near-silent') : 'none'}`)

// Bitrate budget: fit the target size, leaving ~8% headroom for container overhead
const AUDIO_K = hasAudio ? 96 : 0
const budget = (mb) => Math.max(300, Math.floor(((mb * 8192) / duration) * 0.92 - AUDIO_K))
const desktopK = Math.min(2500, budget(5))
const mobileK = Math.min(1400, budget(2.5))

const audioMp4 = hasAudio ? ['-c:a', 'aac', '-b:a', `${AUDIO_K}k`, '-ac', '2'] : ['-an']
const audioWebm = hasAudio ? ['-c:a', 'libopus', '-b:a', `${AUDIO_K}k`, '-ac', '2'] : ['-an']
// Keep the source frame rate up to 30 fps (never duplicate frames from 24 fps footage)
const [fn, fd] = String(video.r_frame_rate ?? '30/1').split('/').map(Number)
const fps = ['-r', String(Math.min(30, Math.round((fn / (fd || 1)) * 1000) / 1000) || 30)]

const desktopScale = video.height > 1080 ? 'scale=-2:1080:flags=lanczos' : 'scale=trunc(iw/2)*2:trunc(ih/2)*2'
const mobileScale = mobileCrop
  ? 'crop=ih*9/16:ih,scale=720:1280:flags=lanczos'
  : video.height > 720
    ? 'scale=-2:720:flags=lanczos'
    : 'scale=trunc(iw/2)*2:trunc(ih/2)*2'

const h264 = (k) => ['-c:v', 'libx264', '-preset', 'veryfast', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-crf', '24', '-maxrate', `${k}k`, '-bufsize', `${k * 2}k`, '-movflags', '+faststart']
const vp9 = (k) => ['-c:v', 'libvpx-vp9', '-b:v', `${k}k`, '-maxrate', `${Math.round(k * 1.3)}k`, '-crf', '33', '-deadline', 'good', '-cpu-used', '4', '-row-mt', '1', '-pix_fmt', 'yuv420p']

const jobs = [
  ['intro-1080.mp4', [...fps, '-vf', desktopScale, ...h264(desktopK), ...audioMp4]],
  ['intro-1080.webm', [...fps, '-vf', desktopScale, ...vp9(desktopK), ...audioWebm]],
  ['intro-720.mp4', [...fps, '-vf', mobileScale, ...h264(mobileK), ...audioMp4]],
  ['intro-720.webm', [...fps, '-vf', mobileScale, ...vp9(mobileK), ...audioWebm]],
]
for (const [name, a] of jobs) {
  const t = Date.now()
  ff(['-i', input, ...a, join(OUT, name)])
  console.log(`  ${name.padEnd(16)} ${(statSync(join(OUT, name)).size / 1048576).toFixed(2)} MB  (${((Date.now() - t) / 1000).toFixed(0)}s)`)
}

// Posters: first frame, so there is no visible jump when playback starts
ff(['-i', input, '-frames:v', '1', '-vf', desktopScale, '-q:v', '4', join(OUT, 'poster.jpg')])
ff(['-i', input, '-frames:v', '1', '-vf', mobileScale, '-q:v', '4', join(OUT, 'poster-mobile.jpg')])

const manifest = {
  duration: Math.round(duration * 10) / 10,
  hasAudio,
  poster: '/intro/poster.jpg',
  posterMobile: '/intro/poster-mobile.jpg',
  desktop: [
    { src: '/intro/intro-1080.webm', type: 'video/webm; codecs="vp9, opus"' },
    { src: '/intro/intro-1080.mp4', type: 'video/mp4' },
  ],
  mobile: [
    { src: '/intro/intro-720.webm', type: 'video/webm; codecs="vp9, opus"' },
    { src: '/intro/intro-720.mp4', type: 'video/mp4' },
  ],
}
writeFileSync(join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log('  manifest.json written — the app picks it up automatically')
