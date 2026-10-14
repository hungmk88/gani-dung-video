/**
 * Polling as a setTimeout chain: the next request starts only once the previous one has
 * settled, so a slow answer can never land after a newer one (setInterval would overlap).
 * `tick` gets a signal that aborts on stop: check `signal.aborted` before using a result.
 * Returns the stop function (for a React effect cleanup).
 */
export function startPolling(tick: (signal: AbortSignal) => Promise<unknown>, intervalMs: number): () => void {
  const ctrl = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const run = async (): Promise<void> => {
    try {
      await tick(ctrl.signal);
    } catch {
      // The tick reports its own failures; polling goes on.
    }
    if (!ctrl.signal.aborted) timer = setTimeout(() => void run(), intervalMs);
  };
  void run();
  return () => {
    ctrl.abort();
    clearTimeout(timer);
  };
}
