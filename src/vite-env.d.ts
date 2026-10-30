/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** FastAPI catalogue endpoint base; defaults to the Vite-proxied /api. */
  readonly VITE_CINEMA_API_URL?: string
  /** Optional FastAPI target for Vite's /api development proxy. */
  readonly VITE_API_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
