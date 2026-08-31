import type { ConversationTurn } from './api';
import { formatTime } from './formatters';

export function renderConversation(container: HTMLElement, count: HTMLElement, turns: ConversationTurn[], globalRunActive: boolean, onRetry: (turn: ConversationTurn) => void): void {
  count.textContent = String(turns.length);
  container.replaceChildren();
  if (turns.length === 0) {
    container.innerHTML = '<p class="panel-empty">No messages yet. Describe the first CAD part below.</p>';
    return;
  }
  for (const turn of turns) {
    const article = document.createElement('article'); article.className = `conversation-turn turn-${turn.status}`;
    const user = document.createElement('div'); user.className = 'message message-user';
    const userMeta = document.createElement('div'); userMeta.className = 'message-meta';
    userMeta.innerHTML = `<strong>You</strong><time>${formatTime(turn.created_at)}</time>`;
    const userBody = document.createElement('p'); userBody.textContent = turn.user_message; user.append(userMeta, userBody);
    const assistant = document.createElement('div'); assistant.className = 'message message-assistant';
    const assistantMeta = document.createElement('div'); assistantMeta.className = 'message-meta';
    const assistantName = document.createElement('strong'); assistantName.textContent = 'CadFlow';
    const turnStatus = document.createElement('span'); turnStatus.className = `turn-status status-${turn.status}`; turnStatus.textContent = turn.status;
    assistantMeta.append(assistantName, turnStatus);
    const assistantBody = document.createElement('p'); assistantBody.textContent = turn.assistant_message || turn.error || (turn.status === 'running' ? 'Working on this CAD change...' : 'No response was recorded.');
    assistant.append(assistantMeta, assistantBody);
    if (turn.status === 'failed' || turn.status === 'cancelled') {
      const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'retry-turn quiet-button'; retry.textContent = 'Retry'; retry.disabled = globalRunActive;
      retry.addEventListener('click', () => void onRetry(turn)); assistant.append(retry);
    }
    article.append(user, assistant); container.append(article);
  }
  container.scrollTop = container.scrollHeight;
}
