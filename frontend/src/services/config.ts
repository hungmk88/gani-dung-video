/**
 * Single switch-over point for phase 6 (spec: "SQLite Queue Daemon... REST API/WebSocket").
 * Right now there is no backend, so USE_MOCK stays true and every call in api.ts routes to
 * mockApi.ts. When the FastAPI/Flask daemon at 127.0.0.1:8765 exists, flip USE_MOCK (or set
 * VITE_USE_MOCK=false) and implement the `real*` functions in api.ts — component code never
 * changes because it only imports the `api` object from api.ts.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8765';

export const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? 'true') !== 'false';

// vite.config.ts already proxies /api → API_BASE for when USE_MOCK is flipped off.
export const WS_URL = API_BASE.replace(/^http/, 'ws');
