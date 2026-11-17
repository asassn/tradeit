import { useEffect, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Load once per key, and keep the error rather than swallowing it.
 *
 * Every view fails on its own: a half-loaded screen that says which half
 * failed beats a blank one, and beats one that silently drops a section a
 * reader would take for "nothing recorded".
 */
export function useAsync<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  });
  useEffect(() => {
    let live = true;
    setState({ data: null, error: null, loading: true });
    load()
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch(
        (error: unknown) =>
          live &&
          setState({
            data: null,
            error: error instanceof Error ? error : new Error(String(error)),
            loading: false,
          }),
      );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
