import type { Project } from './api';
import { formatCompactTokenCount, formatDuration } from './formatters';

export class ProjectCatalog {
  private projects: Project[] = [];

  get all(): Project[] { return this.projects; }
  setAll(projects: Project[]): void { this.projects = [...projects]; }
  upsert(project: Project): void {
    const index = this.projects.findIndex((item) => item.project_id === project.project_id);
    if (index >= 0) this.projects[index] = project;
    else this.projects.unshift(project);
    this.projects.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  }
  remove(projectId: string): void { this.projects = this.projects.filter((item) => item.project_id !== projectId); }
  get(projectId: string | null): Project | null { return this.projects.find((project) => project.project_id === projectId) ?? null; }
  has(projectId: string): boolean { return this.projects.some((project) => project.project_id === projectId); }
  globalRunActive(): boolean { return this.projects.some((project) => project.state === 'Running'); }

  render(container: HTMLElement, empty: HTMLElement, count: HTMLElement, selectedProjectId: string | null, onSelect: (id: string) => void): void {
    count.textContent = String(this.projects.length);
    empty.hidden = this.projects.length > 0;
    container.replaceChildren();
    for (const project of this.projects) {
      const row = document.createElement('button');
      row.type = 'button'; row.className = 'project-row';
      row.classList.toggle('selected', project.project_id === selectedProjectId);
      row.setAttribute('role', 'listitem');
      row.setAttribute('aria-current', String(project.project_id === selectedProjectId));
      const title = document.createElement('strong'); title.textContent = project.name;
      const meta = document.createElement('span'); meta.className = 'project-row-meta';
      const state = document.createElement('span'); state.textContent = project.state; state.className = `project-state state-${project.state.toLowerCase()}`;
      const usage = document.createElement('span'); usage.className = 'project-usage';
      usage.textContent = `${formatCompactTokenCount(project.token_usage?.total_tokens ?? null)} tok · ${formatDuration(project.duration_seconds)}`;
      meta.append(state, usage); row.append(title, meta);
      row.addEventListener('click', () => void onSelect(project.project_id));
      container.append(row);
    }
  }
}
