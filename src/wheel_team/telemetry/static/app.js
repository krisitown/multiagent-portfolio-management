'use strict';
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = String(text); return n; };
const num = x => typeof x === 'number' && Number.isFinite(x) ? x.toLocaleString(undefined, {maximumFractionDigits: 1}) : '—';
const duration = x => x == null || !Number.isFinite(x) ? '—' : x >= 60 ? `${Math.floor(x/60)}m ${Math.floor(x%60)}s` : `${x.toFixed(1)}s`;
const time = x => x ? new Date(x).toLocaleTimeString(undefined, {hour12: false}) : '—';
const seconds = x => x ? Date.parse(x)/1000 : null;
const roleName = i => i.role === 'team_lead' ? 'Team lead' : i.role === 'wheel_expert' ? `Wheel expert · ${i.id.split(':').slice(1).join(':')}` : i.id.startsWith('analyst:') ? i.id.split(':').slice(1,-1).join(':') : i.id;
const stateClass = s => /failed|error|partial|incomplete|exhausted|cancel|deadline/.test(s || '') ? 'issue' : /success|complete/.test(s || '') ? 'success' : 'active';
let run = null, selected = null, revision = null, refreshId = 0, connected = false, lastSuccess = null;
let metricsHistory = [], toolCache = new Map(), selectedDetail = null;
async function api(path) { const response = await fetch(path, {cache:'no-store', credentials:'same-origin'}); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
function card(label, value, note, color='') { const n=el('div','card');n.append(el('div','card-label',label), el('div',`card-value ${color}`,value), el('div','card-note',note));return n; }
function mini(label, value) { const n=el('div');n.append(el('div','mini-label',label),el('div','mini-value',value));return n; }
function details(key, title, text, open=false, narrative=false) { const d=el('details');d.dataset.key=key;d.open=open;d.append(el('summary','',title),el('pre',narrative?'narrative':'',text));return d; }
function totals() {
  const t=run.totals, done=!!run.completion_reason;
  const successes=run.invocations.filter(i=>stateClass(i.status)==='success').length;
  const latencies=run.invocations.flatMap(i=>i.tools).map(t=>t.elapsed_seconds).filter(x=>typeof x==='number').sort((a,b)=>a-b);
  const median=latencies.length ? latencies[Math.floor(latencies.length/2)] : null;
  $('cards').replaceChildren(
    card(done?'Missing model finishes':'Unclosed model starts',num(t.unclosed_model_starts),done?'Trace closure gaps · not active requests':`Trace only · ${num(t.waiting_tools)} tools / batches pending`,done?'':'mint'),
    card('Invocations',num(t.invocations),`${successes} successful · ${t.peak_concurrency==null?'trace overlap unknown':'peak traced overlap '+num(t.peak_concurrency)}`),
    card('Reported output tokens',num(t.known_output_tokens),`${t.known_usage_calls} usage records · ${t.unknown_usage_calls} unknown`),
    card('Peak input tokens',num(t.peak_prompt_tokens),`${num(t.known_input_tokens)} summed input tokens`),
    card('Tool wall latency',duration(median),`Median · ${t.tool_calls} observed calls`),
    card('Tool issues',num(t.transport_failures + t.nested_affected_calls),`${t.transport_failures} transport · ${t.nested_affected_calls} nested*`,t.transport_failures||t.nested_affected_calls?'amber':'')
  );
  $('cards').children[0].title=t.concurrency_scope;
  $('cards').children[1].title=t.model_lifecycle_complete?'Peak overlap of fully paired SDK hook intervals; includes waiting.':'Peak overlap unavailable: one or more model lifecycle boundaries are missing.';
  $('cards').lastChild.title='Transport and nested counts may overlap; nested statuses do not prove a usable source.';
}
function elapsed() {
  if (!run) return;
  const start=seconds(run.started_at), end=seconds(run.completed_at) || Date.now()/1000;
  const wall=start == null ? null : Math.max(0,end-start);
  $('elapsed').textContent=`${duration(wall)} elapsed`;
  const budget=run.manifest?.limits?.deadline_seconds;
  $('budget').textContent=run.completion_reason ? `Finished · ${run.completion_reason}` : budget && wall != null ? `${duration(Math.max(0,budget-wall))} budget left` : 'Budget unavailable';
  $('last-update').textContent=lastSuccess ? `Observed ${time(lastSuccess)}` : 'Waiting';
}
function lanes() {
  const search=$('search').value.toLowerCase(), filter=$('status-filter').value;
  const visible=run.invocations.filter(i=>JSON.stringify([i.id,i.role,i.tools.map(t=>t.name)]).toLowerCase().includes(search) && (filter==='all' || filter==='success'&&stateClass(i.status)==='success' || filter==='issues'&&stateClass(i.status)==='issue' || filter==='active'&&stateClass(i.status)==='active'));
  $('lanes').replaceChildren();$('lane-count').textContent=run.invocations.length;$('empty').hidden=!!visible.length;
  if (!visible.length && run.invocations.length) $('empty').textContent='No invocations match these filters.';
  const begin=seconds(run.started_at) || Math.min(...run.invocations.flatMap(i=>i.generations).map(g=>seconds(g.started_at)).filter(Boolean));
  const end=seconds(run.completed_at)||Date.now()/1000, span=Math.max(1,end-begin);
  for (const i of visible) {
    const lane=el('button',`lane ${selected===i.id?'selected':''}`);lane.type='button';lane.setAttribute('aria-pressed',String(selected===i.id));
    const top=el('div','lane-top');top.append(el('span','lane-name',roleName(i)),el('span',`badge ${stateClass(i.status)}`,i.status));
    lane.append(top,el('div','lane-sub',i.id));
    const track=el('div','lane-track');track.setAttribute('aria-label','Model generation intervals from recorded UTC timestamps');
    for (const g of i.generations) {
      const start=seconds(g.started_at), stop=seconds(g.completed_at)||end;
      if (start==null) continue;
      const bar=el('span',`segment ${g.status==='generating/waiting'?'active':g.status==='failed'?'failed':''}`);
      bar.style.left=`${Math.min(100,Math.max(0,(start-begin)/span*100))}%`;bar.style.width=`${Math.min(100,Math.max(.4,(stop-start)/span*100))}%`;
      bar.title=`Generation ${g.generation} · ${g.status} · ${duration(g.elapsed_seconds)}`;track.append(bar);
    }
    lane.append(track);
    const stats=el('div','lane-stats');stats.append(el('span','',`${i.generations.length} generations`),el('span','',`${i.tools.length} tools`),el('span','',`${num(i.output_words)} ${i.output_kind==='runtime_fallback'?'fallback words':'saved words'}`));lane.append(stats);
    lane.addEventListener('click',()=>{selected=i.id;selectedDetail=null;lanes();inspect();});$('lanes').append(lane);
  }
  $('activity-dot').classList.toggle('idle',run.completion_reason || !run.totals.unclosed_model_starts);
}
async function inspect() {
  if (!selected) return;
  const current=selected, id=++refreshId;
  try {
    const i=await api(`/api/invocation?id=${encodeURIComponent(current)}`);
    if (id!==refreshId || current!==selected) return;
    selectedDetail=i;renderInspector(i);
  } catch (_) { if (current===selected) $('selected-status').textContent='Detail unavailable'; }
}
function renderInspector(i) {
  const old=new Map([...$('inspector-content').querySelectorAll('details')].map(d=>[d.dataset.key,d.open]));
  const scrolls=new Map([...$('inspector-content').querySelectorAll('details pre')].map(n=>[n.parentElement.dataset.key,n.scrollTop]));
  $('selected-title').textContent=roleName(i);$('selected-status').textContent=i.status;$('selected-status').className=`pill ${stateClass(i.status)==='issue'?'issue':'neutral'}`;
  const content=$('inspector-content');content.replaceChildren();
  const measured=i.generations.filter(g=>g.usage), inputs=measured.map(g=>g.usage.inputTokens).filter(x=>typeof x==='number'), outputs=measured.map(g=>g.usage.outputTokens).filter(x=>typeof x==='number'), input=inputs.reduce((a,b)=>a+b,0), output=outputs.reduce((a,b)=>a+b,0);
  const grid=el('div','inspector-grid');grid.append(mini('INPUT TOKENS · REPORTED',inputs.length?num(input):'—'),mini('OUTPUT TOKENS · REPORTED',outputs.length?num(output):'—'),mini(i.output_kind==='runtime_fallback'?'RUNTIME FALLBACK WORDS':'SAVED OUTPUT WORDS',num(i.output_words)),mini('INVOCATION INPUT · CHARS',num(i.handoff_chars)));content.append(grid);
  const meta=el('p','detail-meta',`${i.id}\nLast activity ${time(i.last_activity)} · ${i.handoff_provenance || 'Full invocation input size unavailable'}${i.correlation?' · '+i.correlation:''}`);content.append(meta);
  if (i.parent_call_id) content.append(el('p','detail-meta',`Parent call ${i.parent_call_id}`));
  if (i.task) content.append(details('task','Task / consultation question',i.task,i.task.length<400));
  if (i.handoff_question_chars != null) content.append(el('p','detail-meta',`Consultation question: ${num(i.handoff_question_chars)} characters. Full handoff also includes host-selected records and reports.`));
  if (i.output) content.append(details('output',`${i.output_label || 'Saved output · origin unverified'} · ${num(i.output_chars)} characters / ${num(i.output_words)} words`,i.output,false,true));
  else content.append(el('p','detail-meta','No completed narrative has been saved yet. Live public text may be incomplete.'));
  content.append(el('p','detail-meta',`Output provenance: ${i.output_provenance}. Presentation redacts recognizable credentials; source bytes remain untouched.`));
  const textGenerations=i.generations.filter(g=>g.text);
  if (textGenerations.length) {
    const group=el('div');group.append(el('h3','section-title','Observed public assistant messages'));
    for (const g of textGenerations) group.append(details(`text-${g.generation}`,`Generation ${g.generation} · ${g.text_final?'completed message':'incomplete observation'} · ${num(g.text.length)} chars`,g.text,g.status==='generating/waiting',true));content.append(group);
  }
  content.append(el('h3','section-title',`Model calls · ${i.generations.length}`));
  const generations=el('div');
  for (const g of i.generations) {
    const text=`Status: ${g.status}\nCall wall time: ${duration(g.elapsed_seconds)} (instrumented monotonic elapsed)\nInput tokens: ${num(g.usage?.inputTokens)}\nOutput tokens: ${num(g.usage?.outputTokens)}\nStop reason: ${g.stop_reason || 'unavailable'}\nOutput / call wall second: ${g.elapsed_seconds>0 && g.usage?.outputTokens!=null?num(g.usage.outputTokens/g.elapsed_seconds):'unavailable'}\nTTFT / prefill / decode speed: unavailable per request\nUTC start: ${g.started_at||'unavailable'}\nUTC finish: ${g.completed_at||'unavailable'}`;
    generations.append(details(`generation-${g.generation}`,`#${g.generation} · ${g.status} · ${duration(g.elapsed_seconds)} · ${num(g.usage?.outputTokens)} output tokens`,text));
  }content.append(generations);
  content.append(el('h3','section-title',`Tool history · ${i.tools.length}`));
  const tools=el('div','tool-list');
  for (const t of i.tools) {
    const statuses=(t.nested_statuses||[]).join(', '), key=`tool-${t.call_id}`;
    const d=details(key,`${t.name} · transport: ${t.status||'unknown'} · ${duration(t.elapsed_seconds)}${statuses?' · nested: '+statuses:''}`,'Expand to load private input and return.');
    const load=async()=>{
      if (!d.open || d.dataset.loaded) return;
      try {
        const cacheKey=`${i.id}/${t.call_id}/${t.status}`;
        const full=toolCache.get(cacheKey)||await api(`/api/tool?invocation=${encodeURIComponent(i.id)}&call=${encodeURIComponent(t.call_id)}`);
        if(t.status!=='waiting')toolCache.set(cacheKey,full);
        d.querySelector('pre').textContent=JSON.stringify(full,null,2);d.dataset.loaded='true';
      } catch (_) {d.querySelector('pre').textContent='Tool payload unavailable; reconnecting may restore it.';}
    };
    d.addEventListener('toggle',load);tools.append(d);
  }content.append(tools);
  for (const d of content.querySelectorAll('details')) {if(old.has(d.dataset.key)) d.open=old.get(d.dataset.key);const pre=d.querySelector('pre');if(pre&&scrolls.has(d.dataset.key))pre.scrollTop=scrolls.get(d.dataset.key);}
}
function charts() {
  const calls=run.invocations.flatMap(i=>i.generations.map(g=>({...g,invocation:i.id})));
  for (const [id,field] of [['prompt-chart','input'],['generation-chart','output']]) {
    const chart=$(id);chart.replaceChildren();
    const rows=calls.map(g=>({g,value:field==='input'?g.usage?.inputTokens:g.elapsed_seconds>0&&g.usage?.outputTokens!=null?g.usage.outputTokens/g.elapsed_seconds:null})).filter(x=>x.value!=null);
    if(!rows.length){chart.append(el('span','chart-empty','No reported measurement yet'));continue;}
    const max=Math.max(1,...rows.map(x=>x.value));
    for(const {g,value} of rows){const bar=el('div',`chart-bar ${field==='output'?'output':''}`);bar.style.height=`${Math.max(1,value/max*100)}%`;bar.title=`${g.invocation} · generation ${g.generation}\n${num(value)} ${field==='input'?'input tokens':'output tokens / call wall second'}`;chart.append(bar);}
  }
}
function timeline() {
  if(!run)return;
  const search=$('event-search').value.toLowerCase(),role=$('event-role').value;
  const rows=run.events.filter(e=>(role==='all'||e.role===role)&&JSON.stringify(e).toLowerCase().includes(search)).slice(-150).reverse();
  $('event-count').textContent=`${run.events.length} retained · latest 150 shown`;$('timeline').replaceChildren();
  for(const e of rows){const tr=el('tr');const text=e.name?`${e.event.replaceAll('_',' ')} · ${e.name}`:e.event.replaceAll('_',' ');for(const x of [time(e.at),e.invocation_id||e.job_id||'run',text,[e.status||e.outcome||e.completion_reason,e.generation!=null?'#'+e.generation:'',e.elapsed_seconds!=null?duration(e.elapsed_seconds):''].filter(Boolean).join(' · ')])tr.append(el('td','',x));$('timeline').append(tr);}
}
function render() {
  $('run-id').textContent=run.manifest?.run_id||run.run_name;
  $('mode').textContent=run.completion_reason?'Replay':'Live view';
  $('warnings').textContent=[...run.warnings,...(run.observer_health||[]).filter(h=>h.dropped_events||h.io_failures).map(h=>`Observer ${h.invocation_id}: ${h.dropped_events} dropped observations, ${h.io_failures} IO failures`)].join('\n');
  $('report-proof').textContent=run.report?`Report SHA-256 ${run.report.sha256.slice(0,16)}… · ${num(run.report.bytes)} original bytes`:'Final report pending';
  if(run.report)$('report-proof').title=run.report.sha256;
  if(!selected&&run.invocations.length)selected=run.invocations[0].id;
  if(selected&&!run.invocations.some(i=>i.id===selected))selected=run.invocations[0]?.id||null;
  totals();elapsed();lanes();charts();timeline();inspect();
}
async function poll() {
  try {
    const next=await api('/api/run'+(revision!=null?'?after='+revision:''));
    lastSuccess=new Date().toISOString();connected=true;
    $('connection').textContent='Connected';$('connection').className='pill';
    if(!next.unchanged){run=next;revision=next.revision;render();}else elapsed();
  }catch(_){connected=false;$('connection').textContent='Disconnected · retrying';$('connection').className='pill issue';}
  setTimeout(poll,1000);
}
async function pollMetrics() {
  try {
    const m=await api('/api/metrics');$('metrics-status').textContent=m.available?`Server-wide · ${m.rate_interval_seconds?num(m.rate_interval_seconds)+'s interval':'waiting for rate sample'}`:m.reason||'Unavailable';
    const entries=[['Running requests',m.running],['Waiting requests',m.waiting],['KV utilization',m.kv_percent==null?null:num(m.kv_percent)+'%'],['Generation · tok/s',m.generation_tok_s],['Local prompt · tok/s',m.local_compute_prompt_tok_s],['Cache hits · tok/s',m.cache_hit_tok_s]];
    $('server-cards').replaceChildren(...entries.map(([label,value])=>{const c=mini(label,typeof value==='string'?value:num(value));c.className='server-card';return c;}));
    $('server-note').textContent=(m.limitations||'Optional read-only server metrics. Rates and missing values stay unavailable.')+` Sampled every 5 seconds. Preemptions since server start: ${num(m.preemptions_total)}.`;
    if(m.available){metricsHistory.push(m);metricsHistory=metricsHistory.slice(-60);drawServer();}
  }catch(_){$('metrics-status').textContent='Unavailable';}
  setTimeout(pollMetrics,5000);
}
function drawServer(){
  const container=$('server-chart');container.replaceChildren();
  if(metricsHistory.length<2)return;
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 65');svg.setAttribute('role','img');svg.setAttribute('aria-label','Server-wide sampled token rates; blue prompt compute, green generation');
  const max=Math.max(1,...metricsHistory.flatMap(x=>[x.generation_tok_s||0,x.local_compute_prompt_tok_s||0]));
  for(const [key,color] of [['generation_tok_s','#76d6b4'],['local_compute_prompt_tok_s','#7caeeb']]){
    const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('fill','none');line.setAttribute('stroke',color);line.setAttribute('stroke-width','2');line.setAttribute('points',metricsHistory.map((m,index)=>m[key]==null?'':`${index/(metricsHistory.length-1)*1000},${62-m[key]/max*57}`).filter(Boolean).join(' '));svg.append(line);
  }container.append(svg);
}
for(const id of ['search','status-filter'])$(id).addEventListener(id==='search'?'input':'change',()=>run&&lanes());
for(const id of ['event-search','event-role'])$(id).addEventListener(id==='event-search'?'input':'change',timeline);
setInterval(elapsed,1000);poll();pollMetrics();
