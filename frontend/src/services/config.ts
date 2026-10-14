/**
 * Single switch between the in-browser simulation (mockApi.ts) and the local server of
 * phase 6b (realApi.ts, `python -m gani_dung_video serve` on 127.0.0.1:8765). Components only
 * import the `api` object from api.ts, so nothing else changes.
 *
 * `npm run dev` / `npm run build` → simulation; `npm run dev:real` / `npm run build:real`
 * (VITE_USE_MOCK=false in .env.real) → real server. In dev mode vite.config.ts proxies /api
 * and /media to the server; the built UI is served by the server itself (same origin).
 */

export const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? 'true') !== 'false';
