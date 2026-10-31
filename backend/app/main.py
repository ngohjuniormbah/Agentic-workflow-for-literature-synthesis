import uuid, logging, io, os, json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from .db import Base, engine, get_db
from .models import Project, Run, Paper, Artifact, Event
from .schemas import ProjectCreate, ProjectUpdate, RunCreate, ReviewAction
from .workflow import advance, execute_stage, record, WorkflowError
from .agents import AI_STAGES, STAGES, complete, provider_catalog, provider_config, ProviderError
from .config import settings

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
app=FastAPI(title=settings.app_name,version="1.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],allow_credentials=True,allow_methods=["GET","POST","PATCH","OPTIONS"],allow_headers=["Authorization","Content-Type"])

def proj_or_404(db,id):
    p=db.get(Project,id)
    if not p: raise HTTPException(404,"Project not found")
    return p

def run_or_404(db,id):
    r=db.get(Run,id)
    if not r: raise HTTPException(404,"Run not found")
    return r

def artifact_view(a): return {"id":a.id,"run_id":a.run_id,"stage":a.stage,"version":a.version,"content":a.content,"status":a.status,"model":a.model,"reviewer_note":a.reviewer_note,"created_at":a.created_at}
def paper_view(p): return {"id":p.id,"title":p.title,"authors":p.authors,"year":p.year,"doi":p.doi,"url":p.url,"abstract":p.abstract,"has_full_text":bool(p.full_text),"venue":p.venue,"citation_count":p.citation_count}
def project_view(p): return {"id":p.id,"title":p.title,"question":p.question,"scope":p.scope,"review_policy":p.review_policy,"created_at":p.created_at,"run_count":len(p.runs)}
def run_view(r): return {"id":r.id,"project_id":r.project_id,"status":r.status,"current_stage":r.current_stage,"models":r.models,"settings":r.settings,"created_at":r.created_at,"updated_at":r.updated_at}

@app.get("/health")
def health(db:Session=Depends(get_db)):
    try:
        db.execute(select(1))
    except Exception as exc:
        raise HTTPException(503,"Database health check failed") from exc
    providers=provider_catalog()
    return {"status":"ok","environment":settings.app_env,"dependencies":{"database":"ok"},"configured_models":[n for n,p in providers.items() if p.configured]}
@app.get("/api/v1/models")
def models():
    return {"providers":[{"id":p.id,"gateway":p.gateway,"configured":p.configured,"model":p.model_id} for p in provider_catalog().values()]}
@app.post("/api/v1/models/{provider_id}/check")
def check_model(provider_id:str):
    try:
        text,model,_usage=complete(provider_id,"Reply with exactly: OK","You are checking that the model connection works. Reply with only OK.",max_tokens=16)
    except ProviderError as exc:
        raise HTTPException(exc.status_code,str(exc)) from exc
    return {"provider":provider_id,"model":model,"ok":True,"response":text[:100]}
@app.get("/api/v1/projects")
def projects(db:Session=Depends(get_db)): return [project_view(p) for p in db.scalars(select(Project).order_by(desc(Project.created_at))).all()]
@app.post("/api/v1/projects",status_code=201)
def create_project(body:ProjectCreate,db:Session=Depends(get_db)):
    p=Project(id=str(uuid.uuid4()),title=body.title,question=body.question,scope=body.scope,review_policy=body.review_policy); db.add(p); db.commit(); db.refresh(p); return project_view(p)
@app.patch("/api/v1/projects/{project_id}")
def update_project(project_id:str,body:ProjectUpdate,db:Session=Depends(get_db)):
    p=proj_or_404(db,project_id)
    for key,value in body.model_dump().items(): setattr(p,key,value)
    db.commit(); db.refresh(p); return project_view(p)
@app.get("/api/v1/projects/{project_id}")
def get_project(project_id:str,db:Session=Depends(get_db)):
    p=proj_or_404(db,project_id); return {**project_view(p),"runs":[run_view(r) for r in db.scalars(select(Run).where(Run.project_id==p.id).order_by(desc(Run.created_at))).all()]}
@app.get("/api/v1/projects/{project_id}/papers")
def papers(project_id:str,db:Session=Depends(get_db)):
    proj_or_404(db,project_id); return [paper_view(p) for p in db.scalars(select(Paper).where(Paper.project_id==project_id).order_by(desc(Paper.created_at))).all()]
@app.get("/api/v1/projects/{project_id}/runs/export")
def export_runs(project_id:str,db:Session=Depends(get_db)):
    project=proj_or_404(db,project_id)
    runs=db.scalars(select(Run).where(Run.project_id==project.id).order_by(Run.created_at)).all()
    rows=[]
    for run in runs:
        artifacts=db.scalars(select(Artifact).where(Artifact.run_id==run.id).order_by(Artifact.created_at)).all()
        run_events=db.scalars(select(Event).where(Event.run_id==run.id).order_by(Event.created_at)).all()
        rows.append({"project":{"id":project.id,"title":project.title,"question":project.question,"scope":project.scope,"review_policy":project.review_policy},"run":run_view(run),"artifacts":[artifact_view(a) for a in artifacts],"events":[{"id":e.id,"type":e.type,"message":e.message,"details":e.details,"created_at":e.created_at} for e in run_events]})
    body="\n".join(json.dumps(row,ensure_ascii=False,default=str) for row in rows)+("\n" if rows else "")
    return Response(body,media_type="application/x-ndjson",headers={"Content-Disposition":f'attachment; filename="{project.id}-experiment-runs.jsonl"'})
@app.post("/api/v1/projects/{project_id}/papers/upload",status_code=201)
async def upload_paper(project_id:str,file:UploadFile=File(...),db:Session=Depends(get_db)):
    project=proj_or_404(db,project_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(415,"Upload a PDF file.")
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024: raise HTTPException(413,"PDF exceeds the 25 MB upload limit.")
    if not data.startswith(b"%PDF-"): raise HTTPException(415,"The uploaded file is not a valid PDF.")
    try:
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data),strict=False)
        if reader.is_encrypted: raise HTTPException(422,"Encrypted PDFs are not supported.")
        if len(reader.pages)>500: raise HTTPException(413,"PDF exceeds the 500 page limit.")
        text="\n\n".join((page.extract_text() or "") for page in reader.pages)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(422,"Could not read text from this PDF.") from exc
    if not text.strip(): raise HTTPException(422,"No selectable text found. OCR scanned PDFs before upload.")
    title=os.path.splitext(os.path.basename(file.filename))[0][:500]
    paper=Paper(id=str(uuid.uuid4()),project_id=project.id,run_id=None,title=title,authors=[],year=None,doi=None,url="",abstract="",full_text=text[:2_000_000],venue="Uploaded PDF",citation_count=0)
    db.add(paper); record(db,project.id,"library","paper_uploaded",f"Uploaded paper: {title}",{"paper_id":paper.id,"characters":len(text)})
    db.commit(); db.refresh(paper); return paper_view(paper)
@app.get("/api/v1/projects/{project_id}/events")
def events(project_id:str,db:Session=Depends(get_db)):
    proj_or_404(db,project_id); return [{"id":e.id,"run_id":e.run_id,"type":e.type,"message":e.message,"details":e.details,"created_at":e.created_at} for e in db.scalars(select(Event).where(Event.project_id==project_id).order_by(desc(Event.created_at)).limit(100)).all()]
@app.post("/api/v1/projects/{project_id}/runs",status_code=201)
def create_run(project_id:str,body:RunCreate,db:Session=Depends(get_db)):
    p=proj_or_404(db,project_id)
    if body.source_mode=="saved_corpus" and not db.scalar(select(Paper.id).where(Paper.project_id==p.id).limit(1)):
        raise HTTPException(422,"Upload at least one paper before starting a saved-corpus run.")
    missing=sorted(set(AI_STAGES)-set(body.models))
    if missing: raise HTTPException(422,f"Choose a model for every AI stage: {', '.join(missing)}")
    for stage,provider in body.models.items():
        if stage not in AI_STAGES or provider not in provider_catalog(): raise HTTPException(422,f"Invalid model assignment for {stage}")
        try:
            provider_config(provider)
        except ProviderError as exc:
            raise HTTPException(exc.status_code,str(exc)) from exc
    r=Run(id=str(uuid.uuid4()),project_id=p.id,models=body.models,settings={"max_papers":body.max_papers,"source_mode":body.source_mode,"experiment_label":body.experiment_label,"condition":body.condition,"replicate":body.replicate,"random_seed":body.random_seed,"temperature":body.temperature,"protocol_version":"1.0","review_policy":p.review_policy},status="queued"); db.add(r); db.flush(); record(db,p.id,r.id,"run_queued","Workflow run queued",{"review_policy":p.review_policy,"models":body.models,"experiment_label":body.experiment_label,"condition":body.condition,"replicate":body.replicate,"source_mode":body.source_mode}); db.commit(); db.refresh(r)
    return {**run_view(r),"artifacts":[]}
@app.get("/api/v1/runs/{run_id}")
def get_run(run_id:str,db:Session=Depends(get_db)):
    r=run_or_404(db,run_id); artifacts=db.scalars(select(Artifact).where(Artifact.run_id==r.id).order_by(Artifact.created_at)).all()
    return {**run_view(r),"artifacts":[artifact_view(a) for a in artifacts],"papers":[paper_view(p) for p in db.scalars(select(Paper).where(Paper.project_id==r.project_id).order_by(desc(Paper.created_at))).all()]}
@app.post("/api/v1/runs/{run_id}/artifacts/{artifact_id}/review")
def review(run_id:str,artifact_id:str,body:ReviewAction,db:Session=Depends(get_db)):
    r=run_or_404(db,run_id); p=proj_or_404(db,r.project_id); a=db.get(Artifact,artifact_id)
    if not a or a.run_id!=r.id: raise HTTPException(404,"Artifact not found")
    if a.status!="pending_review": raise HTTPException(409,"Artifact is no longer awaiting review")
    if r.status!="awaiting_review": raise HTTPException(409,"Run is not currently waiting for review")
    a.reviewer_note=body.note
    if body.action=="approve":
        a.status="approved"; r.settings={**r.settings,"resume_from_stage":a.stage}; record(db,p.id,r.id,"review_approved",f"{a.stage.title()} v{a.version} approved",{"artifact_id":a.id,"note":body.note})
    else:
        a.status="revision_requested"; stage=a.stage
        if body.action=="return": stage=STAGES[max(0,STAGES.index(a.stage)-1)]
        resume_from=STAGES[STAGES.index(stage)-1] if STAGES.index(stage)>0 else None
        r.settings={**r.settings,"review_note":body.note,"resume_from_stage":resume_from}
        record(db,p.id,r.id,"review_revision_requested",f"Revision requested from {stage.title()}",{"artifact_id":a.id,"action":body.action,"note":body.note,"retry_stage":stage})
    r.status="queued"; record(db,p.id,r.id,"run_queued","Run queued after reviewer decision",{"resume_from_stage":r.settings.get("resume_from_stage")}); db.commit()
    db.refresh(r)
    return {**run_view(r),"artifacts":[artifact_view(x) for x in db.scalars(select(Artifact).where(Artifact.run_id==r.id).order_by(Artifact.created_at)).all()]}
@app.get("/")
def root(): return {"service":"Synthesis API","docs":"/docs","health":"/health"}
