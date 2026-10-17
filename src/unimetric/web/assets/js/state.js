export const state = {
  projects: [], project: null, mode: 'local', view: 'overview',
  messages: [], session: null,
};

export const events = new EventTarget();
export const requestRender = () => events.dispatchEvent(new Event('render'));
export const requestProjectsRefresh = () => events.dispatchEvent(new Event('projects-changed'));
export const requestNewProject = () => events.dispatchEvent(new Event('new-project'));
