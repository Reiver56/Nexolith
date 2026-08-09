export const DELIBERATE_MOTION_MS = 240;

export function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function deliberateMotionDuration(): number {
  return prefersReducedMotion() ? 0 : DELIBERATE_MOTION_MS;
}
