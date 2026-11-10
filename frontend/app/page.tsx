'use client';
import {useCallback,useEffect,useMemo,useState} from 'react';
import {Activity,ArrowLeft,ArrowRight,BookOpen,Check,ChevronDown,CircleHelp,Clock3,Database,Download,FileSearch,FileText,FlaskConical,Layers3,LoaderCircle,Plus,RefreshCw,Search,Settings2,ShieldCheck,Sparkles,Trash2,TriangleAlert,X} from 'lucide-react';
import {api,Artifact,Paper,Project,Provider,Run,STAGES} from '@/lib/api';
const stageTitle:Record<string,string>={search:'Search',extraction:'Extraction',organization:'Organization',synthesis:'Synthesis'};
const conditionTitles:Record<string,string>={search_discovery:'Search and save papers',without_intermediate_human_supervision:'Condition A · Automatic',with_human_supervision_all_stages:'Condition B · Human review',full_audit:'Legacy: review every stage',targeted_retry:'Legacy: Search + final review',endpoint_oversight:'Legacy: final synthesis only',midpoint_gating:'Legacy: after Extraction'};
function ago(date:string){const n=Math.max(0,Math.floor((Date.now()-new Date(date).getTime())/60000));return n<1?'just now':n<60?`${n}m ago`:`${Math.floor(n/60)}h ago`}
export default function Home(){
 const [apiOnline,setApiOnline]=useState(false);
 const [uploading,setUploading]=useState(false),[testingModel,setTestingModel]=useState(''),[modelResults,setModelResults]=useState<Record<string,string>>({}),[projects,setProjects]=useState<Project[]>([]),[providers,setProviders]=useState<Provider[]>([]),[active,setActive]=useState<Project|null>(null),[run,setRun]=useState<Run|null>(null),[papers,setPapers]=useState<Paper[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[createOpen,setCreateOpen]=useState(false),[editMode,setEditMode]=useState(false),[modelCondition,setModelCondition]=useState('openai'),[note,setNote]=useState(''),[correctionJson,setCorrectionJson]=useState(''),[reviewStartedAt,setReviewStartedAt]=useState<number|null>(null),[tab,setTab]=useState<'overview'|'sources'|'artifacts'|'research_data'>('overview'),[experimentLabel,setExperimentLabel]=useState(''),[condition,setCondition]=useState('without_intermediate_human_supervision'),[replicate,setReplicate]=useState('1'),[randomSeed,setRandomSeed]=useState('42'),[temperature,setTemperature]=useState('0'),[selectedSourceIds,setSelectedSourceIds]=useState<string[]>([]);
 const refresh=useCallback(async(projectId?:string,runId?:string)=>{setLoading(true);setError('');try{const ps=await api<Project[]>('/projects');setProjects(ps);if(projectId){const p=await api<Project>(`/projects/${projectId}`);setActive(p);if(runId){const r=await api<Run>(`/runs/${runId}`);setRun(r);setPapers(r.papers||[])}else {const latest=(p as any).runs?.[0];if(latest){const r=await api<Run>(`/runs/${latest.id}`);setRun(r);setPapers(r.papers||[])}else{setRun(null);setPapers(await api<Paper[]>(`/projects/${p.id}/papers`))}}}else if(ps.length){const p=ps[0];setActive(p);const detail=await api<any>(`/projects/${p.id}`);if(detail.runs?.length){const r=await api<Run>(`/runs/${detail.runs[0].id}`);setRun(r);setPapers(r.papers||[])}else{setRun(null);setPapers(await api<Paper[]>(`/projects/${p.id}/papers`))}}else{setActive(null);setRun(null);setPapers([])}}catch(e:any){setError(e.message)}finally{setLoading(false)}},[]);
 useEffect(()=>{refresh();api<{providers:Provider[]}>('/models').then(x=>setProviders(x.providers)).catch(()=>{});const check=()=>fetch('/health',{cache:'no-store'}).then(r=>setApiOnline(r.ok)).catch(()=>setApiOnline(false));check();const timer=setInterval(check,10000);return()=>clearInterval(timer)},[refresh]);
 useEffect(()=>{if(!run||!['queued','running'].includes(run.status))return;const timer=setInterval(()=>api<Run>(`/runs/${run.id}`).then(r=>{setRun(r);setPapers(r.papers||[])}).catch(()=>{}),2000);return()=>clearInterval(timer)},[run?.id,run?.status]);
 useEffect(()=>{const selected=providers.find(p=>p.id===modelCondition&&p.configured);if(!selected){const first=providers.find(p=>p.configured);if(first)setModelCondition(first.id)}},[providers,modelCondition]);
 useEffect(()=>{if(!run)return;if(run.models)setModelCondition(run.models.extraction||Object.values(run.models)[0]||'openai');if(run.settings){setExperimentLabel(run.settings.experiment_label||'');setCondition(run.settings.condition==='with_human_supervision_all_stages'?'with_human_supervision_all_stages':'without_intermediate_human_supervision');setReplicate(run.settings.replicate||'1');setRandomSeed(String(run.settings.random_seed??42));setTemperature(String(run.settings.temperature??0))}},[run?.id]);
 const configured=providers.filter(p=>p.configured);const selectedProvider=providers.find(p=>p.id===modelCondition);const frozenPaperCount=papers.filter(p=>p.in_frozen_corpus!==false).length;const canStart=!busy&&apiOnline&&!['queued','running','awaiting_review'].includes(run?.status||'');const waiting=run?.artifacts?.find(a=>a.status==='pending_review');const currentCondition=run?.settings?.condition||condition;
 useEffect(()=>{setCorrectionJson('');setReviewStartedAt(waiting?Date.now():null);if(waiting?.stage==='search')setSelectedSourceIds((waiting.content?.papers||[]).map((p:any)=>p.id).filter(Boolean))},[waiting?.id]);
 async function createProject(form:FormData){setBusy(true);try{const payload=JSON.stringify({title:form.get('title'),question:form.get('question'),scope:form.get('scope')});const p=editMode&&active?await api<Project>(`/projects/${active.id}`,{method:'PATCH',body:payload}):await api<Project>('/projects',{method:'POST',body:payload});setCreateOpen(false);await refresh(p.id,editMode?run?.id:undefined)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function deleteProject(project:Project){if(!window.confirm(`Delete “${project.title}” and permanently remove its saved papers, runs, and experiment records?`))return;setBusy(true);setError('');try{await api(`/projects/${project.id}`,{method:'DELETE'});const remaining=projects.filter(p=>p.id!==project.id);setProjects(remaining);if(active?.id===project.id){setActive(null);setRun(null);setPapers([]);if(remaining.length)await refresh(remaining[0].id)}else await refresh(active?.id,run?.id)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function submitRun(runCondition:string,sourceMode:'saved_corpus'|'openalex'){if(!active)return;if(runCondition!=='search_discovery'&&!frozenPaperCount){setError('Search OpenAlex and approve a paper set before starting Condition A or B.');return}setBusy(true);setError('');try{const modelMap={search:modelCondition,extraction:modelCondition,organization:modelCondition,synthesis:modelCondition};const r=await api<Run>(`/projects/${active.id}/runs`,{method:'POST',body:JSON.stringify({models:modelMap,max_papers:25,source_mode:sourceMode,experiment_label:experimentLabel,condition:runCondition,replicate,random_seed:Number(randomSeed),temperature:Number(temperature)})});await refresh(active.id,r.id)}catch(e:any){setError(e.message);await refresh(active.id)}finally{setBusy(false)}}
 async function startDiscovery(){await submitRun('search_discovery','openalex')}
 async function startRun(){await submitRun(condition,'saved_corpus')}
 async function checkModel(id:string){setTestingModel(id);setModelResults({...modelResults,[id]:''});try{const r=await api<{ok:boolean;model:string}>(`/models/${id}/check`,{method:'POST',body:JSON.stringify({})});setModelResults({...modelResults,[id]:`Connected · ${r.model}`})}catch(e:any){setModelResults({...modelResults,[id]:e.message})}finally{setTestingModel('')}}
 async function review(a:Artifact,action:'approve'|'retry'|'return'|'restart_from_extraction'){if(!run)return;setBusy(true);setError('');try{let correction:any=undefined;if(correctionJson.trim()){correction=JSON.parse(correctionJson);if(!correction||Array.isArray(correction)||typeof correction!=='object')throw new Error('Correction must be a JSON object.')}await api(`/runs/${run.id}/artifacts/${a.id}/review`,{method:'POST',body:JSON.stringify({action,note,correction,review_duration_ms:reviewStartedAt?Date.now()-reviewStartedAt:undefined,source_paper_ids:waiting?.stage==='search'?selectedSourceIds:undefined})});setNote('');setCorrectionJson('');await refresh(active?.id,run.id)}catch(e:any){setError(e instanceof SyntaxError?'Correction must be valid JSON.':e.message);await refresh(active?.id,run.id)}finally{setBusy(false)}}
 const latestByStage=useMemo(()=>{const m:Record<string,Artifact>={};for(const a of run?.artifacts||[])m[a.stage]=a;return m},[run]);
 return <div className="shell">
  <aside className="sidebar"><div className="brand"><div className="brand-mark"><Sparkles size={19}/></div><span>Synthesis</span><span className="brand-tag">RESEARCH</span></div><div className="workspace"><span className="workspace-dot"/>Research workspace<ChevronDown size={14}/></div><div className="side-label">WORKSPACE</div><button className="side-link selected"><Layers3 size={17}/> Literature reviews</button><button className="side-link"><Database size={17}/> Source library <span className="side-count">{papers.length}</span></button><button className="side-link"><Activity size={17}/> Activity log</button><div className="side-divider"/><div className="projects-heading"><span className="side-label">PROJECTS</span><button className="icon-button tiny" onClick={()=>{setEditMode(false);setCreateOpen(true)}} aria-label="New project"><Plus size={16}/></button></div><div className="project-nav">{projects.map(p=><div key={p.id} className={`project-row ${active?.id===p.id?'active':''}`}><button className="project-item" onClick={()=>refresh(p.id)}><span className="project-dot"/><span className="project-title">{p.title}</span></button><button className="project-delete" title={`Delete ${p.title}`} aria-label={`Delete project ${p.title}`} disabled={busy} onClick={()=>void deleteProject(p)}><Trash2 size={14}/></button></div>)}</div><button className="new-project" onClick={()=>{setEditMode(false);setCreateOpen(true)}}><Plus size={16}/> New review</button><div className="sidebar-bottom"><div className="user-avatar">AR</div><div><strong>Researcher</strong><small>Personal workspace</small></div><Settings2 size={17} className="settings-icon"/></div></aside>
  <main className="main"><header className="topbar"><div className="crumb"><span>Workspace</span><span className="slash">/</span><b>{active?.title||'Literature reviews'}</b></div><div className="top-actions"><span className={`connection ${apiOnline?'':'offline'}`}><i/> {apiOnline?'API connected':'API unavailable'}</span><button className="icon-button" title="Refresh" onClick={()=>refresh(active?.id,run?.id)}><RefreshCw size={16}/></button><div className="user-avatar small-avatar">AR</div></div></header>
  {error&&<div className="error-banner"><TriangleAlert size={16}/><span>{error}</span><button onClick={()=>setError('')}><X size={15}/></button></div>}
  {loading&&!active?<div className="loading"><LoaderCircle className="spin"/> Loading workspace…</div>:!active?<div className="empty-state"><div className="empty-icon"><BookOpen/></div><p className="eyebrow">YOUR RESEARCH WORKSPACE</p><h1>Turn papers into a<br/>grounded synthesis.</h1><p>Four focused agents gather, extract, organize, and synthesize literature. You stay in control at every review gate.</p><button className="primary" onClick={()=>setCreateOpen(true)}><Plus size={17}/> Create your first review</button><div className="stage-preview">{STAGES.map((s,i)=><div key={s}><span>0{i+1}</span>{stageTitle[s]}{i<3&&<ArrowRight size={14}/>}</div>)}</div></div>:<>
   <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-icon"><FlaskConical size={15}/></span> LITERATURE REVIEW</div><h1>{active.title}</h1><p className="question">{active.question}</p></div></div>
   <div className="metadata-row"><span><ShieldCheck size={15}/>{conditionTitles[currentCondition]}</span><span><FileSearch size={15}/>{papers.filter(p=>p.in_frozen_corpus!==false).length} papers in corpus</span><span><Clock3 size={15}/>{run?`Updated ${ago(run.updated_at||run.created_at)}`:'No run yet'}</span><button onClick={()=>{setEditMode(true);setCreateOpen(true)}}>Edit project <ArrowRight size={13}/></button></div>
   <div className="pipeline-card"><div className="pipeline-top"><div><h2>Workflow pipeline</h2><p>Each agent produces a reviewable artifact before the next stage.</p></div><div className={`run-status ${run?.status||'idle'}`}><i/>{run?.status?.replace('_',' ')||'Not started'}</div></div><div className="pipeline">{STAGES.map((s,i)=>{const a=latestByStage[s];const activeStage=run?.current_stage===s;let state=a?.status==='approved'?'complete':a?.status==='pending_review'?'review':a?'revision':activeStage&&run?.status==='running'?'working':'queued';const Icon=[Search,FileText,Layers3,Sparkles][i];return <div className="stage-wrap" key={s}><div className={`stage-node ${state}`}><div className="stage-icon">{state==='complete'?<Check size={17}/>:state==='working'?<LoaderCircle size={17} className="spin"/>:<Icon size={17}/>}</div><div className="stage-copy"><span className="stage-overline">STAGE 0{i+1}</span><strong>{stageTitle[s]}</strong><small>{state==='complete'?'Approved':state==='review'?'Review needed':state==='working'?'Processing':state==='revision'?'Revision requested':'Waiting'}</small></div>{a&&<button className="artifact-open" onClick={()=>setTab('artifacts')}>v{a.version} <ArrowRight size={12}/></button>}</div>{i<3&&<div className={`pipe-line ${state==='complete'?'done':''}`}><ArrowRight size={14}/></div>}</div>})}</div><div className="pipeline-foot"><span className="pipeline-source"><b>Search source</b> OpenAlex · approved papers are saved before the next agent</span><button className="text-button" onClick={()=>setTab('artifacts')}>View artifacts <ArrowRight size={14}/></button></div></div>
   {run?.status==='failed'&&run.failure&&<div className="run-failure" role="alert"><div className="failure-icon"><TriangleAlert size={20}/></div><div><h2>Run failed during {stageTitle[run.failure.stage]||run.failure.stage}</h2><p>{run.failure.message}</p><small>Failure saved with this run. Check the key/model access or service named in the message, then retry as a new recorded run.</small></div>{run.settings.condition==='search_discovery'?<button className="secondary" disabled={!canStart} onClick={startDiscovery}>Retry paper search</button>:<button className="secondary" disabled={!canStart} onClick={startRun}>Start a new run</button>}</div>}
   <section className="content-card experiment-card simple-experiment-card"><div className="section-head"><div><h2>Run your experiment</h2><p>Choose the model and review condition. OpenAlex is the paper search source.</p></div><FlaskConical size={20}/></div>
    <div className="experiment-control-grid">
     <div className="experiment-control model-control"><div className="control-card-heading"><span>AI MODEL</span><h3>Shared by all four agents</h3></div><label className="model-select-label"><span>Model</span><select value={modelCondition} disabled={!canStart||!configured.length} onChange={e=>setModelCondition(e.target.value)}>{providers.map(p=><option key={p.id} value={p.id} disabled={!p.configured}>{p.gateway} · {p.model||p.id}{p.configured?'':' · setup needed'}</option>)}</select></label><p>{selectedProvider?.configured?'This model performs Search, Extraction, Organization, and Generation.':'Add the API key and exact model ID to backend/.env, then restart the backend.'}</p><div className="model-test-row"><button className="secondary" disabled={!selectedProvider?.configured||testingModel===modelCondition} onClick={()=>checkModel(modelCondition)}>{testingModel===modelCondition?<LoaderCircle size={15} className="spin"/>:'Test model connection'}</button>{modelResults[modelCondition]&&<span role="status">{modelResults[modelCondition]}</span>}</div></div>
     <div className="experiment-control source-control"><div className="control-card-heading"><span>PAPER SEARCH</span><h3><Search size={19}/> OpenAlex</h3></div><p>OpenAlex finds candidate papers for your research question. Review and freeze the same approved paper set before comparing conditions.</p><div className="corpus-count"><Database size={17}/><strong>{frozenPaperCount}</strong><span>papers in the fixed corpus</span></div></div>
     <fieldset className="experiment-control condition-control"><legend>REVIEW CONDITION</legend><label className={`condition-choice ${condition==='without_intermediate_human_supervision'?'selected':''}`}><input type="radio" name="condition" value="without_intermediate_human_supervision" checked={condition==='without_intermediate_human_supervision'} disabled={!canStart} onChange={()=>setCondition('without_intermediate_human_supervision')}/><span><b>Condition A · Automatic</b><small>Agents proceed without human review between stages.</small></span></label><label className={`condition-choice ${condition==='with_human_supervision_all_stages'?'selected':''}`}><input type="radio" name="condition" value="with_human_supervision_all_stages" checked={condition==='with_human_supervision_all_stages'} disabled={!canStart} onChange={()=>setCondition('with_human_supervision_all_stages')}/><span><b>Condition B · Human review</b><small>You review Search, Extraction, Organization, and Generation.</small></span></label></fieldset>
    </div>
    {frozenPaperCount===0?<div className="corpus-next-step"><div><b>Step 1 · Find and approve papers</b><p>Search OpenAlex, review the results, and save the papers before starting Condition A or B.</p></div><button className="primary" disabled={!canStart||!selectedProvider?.configured} onClick={startDiscovery}>{busy?<LoaderCircle size={17} className="spin"/>:<Search size={17}/>} {run?.settings.condition==='search_discovery'&&run.status==='failed'?'Retry paper search':'Search OpenAlex and review papers'}</button></div>:<div className="experiment-launch-row"><label className="pair-field"><span>Pair ID</span><input value={replicate} maxLength={80} onChange={e=>setReplicate(e.target.value)} placeholder="RQ1-pair-01"/><small>Reuse this ID for the matching Automatic and Human review runs.</small></label><button className="primary start-experiment-button" disabled={!canStart||!selectedProvider?.configured||!replicate.trim()} onClick={startRun}>{busy?<LoaderCircle size={17} className="spin"/>:<Sparkles size={17}/>} {run?.status==='running'||run?.status==='queued'?'Workflow running':run?.status==='awaiting_review'?'Complete the pending review':`Start ${condition==='without_intermediate_human_supervision'?'Condition A · Automatic':'Condition B · Human review'}`}</button></div>}
    <details className="advanced-settings"><summary>Optional settings and CSV export</summary><div className="advanced-fields"><label>Study label<input value={experimentLabel} maxLength={240} onChange={e=>setExperimentLabel(e.target.value)} placeholder="e.g. synthesis-study"/></label><label>Temperature<input type="number" min="0" max="2" step="0.1" value={temperature} onChange={e=>setTemperature(e.target.value)}/></label><label>Run seed<input type="number" min="0" max="4294967295" value={randomSeed} onChange={e=>setRandomSeed(e.target.value)}/></label></div><p>Keep these values the same within each A/B pair. The seed is recorded for reproducibility; hosted model output may still vary.</p><div className="experiment-exports"><a href={'/api/v1/projects/'+active.id+'/runs/log.csv'}>Download readable experiment log (CSV)</a><a href={'/api/v1/projects/'+active.id+'/runs/export'}>Download detailed records (JSONL)</a></div></details>
   </section>
   {waiting&&<div className="review-card"><div className="review-heading"><div className="review-icon"><ShieldCheck size={18}/></div><div><div className="review-kicker">HUMAN REVIEW REQUIRED · STAGE 0{STAGES.indexOf(waiting.stage)+1}</div><h2>Review {stageTitle[waiting.stage].toLowerCase()} results</h2><p>Check the agent’s work before it flows to the next stage.</p></div><span className="pending-badge">Needs your review</span></div><ArtifactBody artifact={waiting} papers={papers}/>{waiting.stage==='search'&&<SourceScreen papers={waiting.content?.papers||[]} selectedIds={selectedSourceIds} onChange={setSelectedSourceIds} frozenCorpus={run?.settings?.protocol_version==='2.0'}/>}<label className="note-label" htmlFor="review-note">Reviewer comments <span>{run?.settings?.condition==='with_human_supervision_all_stages'&&waiting.stage!=='search'?'Required when restarting from saved papers':'Optional · saved with the review record'}</span></label><textarea id="review-note" className="review-note" value={note} onChange={e=>setNote(e.target.value)} placeholder="Explain your decision or identify an error…"/><label className="note-label" htmlFor="review-correction">Structured correction <span>Optional JSON fields to save as a new artifact version</span></label><textarea id="review-correction" className="review-note" value={correctionJson} onChange={e=>setCorrectionJson(e.target.value)} placeholder='{"draft": "Corrected synthesis text"} for Generation; use the stage output fields for other stages…'/><div className="review-actions"><button className="secondary" disabled={busy} onClick={()=>review(waiting,'return')}><ArrowLeft size={15}/> Return to previous stage</button><button className="secondary" disabled={busy} onClick={()=>review(waiting,'retry')}><RefreshCw size={15}/> Request revision</button>{run?.settings?.condition==='with_human_supervision_all_stages'&&waiting.stage!=='search'&&<button className="secondary" disabled={busy||note.trim().length<3||correctionJson.trim().length>0} title="Clear the correction field first. Re-run Extraction and every later stage from the same frozen papers" onClick={()=>review(waiting,'restart_from_extraction')}><Database size={15}/> Restart from saved papers at Extraction</button>}<button className="approve" disabled={busy||(waiting.stage==='search'&&selectedSourceIds.length===0&&run?.settings?.protocol_version!=='2.0')} onClick={()=>review(waiting,'approve')}>{busy?<LoaderCircle size={15} className="spin"/>:<Check size={15}/>} Approve & continue <ArrowRight size={15}/></button></div></div>}
   <div className="tab-row"><button className={tab==='overview'?'on':''} onClick={()=>setTab('overview')}>Overview</button><button className={tab==='sources'?'on':''} onClick={()=>setTab('sources')}>Saved papers <span>{papers.length}</span></button><button className={tab==='artifacts'?'on':''} onClick={()=>setTab('artifacts')}>Artifacts <span>{run?.artifacts?.length||0}</span></button><button className={tab==='research_data'?'on':''} onClick={()=>setTab('research_data')}>Evaluation & records</button></div>
   {tab==='overview'&&<><div className="overview-grid"><div className="content-card"><div className="section-head"><div><h3>Research question</h3><p>Study objective and boundaries</p></div><CircleHelp size={16}/></div><div className="question-panel">{active.question}</div>{active.scope&&<div className="scope"><strong>Scope notes</strong><p>{active.scope}</p></div>}</div><div className="content-card compact-card"><div className="section-head"><div><h3>Run summary</h3><p>Current workflow snapshot</p></div><Activity size={16}/></div><div className="stat-line"><span>Run scenario</span><b>{conditionTitles[currentCondition]}</b></div><div className="stat-line"><span>Saved sources</span><b>{papers.length}</b></div><div className="stat-line"><span>Workflow status</span><b className="capitalize">{run?.status?.replace('_',' ')||'Not started'}</b></div></div></div></>}
   {tab==='sources'&&<div className="content-card source-list-card"><div className="section-head"><div><h3>Saved paper library</h3><p>Ground truth for every extraction and citation in this review.</p></div><div className="source-tools"><span className="source-total">{papers.filter(p=>p.in_frozen_corpus!==false).length} in corpus · {papers.length} candidates</span><label className="upload-button">{uploading?<LoaderCircle size={14} className="spin"/>:<Plus size={14}/>} Add PDF<input type="file" accept="application/pdf,.pdf" disabled={uploading} onChange={async e=>{const file=e.target.files?.[0];if(!file||!active)return;setUploading(true);setError('');try{await api(`/projects/${active.id}/papers/upload`,{method:'POST',body:(()=>{const d=new FormData();d.append('file',file);return d})()});await refresh(active.id,run?.id)}catch(err:any){setError(err.message)}finally{setUploading(false);e.target.value=''}}}/></label></div></div>{papers.length?papers.map(p=><PaperRow key={p.id} paper={p}/>):<div className="empty-inline"><Database size={22}/><b>No papers saved yet</b><span>Run the search stage to find relevant sources.</span></div>}</div>}
   {tab==='sources'&&active&&<ReferenceAnnotationPanel project={active} papers={papers}/> }
   {tab==='artifacts'&&<div className="artifact-list">{(run?.artifacts||[]).length?STAGES.flatMap(s=>(run?.artifacts||[]).filter(a=>a.stage===s).slice().reverse()).map(a=><div className="content-card artifact-card" key={a.id}><div className="section-head"><div><div className="artifact-meta">STAGE 0{STAGES.indexOf(a.stage)+1} · VERSION {a.version}</div><h3>{stageTitle[a.stage]}</h3></div><span className={`artifact-status ${a.status}`}>{a.status.replace('_',' ')}</span></div><div className="artifact-info">Generated by <b>{a.model}</b> · {new Date(a.created_at).toLocaleString()}</div><ArtifactBody artifact={a} papers={papers}/></div>):<div className="content-card empty-inline"><FileText size={22}/><b>No artifacts yet</b><span>Run the workflow to create inspectable stage outputs.</span></div>}</div>}
   {tab==='research_data'&&run&&active&&<ResearchDataPanel run={run} project={active} papers={papers}/> }
  </>}
  <footer className="footer"><span>Human judgment at every stage.</span><span>Sources stay traceable · Workflow events are recorded</span></footer>
  </main>{createOpen&&<ProjectDialog close={()=>setCreateOpen(false)} submit={createProject} busy={busy} initial={editMode?active:null}/>}</div>
}
function PaperRow({paper:p}:{paper:Paper}){return <article className="paper-row"><div className="paper-file"><FileText size={18}/></div><div className="paper-main"><a href={p.url||'#'} target="_blank" rel="noreferrer" className="paper-title">{p.title}</a><div className="paper-meta">{p.authors?.slice(0,3).join(', ')}{(p.authors?.length||0)>3?' et al.':''}{p.year?` · ${p.year}`:''}{p.venue?` · ${p.venue}`:''}{p.has_full_text?' · full text available':''}{p.in_frozen_corpus===false?' · excluded from frozen corpus':''}</div>{p.abstract&&<p className="paper-abstract">{p.abstract.slice(0,300)}{p.abstract.length>300?'…':''}</p>}</div><div className="paper-cites"><b>{p.citation_count}</b><span>citations</span></div></article>}
function SourceScreen({papers,selectedIds,onChange,frozenCorpus=false}:{papers:any[];selectedIds:string[];onChange:(ids:string[])=>void;frozenCorpus?:boolean}){const selected=new Set(selectedIds);return <section className="screening-panel" aria-label="Screen search results"><div className="screening-head"><div><b>Review Search results</b><p>{frozenCorpus?'Record relevance decisions; the frozen comparison corpus stays unchanged downstream.':'Uncheck papers that do not meet your inclusion criteria.'}</p></div><span>{selectedIds.length} / {papers.length} included</span></div><div className="screening-list">{papers.map((p:any)=><label className="screening-item" key={p.id}><input type="checkbox" checked={selected.has(p.id)} onChange={e=>onChange(e.target.checked?[...selectedIds,p.id]:selectedIds.filter(id=>id!==p.id))}/><span><b>{p.title}</b><small>Rank {p.relevance_rank||'—'} · {p.year||'Year unknown'}{p.doi?` · ${p.doi}`:''}</small>{p.relevance_note&&<em>Search agent: {p.relevance_note}</em>}{p.abstract&&<em>{p.abstract.slice(0,280)}{p.abstract.length>280?'…':''}</em>}</span></label>)}</div></section>}
function artifactValueText(value:any):string{
 if(value===null||value===undefined)return '';
 if(typeof value==='string'||typeof value==='number'||typeof value==='boolean')return String(value);
 if(Array.isArray(value))return value.map(artifactValueText).filter(Boolean).join(', ');
 if(typeof value==='object'){
  if(typeof value.text==='string')return value.text;
  if(typeof value.claim==='string')return value.claim;
  if(typeof value.summary==='string')return value.summary;
  return JSON.stringify(value);
 }
 return String(value);
}
function ArtifactEvidenceList({label,items}:{label:string;items:any[]}){
 if(!Array.isArray(items)||items.length===0)return null;
 return <div className="theme-evidence-list"><strong>{label}</strong>{items.map((item:any,index:number)=>{
  const text=artifactValueText(item);
  const indices=Array.isArray(item?.claim_indices)?item.claim_indices.filter((n:any)=>Number.isInteger(n)&&n>=0):[];
  return <div className="theme-evidence-item" key={index}><p>{text||'No description supplied.'}</p>{indices.length>0&&<small>Evidence claims: {indices.map((n:number)=>`#${n+1}`).join(', ')}</small>}</div>;
 })}</div>;
}
function ArtifactBody({artifact:a,papers}:{artifact:Artifact;papers:Paper[]}){
 const c=a.content||{};
 if(a.stage==='search')return <div className="artifact-preview">
  <div className="preview-metric"><b>{c.count||0}</b><span>candidates ranked · awaiting human screening</span></div>
  <p>Retrieval: {artifactValueText(c.retrieval_source||c.source_mode)} · ranked by {artifactValueText(c.model||c.provider)}</p>
  <p>Research question: “{artifactValueText(c.query)}”</p>
  <div className="mini-source-list">{(Array.isArray(c.papers)?c.papers:[]).slice(0,3).map((p:any,i:number)=><div key={p.id||i}><FileText size={13}/><span>{artifactValueText(p.title)}</span><small>{artifactValueText(p.year)||'—'}</small></div>)}</div>
 </div>;
 if(a.stage==='extraction')return <div className="artifact-preview">
  <div className="preview-metric"><b>{Array.isArray(c.claims)?c.claims.length:0}</b><span>grounded claims · {c.rejected_claim_count||0} unsupported claims filtered</span></div>
  {(Array.isArray(c.claims)?c.claims:[]).map((x:any,i:number)=><div className="claim" key={i}>
   <p>“{artifactValueText(x.quote)}”</p><b>{artifactValueText(x.claim)}</b>
   <span>Source: {artifactValueText(papers.find(p=>p.id===x.paper_id)?.title||x.paper_id)} · {artifactValueText(x.method||'Method not reported')}</span>
  </div>)}
 </div>;
 if(a.stage==='organization')return <div className="artifact-preview">
  <div className="preview-metric"><b>{Array.isArray(c.themes)?c.themes.length:0}</b><span>themes organized</span></div>
  {(Array.isArray(c.themes)?c.themes:[]).map((theme:any,index:number)=><div className="theme-row" key={index}>
   <b>{artifactValueText(theme?.name)||`Theme ${index+1}`}</b>
   <p>{artifactValueText(theme?.summary)}</p>
   {Array.isArray(theme?.claim_indices)&&theme.claim_indices.length>0&&<small>Supporting claims: {theme.claim_indices.filter((n:any)=>Number.isInteger(n)&&n>=0).map((n:number)=>`#${n+1}`).join(', ')}</small>}
   <ArtifactEvidenceList label="Agreements" items={theme?.agreements}/>
   <ArtifactEvidenceList label="Disagreements" items={theme?.disagreements}/>
   <ArtifactEvidenceList label="Methodological comparisons" items={theme?.methodological_comparisons}/>
   <ArtifactEvidenceList label="Research gaps" items={theme?.gaps}/>
  </div>)}
  {(c.dropped_unreferenced_themes||c.dropped_unreferenced_comparisons)?<small className="label-muted">Validation filtered {c.dropped_unreferenced_themes||0} unsupported themes and {c.dropped_unreferenced_comparisons||0} unsupported comparisons.</small>:null}
 </div>;
 return <div className="draft-preview">
  <button className="export-button" onClick={()=>{
   const ids:string[]=Array.isArray(c.citations)?c.citations:[];
   const refs=ids.map((id:string)=>papers.find(p=>p.id===id)).filter(Boolean) as Paper[];
   let draft=typeof c.draft==='string'?c.draft:'';
   refs.forEach((p,i)=>{draft=draft.replaceAll(`[${p.id}]`,`[${i+1}]`)});
   const bibliography=refs.map((p,i)=>`${i+1}. ${p.authors?.join(', ')||'Unknown author'}${p.year?` (${p.year})`:''}. ${p.title}.${p.doi?` https://doi.org/${p.doi}`:p.url?` ${p.url}`:''}`).join('\\n');
   const blob=new Blob([`# Literature synthesis\\n\\n${draft}\\n\\n## References\\n\\n${bibliography}\\n`],{type:'text/markdown'});
   const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='literature-synthesis.md';link.click();URL.revokeObjectURL(url)
  }}><Download size={13}/> Export Markdown</button>
  {(typeof c.draft==='string'?c.draft:'Synthesis draft is empty.').split('\\n').filter(Boolean).map((line:string,i:number)=><p key={i}>{line}</p>)}
  <div className="citation-count">{Array.isArray(c.citations)?c.citations.length:0} linked source citations · {c.word_count||0} words</div>
 </div>;
}
function ProjectDialog({close,submit,busy,initial}:{close:()=>void;submit:(d:FormData)=>void;busy:boolean;initial:Project|null}){return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)close()}}><form className="modal" action={submit}><div className="modal-head"><div><span className="eyebrow">PROJECT SETUP</span><h2>{initial?'Project details':'Create a literature review'}</h2></div><button type="button" className="icon-button" onClick={close}><X size={18}/></button></div><label>Project name<input required minLength={2} name="title" defaultValue={initial?.title} placeholder="e.g. AI in clinical decision support"/></label><label>Research question<textarea required minLength={10} name="question" defaultValue={initial?.question} rows={3} placeholder="What do you want to understand from the literature?"/></label><label>Scope and inclusion notes <span className="label-muted">Optional</span><textarea name="scope" defaultValue={initial?.scope} rows={2} placeholder="Population, years, methods, or exclusions…"/></label><div className="modal-foot"><button type="button" className="secondary" onClick={close}>Cancel</button><button disabled={busy} className="primary" type="submit">{busy&&<LoaderCircle size={15} className="spin"/>}{initial?'Save project':'Create project'} <ArrowRight size={15}/></button></div></form></div>}


function ResearchDataPanel({run,project,papers}:{run:Run;project:Project;papers:Paper[]}){
 const [metrics,setMetrics]=useState<any>(null),[evaluations,setEvaluations]=useState<any[]>([]),[errors,setErrors]=useState<any[]>([]),[runs,setRuns]=useState<any[]>([]),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const [evaluator,setEvaluator]=useState('Researcher'),[rubric,setRubric]=useState('rubric-v1');
 const [scores,setScores]=useState<Record<string,string>>({
  extraction_accuracy:'',extraction_completeness:'',evidence_traceability:'',
  organization_quality:'',synthesis_quality:''
 });
 const [comments,setComments]=useState('');
 const [errorArtifact,setErrorArtifact]=useState(''),[errorCategory,setErrorCategory]=useState('unsupported_claim'),[severity,setSeverity]=useState('moderate'),[errorCorrection,setErrorCorrection]=useState(''),[errorComments,setErrorComments]=useState(''),[sourceEvidenceJson,setSourceEvidenceJson]=useState('[]'),[corrected,setCorrected]=useState(false),[propagated,setPropagated]=useState(false);
 const [pairs,setPairs]=useState([{a:'',b:''}]),[comparison,setComparison]=useState<any>(null);
 const available=run.artifacts||[];
 const refresh=async()=>{
  try{
   const [m,e,x,p]=await Promise.all([
    api<any>(`/runs/${run.id}/metrics`),api<any[]>(`/runs/${run.id}/evaluations`),
    api<any[]>(`/runs/${run.id}/errors`),api<any>(`/projects/${project.id}`)
   ]);
   setMetrics(m);setEvaluations(e);setErrors(x);setRuns(p.runs||[]);
   const next:Record<string,string>={extraction_accuracy:'',extraction_completeness:'',evidence_traceability:'',organization_quality:'',synthesis_quality:''};
   for(const row of e){
    const stageMap:Record<string,Record<string,string>>={
     extraction:{factual_accuracy:'extraction_accuracy',accuracy:'extraction_accuracy',semantic_accuracy:'extraction_accuracy',extraction_completeness:'extraction_completeness',completeness:'extraction_completeness',evidence_support:'evidence_traceability',evidence_traceability:'evidence_traceability',evidence_fidelity:'evidence_traceability',source_fidelity:'evidence_traceability'},
     organization:{organization_quality:'organization_quality',overall_quality:'organization_quality'},
     synthesis:{synthesis_quality:'synthesis_quality',overall_quality:'synthesis_quality'}
    };
    for(const [key,value] of Object.entries(row.scores||{})){
     const target=stageMap[row.stage]?.[key];
     if(target&&value!=null)next[target]=String(value);
    }
    if(row.comments)setComments(row.comments);
   }
   setScores(next);
  }catch(err:any){setMessage(err.message)}
 };
 useEffect(()=>{refresh()},[run.id]);
 useEffect(()=>{if(available.length&&!errorArtifact)setErrorArtifact(available[available.length-1].id)},[run.id,available.length]);
 const metricValue=(value:any,suffix='')=>value===null||value===undefined?'Not recorded':`${value}${suffix}`;
 const humanStats=metrics?.human?.measured_review_duration_ms;
 const totalReviewMinutes=humanStats&&humanStats.n?((humanStats.mean*humanStats.n)/60000).toFixed(2):run.settings?.condition==='without_intermediate_human_supervision'?'0.00':'Not recorded';
 const fieldPresence=metrics?.stage_observations?.extraction?.field_presence_rate;
 const corpusCount=(metrics?.source_paper_ids||[]).length;
 const modelNames=Object.values(run.settings?.model_ids||{}).filter(Boolean);
 const modelLabel=modelNames.length&&new Set(modelNames).size===1?String(modelNames[0]):Object.values(run.models||{}).join(', ');
 const setScore=(key:string,value:string)=>setScores(prev=>({...prev,[key]:value}));
 async function saveResult(e:React.FormEvent){
  e.preventDefault();setBusy(true);setMessage('');
  try{
   const numeric=(key:string)=>scores[key]?Number(scores[key]):null;
   const result=await api<any>(`/runs/${run.id}/result-record`,{method:'POST',body:JSON.stringify({
    evaluator_id:evaluator,rubric_version:rubric,
    extraction_accuracy:numeric('extraction_accuracy'),
    extraction_completeness:numeric('extraction_completeness'),
    evidence_traceability:numeric('evidence_traceability'),
    organization_quality:numeric('organization_quality'),
    synthesis_quality:numeric('synthesis_quality'),
    comments
   })});
   setMessage(`Result record saved. Updated: ${result.saved_stages.join(', ')}.`);
   await refresh();
  }catch(err:any){setMessage(err.message)}finally{setBusy(false)}
 }
 async function submitError(e:React.FormEvent){
  e.preventDefault();setBusy(true);setMessage('');
  try{
   const source_evidence=JSON.parse(sourceEvidenceJson);
   await api(`/runs/${run.id}/errors`,{method:'POST',body:JSON.stringify({
    artifact_id:errorArtifact,evaluator_id:evaluator,category:errorCategory,severity,
    source_evidence,human_correction:errorCorrection,corrected,propagated,comments:errorComments
   })});
   setMessage('Error annotation saved.');await refresh();
  }catch(err:any){setMessage(err instanceof SyntaxError?'Source evidence must be valid JSON.':err.message)}
  finally{setBusy(false)}
 }
 async function compare(e:React.FormEvent){
  e.preventDefault();setBusy(true);setMessage('');
  try{
   const selected=pairs.filter(pair=>pair.a&&pair.b).map(pair=>({no_intermediate_run_id:pair.a,supervised_run_id:pair.b}));
   if(!selected.length)throw new Error('Add at least one completed run pair.');
   const result=await api<any>(`/projects/${project.id}/compare`,{method:'POST',body:JSON.stringify({pairs:selected})});
   setComparison(result);
  }catch(err:any){setMessage(err.message)}finally{setBusy(false)}
 }
 const artifactLabel=(a:any)=>`${a.settings?.experiment_label||a.settings?.condition||'Run'} · ${a.settings?.replicate||'replicate'} · ${a.status} · ${a.id.slice(0,8)}`;
 const scoreControl=(key:string,title:string,description:string)=><label className="result-score-field"><span><b>{title}</b><small>{description}</small></span><select required value={scores[key]} onChange={e=>setScore(key,e.target.value)}><option value="">Select score</option>{[1,2,3,4,5].map(n=><option value={n} key={n}>{n} / 5</option>)}</select></label>;
 const completed=run.status==='completed';
 const scoredCount=Object.values(scores).filter(Boolean).length;
 return <div className="research-data">
  <section className="content-card result-record-card">
   <div className="section-head"><div><span className="eyebrow">RUN RESULT RECORD</span><h3>Review and save this experiment</h3><p>Run observations are collected automatically. Assess the five quality outcomes once, then download the result.</p></div><span className={completed?'artifact-status approved':'artifact-status pending_review'}>{completed?'Run completed':'Run not completed'}</span></div>
   <div className="result-facts">
    <div><small>Condition</small><b>{run.settings?.condition==='with_human_supervision_all_stages'?'B · Human-supervised':'A · Autonomous'}</b></div>
    <div><small>Model</small><b>{modelLabel||'Not recorded'}</b></div>
    <div><small>Frozen corpus</small><b>{metricValue(corpusCount,' papers')}</b></div>
    <div><small>Corpus fingerprint</small><b className="result-hash">{metrics?.source_snapshot_hash||'Not recorded'}</b></div>
    <div><small>Machine execution</small><b>{metricValue(metrics?.system?.total_agent_execution_duration_ms!=null?(metrics.system.total_agent_execution_duration_ms/1000).toFixed(1):null,' sec')}</b></div>
    <div><small>Human review effort</small><b>{totalReviewMinutes==='Not recorded'?totalReviewMinutes:`${totalReviewMinutes} min`}</b></div>
    <div><small>Extraction fields present</small><b>{fieldPresence==null?'Not recorded':`${(fieldPresence*100).toFixed(1)}% · presence only`}</b></div>
    <div><small>Tokens used</small><b>{metricValue(metrics?.system?.total_tokens)}</b></div>
   </div>
   <form className="result-editor" onSubmit={saveResult}>
    <div className="section-head"><div><h4>Independent quality ratings</h4><p>Inspect the artifacts and source papers. Use the same rubric for both conditions. Select 1–5; no score is pre-filled.</p></div><span className="pending-badge">{scoredCount}/5 assessed</span></div>
    <div className="result-score-grid">
     {scoreControl('extraction_accuracy','1. Extraction accuracy','Are the extracted facts faithful to the source papers?')}
     {scoreControl('extraction_completeness','2. Extraction completeness','Were the relevant objectives, methods, findings and limitations captured?')}
     {scoreControl('evidence_traceability','3. Evidence traceability','Can extracted claims be traced to appropriate source passages?')}
     {scoreControl('organization_quality','4. Organization quality','Are themes, comparisons, agreements and gaps meaningful?')}
     {scoreControl('synthesis_quality','5. Literature synthesis quality','Is the final review accurate, critical, coherent and properly attributed?')}
    </div>
    <div className="result-meta-fields">
     <label>Evaluator<input value={evaluator} onChange={e=>setEvaluator(e.target.value)} required/></label>
     <label>Rubric version<input value={rubric} onChange={e=>setRubric(e.target.value)} required/></label>
    </div>
    <label className="result-comments">Evaluator notes<textarea value={comments} onChange={e=>setComments(e.target.value)} placeholder="Briefly justify ratings and note important strengths or weaknesses…"/></label>
    <div className="result-actions"><button className="primary" disabled={!completed||busy||scoredCount!==5}>{busy?'Saving…':'Save / update result record'}</button><span>Saved ratings can be edited and saved again.</span></div>
   </form>
   <div className="result-downloads">
    {completed?<><a className="export-button" href={`/api/v1/runs/${run.id}/research-summary.csv`}>Download six-outcome CSV</a><a className="export-button" href={`/api/v1/runs/${run.id}/research-tables.zip`}>Download five research tables</a><a className="export-button" href={`/api/v1/runs/${run.id}/evidence-package`}>Full evidence package</a></>:<span className="label-muted">Downloads are available when the run is completed.</span>}
   </div>
  </section>
  <details className="content-card result-advanced"><summary>Advanced records · error annotations and paired comparisons</summary>
   <div className="advanced-result-section"><h4>Structured error analysis</h4><p className="label-muted">Record only errors you actually identify during evaluation.</p>
    <form onSubmit={submitError} className="data-form">
     <label>Artifact<select required value={errorArtifact} onChange={e=>setErrorArtifact(e.target.value)}><option value="">Choose artifact</option>{available.map(a=><option key={a.id} value={a.id}>{a.stage} · v{a.version} · {a.model}</option>)}</select></label>
     <label>Category<select value={errorCategory} onChange={e=>setErrorCategory(e.target.value)}>{['retrieval_error','missing_information','factual_extraction_error','evidence_attribution_error','citation_error','organization_error','incorrect_comparison','unsupported_claim','fabricated_information','synthesis_error'].map(x=><option key={x}>{x}</option>)}</select></label>
     <label>Severity<select value={severity} onChange={e=>setSeverity(e.target.value)}>{['minor','moderate','major','critical'].map(x=><option key={x}>{x}</option>)}</select></label>
     <label>Source evidence (JSON array)<textarea value={sourceEvidenceJson} onChange={e=>setSourceEvidenceJson(e.target.value)}/></label>
     <label>Human correction<textarea value={errorCorrection} onChange={e=>setErrorCorrection(e.target.value)}/></label>
     <label>Comments<textarea value={errorComments} onChange={e=>setErrorComments(e.target.value)}/></label>
     <label><input type="checkbox" checked={corrected} onChange={e=>setCorrected(e.target.checked)}/> Error corrected</label>
     <label><input type="checkbox" checked={propagated} onChange={e=>setPropagated(e.target.checked)}/> Error propagated downstream</label>
     <button className="primary" disabled={busy||!errorArtifact}>{busy?'Saving…':'Save error annotation'}</button>
    </form>
   </div>
   <div className="advanced-result-section"><h4>Paired A/B comparison</h4><p className="label-muted">Match completed runs with identical corpus and model settings.</p>
    <form onSubmit={compare} className="data-form">
     {pairs.map((pair,index)=><div className="experiment-fields" key={index}>
      <label>Condition A · autonomous<select value={pair.a} onChange={e=>setPairs(pairs.map((p,i)=>i===index?{...p,a:e.target.value}:p))}><option value="">Select run</option>{runs.filter((r:any)=>r.status==='completed'&&r.settings?.condition==='without_intermediate_human_supervision').map((r:any)=><option key={r.id} value={r.id}>{artifactLabel(r)}</option>)}</select></label>
      <label>Condition B · human-supervised<select value={pair.b} onChange={e=>setPairs(pairs.map((p,i)=>i===index?{...p,b:e.target.value}:p))}><option value="">Select run</option>{runs.filter((r:any)=>r.status==='completed'&&r.settings?.condition==='with_human_supervision_all_stages').map((r:any)=><option key={r.id} value={r.id}>{artifactLabel(r)}</option>)}</select></label>
     </div>)}
     <div><button type="button" className="secondary" onClick={()=>setPairs([...pairs,{a:'',b:''}])}>Add replicate pair</button> <button className="primary" disabled={busy}>{busy?'Comparing…':'Compare pairs'}</button></div>
    </form>
    {comparison&&<pre className="data-output">{JSON.stringify(comparison,null,2)}</pre>}
   </div>
   <div className="advanced-result-section"><h4>Saved data</h4><button className="secondary" onClick={refresh}>Refresh records</button><pre className="data-output">{JSON.stringify({evaluations,errors,metrics},null,2)}</pre></div>
  </details>
  {message&&<div className="content-card" role="status">{message}</div>}
 </div>
}
function ReferenceAnnotationPanel({project,papers}:{project:Project;papers:Paper[]}){
 const [paperId,setPaperId]=useState(''),[annotator,setAnnotator]=useState('Researcher'),[version,setVersion]=useState('reference-v1'),[annotations,setAnnotations]=useState('{\n  "research_objective": "Not reported",\n  "research_problem": "Not reported",\n  "research_question": "Not reported",\n  "methodology": "Not reported",\n  "research_design": "Not reported",\n  "dataset_or_sample": "Not reported",\n  "data_collection_methods": "Not reported",\n  "analysis_methods": "Not reported",\n  "findings": "Not reported",\n  "limitations": "Not reported",\n  "research_gaps": "Not reported",\n  "contributions": "Not reported",\n  "supporting_evidence": [],\n  "page_or_section_references": []\n}'),[comments,setComments]=useState(''),[rows,setRows]=useState<any[]>([]),[message,setMessage]=useState('');
 const refresh=async()=>{try{setRows(await api<any[]>(`/projects/${project.id}/reference-annotations`))}catch(err:any){setMessage(err.message)}};useEffect(()=>{refresh()},[project.id]);
 async function save(e:React.FormEvent){e.preventDefault();setMessage('');try{const fields=JSON.parse(annotations);await api(`/projects/${project.id}/reference-annotations`,{method:'POST',body:JSON.stringify({paper_id:paperId,annotator_id:annotator,rubric_version:version,annotations:fields,comments})});setMessage('Reference annotation saved.');await refresh()}catch(err:any){setMessage(err instanceof SyntaxError?'Reference annotation must be valid JSON.':err.message)}}
 return <div className="content-card"><div className="section-head"><div><h3>Independent reference annotations</h3><p>Manually annotate selected source papers independently of agent outputs. Use “Not reported” when the source lacks a field.</p></div></div><form onSubmit={save} className="data-form"><label>Paper<select required value={paperId} onChange={e=>setPaperId(e.target.value)}><option value="">Select paper</option>{papers.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label><label>Annotator ID<input required value={annotator} onChange={e=>setAnnotator(e.target.value)}/></label><label>Reference rubric version<input required value={version} onChange={e=>setVersion(e.target.value)}/></label><label>Annotation fields (JSON)<textarea value={annotations} onChange={e=>setAnnotations(e.target.value)} rows={12}/></label><label>Comments<textarea value={comments} onChange={e=>setComments(e.target.value)}/></label><button className="primary" disabled={!paperId}>Save reference annotation</button></form>{message&&<p>{message}</p>}<h4>Saved annotations ({rows.length})</h4><pre className="data-output">{JSON.stringify(rows,null,2)}</pre></div>
}
