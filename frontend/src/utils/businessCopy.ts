/** Format a nullable business value without removing its content or provenance. */
export function formatBusinessText(value: unknown): string {
  return String(value ?? '').trim()
}
