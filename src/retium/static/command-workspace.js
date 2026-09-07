// Presentation only: move the existing live controls, never duplicate their IDs,
// listeners, requests or permissions. Layout changes never move the map camera.
const STORAGE_KEY = 'reticom-command-workspace-v1';
const names = ['comms', 'ops', 'tasks', 'layers'];
export function readWorkspace(raw) {
  let value; try { value = JSON.parse(raw); } catch { value = null; }
  const state = {open: ['comms'], positions: {}};
  if (!value || typeof value !== 'object') return state;
  if (Array.isArray(value.open)) state.open = [...new Set(value.open.filter(n => names.includes(n) && n !== 'layers'))];
  for (const name of names) {
    const p = value.positions?.[name];
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) state.positions[name] = {x: Math.max(0, p.x), y: Math.max(0, p.y)};
  }
  return state;
}
export function clampPanel(position, bounds, size) {
  return {x: Math.max(8, Math.min(position.x, bounds.width - size.width - 8)),
    y: Math.max(8, Math.min(position.y, bounds.height - size.height - 84))};
}
const paths = {
  comms: '<path d="M4 4h16v12H9l-5 4V4Z"/>',
  ops: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
  tasks: '<path d="m3 6 2 2 3-4m3 2h10M3 14l2 2 3-4m3 2h10M11 21h10"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  focus: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  reset: '<path d="M3 10a9 9 0 1 1 2 9M3 4v6h6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  grip: '<path d="M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01"/>',
  draw: '<path d="m4 20 1-5L16 4l4 4L9 19l-5 1Zm10-14 4 4"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;

export function createCommandWorkspace({resizeMap, openDraw, openMembers}) {
  const $ = id => document.getElementById(id), root = $('commandView');
  let state; try { state = readWorkspace(localStorage.getItem(STORAGE_KEY)); } catch { state = readWorkspace(null); }
  let tasksEnabled = false, focused = false, layer = 12;
  const panels = new Map(), dock = document.createElement('nav');
  document.body.classList.add('command-canvas-mode');
  root.classList.add('command-canvas');
  dock.className = 'workspace-dock'; dock.setAttribute('aria-label', 'Command modules');
  dock.innerHTML = names.map(name => `<button type="button" data-workspace-toggle="${name}" aria-controls="workspace-${name}" aria-pressed="false">${icon(name)}<span>${{comms:'Comms',ops:'Ops',tasks:'Tasks',layers:'Layers'}[name]}</span></button>`).join('') +
    `<button type="button" data-workspace-focus aria-pressed="false">${icon('focus')}<span>Map only</span></button><button type="button" data-workspace-reset aria-label="Reset panel layout" title="Reset panel layout">${icon('reset')}</button>`;
  const save = () => { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* Private browsing: keep this session usable. */ } };
  const put = (panel, position) => { panel.style.left = `${position.x}px`; panel.style.top = `${position.y}px`; panel.style.right = 'auto'; };
  function fit(panel) {
    if (panel.hidden || !root.clientWidth) return;
    const name = panel.dataset.workspacePanel;
    const position = state.positions[name] || {x: name === 'comms' ? root.clientWidth-panel.offsetWidth-16 : 16, y: name === 'comms' ? 16 : 72};
    put(panel, clampPanel(position, {width:root.clientWidth,height:root.clientHeight}, {width:panel.offsetWidth,height:panel.offsetHeight}));
  }
  function sync() {
    for (const [name, panel] of panels) {
      const enabled = name !== 'tasks' || tasksEnabled;
      const visible = enabled && !focused && (name === 'layers' ? !$('intelPacksPanel').classList.contains('hidden') : state.open.includes(name));
      panel.hidden = !visible;
      const button = dock.querySelector(`[data-workspace-toggle="${name}"]`);
      button.setAttribute('aria-pressed', String(visible));
      button.hidden = !enabled;
      fit(panel);
    }
    root.classList.toggle('workspace-map-only', focused);
    dock.querySelector('[data-workspace-focus]').setAttribute('aria-pressed', String(focused));
  }
  function setOpen(name, open) {
    focused = false;
    if (name === 'layers') {
      if (open === $('intelPacksPanel').classList.contains('hidden')) $('intelPacksToggle').click();
    } else {
      state.open = state.open.filter(n => n !== name);
      if (open) state.open.push(name);
    }
    if (open) panels.get(name).style.zIndex = ++layer;
    sync(); save();
  }
  function wrap(name, title, children) {
    const panel = document.createElement('section');
    panel.id = `workspace-${name}`; panel.className = 'workspace-panel'; panel.dataset.workspacePanel = name;
    panel.setAttribute('aria-label', title);
    panel.innerHTML = `<header class="workspace-panel-head"><button type="button" class="workspace-grip" aria-label="Move ${title} panel using drag or arrow keys">${icon('grip')}<span>${title}</span></button><button type="button" class="workspace-close" aria-label="Close ${title}">${icon('close')}</button></header>`;
    const content = document.createElement('div');content.className='workspace-panel-content';content.append(...children);panel.append(content);root.append(panel);panels.set(name,panel);
    panel.querySelector('.workspace-close').onclick = () => { setOpen(name,false);dock.querySelector(`[data-workspace-toggle="${name}"]`).focus(); };
    const handle = panel.querySelector('.workspace-grip'); let drag;
    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      drag = {x:e.clientX,y:e.clientY,left:panel.offsetLeft,top:panel.offsetTop};
      handle.setPointerCapture(e.pointerId);panel.style.zIndex=++layer;panel.classList.add('is-dragging');
    });
    handle.addEventListener('pointermove', e => {
      if (!drag) return;
      state.positions[name] = clampPanel({x:drag.left+e.clientX-drag.x,y:drag.top+e.clientY-drag.y}, {width:root.clientWidth,height:root.clientHeight}, {width:panel.offsetWidth,height:panel.offsetHeight});
      put(panel,state.positions[name]);
    });
    const stop = () => {if(!drag)return;drag=null;panel.classList.remove('is-dragging');save();};
    handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop);handle.addEventListener('lostpointercapture',stop);
    handle.addEventListener('keydown',e=>{
      const delta={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[e.key];if(!delta)return;
      e.preventDefault();state.positions[name]=clampPanel({x:panel.offsetLeft+delta[0],y:panel.offsetTop+delta[1]}, {width:root.clientWidth,height:root.clientHeight},{width:panel.offsetWidth,height:panel.offsetHeight});put(panel,state.positions[name]);save();
    });
    return panel;
  }

  // Team-management UI belongs in the team picker, not over the map.
  const teamBlock = root.querySelector('.team-block');
  teamBlock.classList.add('workspace-team-details');
  $('commandTeamsPanel').insertBefore(teamBlock, $('commandTeamsList'));
  teamBlock.append(root.querySelector('.rail-footer'));
  const rail = root.querySelector('.rail');
  rail.querySelector('.section-rule').remove();rail.querySelector('.rail-section-head').remove();
  const review=document.createElement('button');review.type='button';review.className='workspace-review';review.textContent='Review team members';review.onclick=openMembers;
  rail.prepend(review);
  wrap('ops','Operators',[rail]);
  const timeline=root.querySelector('.timeline'),comms=root.querySelector('.command-comms');
  const sender=$('commandSenderBadge');comms.querySelector('.module-head').hidden=true;
  const commsPanel=wrap('comms','Comms',[timeline,comms]);
  commsPanel.querySelector('.workspace-panel-head').insertBefore(sender,commsPanel.querySelector('.workspace-close'));
  wrap('tasks','Tasks',[$('commandTasksModule')]);
  wrap('layers','Map layers',[$('intelPacksPanel')]);
  root.querySelector('.right-stack').remove();root.append(dock);
  // The existing Layers button owns opening, rendering and provider preferences.
  const toolbar=document.createElement('div');toolbar.className='workspace-map-tools';
  toolbar.append($('intelPacksToggle'));
  const draw=document.createElement('button');draw.type='button';draw.className='workspace-draw';draw.innerHTML=icon('draw');draw.title='Add marker or draw';draw.setAttribute('aria-label','Add marker or draw');draw.onclick=openDraw;toolbar.append(draw);root.append(toolbar);
  $('commandTeamsToggle').classList.add('workspace-team-picker');
  $('commandMessage').setAttribute('aria-label','Message the team');
  $('commandMessage').rows=1;
  dock.addEventListener('click',e=>{
    const button=e.target.closest('button');if(!button)return;
    if(button.dataset.workspaceToggle){const name=button.dataset.workspaceToggle;setOpen(name,panels.get(name).hidden);}
    else if(button.hasAttribute('data-workspace-focus')){
      focused=!focused;sync();
    }else if(button.hasAttribute('data-workspace-reset')){
      state=readWorkspace(null);focused=false;
      if(!$('intelPacksPanel').classList.contains('hidden'))$('intelPacksToggle').click();
      sync();save();
    }
  });
  new MutationObserver(()=>{if(!$('intelPacksPanel').classList.contains('hidden'))focused=false;sync();}).observe($('intelPacksPanel'),{attributes:true,attributeFilter:['class']});
  const observer=new ResizeObserver(()=>{panels.forEach(fit);resizeMap();});observer.observe(root);
  sync();
  return {update({active,name,tasks,admin}) {
    tasksEnabled=tasks;review.hidden=!admin;
    document.body.classList.toggle('command-canvas-active',active);
    $('commandTeamsToggle').textContent=`${name || 'Teams'} ▾`;
    $('commandTeamsToggle').setAttribute('aria-label',`Teams: ${name || 'choose team'}`);
    sync();
  }};
}
