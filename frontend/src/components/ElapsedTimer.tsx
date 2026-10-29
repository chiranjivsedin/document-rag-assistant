import { useEffect, useState } from "react";

/** Whole seconds since `startedAt` (a Date.now() value), updated every second. */
export function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return <span className="elapsed">{seconds} s</span>;
}
