import { useEffect, useState } from "react";

export function useClock(active: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 90);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}
export function elapsed(start: number | null, now: number): string {
  const seconds = Math.max(0, Math.floor((now - (start ?? now)) / 1000));
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}
export function spinner(now: number): string { return "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[Math.floor(now / 90) % 10]; }
export function equalizer(now: number): string {
  return Array.from({ length: 6 }, (_, i) => "▁▂▃▄▅▆▇█"[Math.floor((Math.sin(now / 130 + i * 0.9) * 0.5 + 0.5) * 7)]).join("");
}
