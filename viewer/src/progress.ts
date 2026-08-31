import type { ProgressRecord, Project } from './api';
import { formatTime } from './formatters';

export function renderProgress(container: HTMLElement, count: HTMLElement, events: ProgressRecord[], project: Project | null): void {
  count.textContent = String(events.length);
  container.replaceChildren();
  if (events.length === 0) { container.innerHTML = `<p class="panel-empty">${project ? 'No Agent Run yet.' : 'No Project selected.'}</p>`; return; }
  const historical = project?.state !== 'Running';
  for (const event of events) {
    const item = document.createElement('article'); item.className = 'progress-item';
    const heading = document.createElement('div'); heading.className = 'progress-heading';
    const stage = document.createElement('strong'); stage.textContent = `${historical ? 'history · ' : ''}${event.stage.replaceAll('_', ' ')}`;
    const time = document.createElement('time'); time.textContent = formatTime(event.created_at); heading.append(stage, time);
    const detail = document.createElement('span'); detail.textContent = [event.tool, event.attempt ? `attempt ${event.attempt}` : null, event.result].filter(Boolean).join(' · ');
    item.append(heading, detail); container.append(item);
  }
}
