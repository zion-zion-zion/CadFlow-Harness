export function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function isNonNegativeFinite(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value >= 0;
}

export function formatTokenCount(value: number | null): string {
  return isNonNegativeFinite(value) ? Math.trunc(value).toLocaleString() : '--';
}

export function formatCompactTokenCount(value: number | null): string {
  if (!isNonNegativeFinite(value)) return '--';
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(Math.trunc(value));
}

export function formatDuration(value: number | null): string {
  if (!isNonNegativeFinite(value)) return '--';
  if (value < 60) {
    const seconds = value < 10 ? value.toFixed(1).replace(/\.0$/, '') : String(Math.round(value));
    return `${seconds}s`;
  }
  const totalSeconds = Math.round(value);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

export function humanizeIdentifier(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed.';
}
