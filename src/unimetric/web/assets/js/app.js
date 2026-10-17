import { $ } from './dom.js';
import { events } from './state.js';
import { emptyState } from './ui.js';
import { buildNav, setView, loadOrgs, render } from './shell.js';
import { loadProviders } from './providers.js';
import { openModal } from './projects.js';

function showConnectionError() {
  $('#content').replaceChildren(emptyState('데이터베이스 연결을 확인하세요', 'PostgreSQL을 시작한 후 새로고침하세요.'));
}

events.addEventListener('render', render);
events.addEventListener('new-project', openModal);
events.addEventListener('projects-changed', () => loadOrgs().catch(showConnectionError));
$('#orgSwitch').onclick = () => $('#orgMenu').classList.toggle('show');
$('#refreshBtn').onclick = render;
$('#navChatBtn').onclick = () => setView('chat');
document.addEventListener('click', event => {
  if (!event.target.closest('.orgbox')) $('#orgMenu').classList.remove('show');
});

buildNav();
loadProviders();
loadOrgs().catch(showConnectionError);
