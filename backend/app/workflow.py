import uuid, logging, re
import hashlib, json
from datetime import datetime, timezone
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import Project, Run, Paper, Artifact, Event
from .agents import STAGES, json_answer, complete
from .config import settings

log=logging.getLogger("synthesis.workflow")

class WorkflowError(Exception): pass

def record(db, project_id, run_id, kind, message, details=None):
    db.add(Event(id=str(uuid.uuid4()),project_id=project_id,run_id=run_id,type=kind,message=message,details=details or {}))

def needs_review(policy, stage):
    return {"every_stage":STAGES,"search_and_final":["search","synthesis"],"final_only":["synthesis"],"after_extraction":["extraction"]}[policy].__contains__(stage)

def stage_provider(run, stage):
    return run.models.get(stage) or {"search":"openai","extraction":"qwen","organization":"deepseek","synthesis":"openai"}[stage]

def search_sources(project, run, db):
    question=project.question + ("\nScope: "+project.scope if project.scope else "") + ("\nReviewer feedback: "+run.settings.get("review_note","") if run.settings.get("review_note") else "")
    params={"search":question,"per-page":run.settings.get("max_papers",25),"sort":"relevance_score:desc","select":"id,doi,title,publication_year,authorships,primary_location,cited_by_count,abstract_inverted_index,locations"}
    if settings.openalex_email: params["mailto"]=settings.openalex_email
    with httpx.Client(timeout=httpx.Timeout(35,connect=8),follow_redirects=True) as client:
        r=client.get("https://api.openalex.org/works",params=params); r.raise_for_status(); rows=r.json().get("results",[])
    papers=[]
    for row in rows:
        title=row.get("title") or "Untitled source"
        doi=(row.get("doi") or "").replace("https://doi.org/","") or None
        if doi:
            old=db.scalar(select(Paper).where(Paper.project_id==project.id,Paper.doi==doi))
            if old: papers.append(old); continue
        inverted=row.get("abstract_inverted_index") or {}
        words=[""]* (1+max((max(pos) for pos in inverted.values() if pos),default=-1))
        for word,positions in inverted.items():
            for pos in positions:
                if pos<len(words): words[pos]=word
        authors=[a.get("author",{}).get("display_name","") for a in row.get("authorships",[])[:8]]
        loc=row.get("primary_location") or {}
        src=Paper(id=str(uuid.uuid4()),project_id=project.id,run_id=run.id,title=title,authors=authors,year=row.get("publication_year"),doi=doi,url=(loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else row.get("id",""))),abstract=" ".join(words),venue=((loc.get("source") or {}).get("display_name") or ""),citation_count=row.get("cited_by_count") or 0)
        db.add(src); papers.append(src)
    db.flush()
    return papers

def source_dict(p): return {"id":p.id,"title":p.title,"authors":p.authors,"year":p.year,"doi":p.doi,"url":p.url,"abstract":p.abstract,"full_text":p.full_text,"venue":p.venue,"citation_count":p.citation_count}

def artifact_payload(db, run, stage):
    a=db.scalar(select(Artifact).where(Artifact.run_id==run.id,Artifact.stage==stage).order_by(Artifact.version.desc()))
    return {k:v for k,v in a.content.items() if k!="_telemetry"} if a else {}

def execute_stage(db: Session, project: Project, run: Run, stage: str):
    provider=stage_provider(run,stage)
    usage={}
    selected_ids=run.settings.get("source_paper_ids", [])
    sources=db.scalars(select(Paper).where(Paper.project_id==project.id,Paper.id.in_(selected_ids)).order_by(Paper.created_at.desc())).all() if selected_ids else []
    if stage=="search":
        if run.settings.get("source_mode", "openalex")=="saved_corpus":
            papers=db.scalars(select(Paper).where(Paper.project_id==project.id).order_by(Paper.created_at.desc()).limit(run.settings.get("max_papers",25))).all()
            model="Saved corpus"
        else:
            papers=search_sources(project,run,db)
            model="OpenAlex search"
        if not papers:
            raise WorkflowError("No papers were selected. Add papers to the saved corpus or broaden the OpenAlex search.")
        snapshot=[{"paper_id":p.id,"doi":p.doi,"title":p.title,"year":p.year,"text_sha256":hashlib.sha256((p.full_text or p.abstract or "").encode("utf-8")).hexdigest()} for p in papers]
        snapshot_hash=hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()
        run.settings={**run.settings,"source_paper_ids":[p.id for p in papers],"source_snapshot_hash":snapshot_hash}
        content={"query":project.question,"source_mode":run.settings.get("source_mode","openalex"),"count":len(papers),"papers":[{k:v for k,v in source_dict(x).items() if k!="full_text"} for x in papers],"provider":model,"source_snapshot":snapshot,"source_snapshot_hash":snapshot_hash}
    elif stage=="extraction":
        if not sources: raise WorkflowError("No saved papers. Run Search first.")
        compact=[{k:v for k,v in source_dict(p).items() if k in ("id","title","authors","year","doi","abstract","full_text")} for p in sources[:run.settings.get("max_papers",25)]]
        compact=[{**p,"abstract":p.get("abstract","")[:12000],"full_text":p.get("full_text","")[:12000]} for p in compact]
        content,model,usage=json_answer(provider,"Research question: "+project.question+"\nSources: "+str(compact)+("\nReviewer feedback: "+run.settings.get("review_note","") if run.settings.get("review_note") else ""),"Extract evidence-grounded claims. Do not infer beyond supplied paper text or abstracts. Return JSON: {claims:[{claim, paper_id, quote, method, limitation, confidence}]} . Every claim must cite an input paper_id and a verbatim supporting quote from the supplied paper text or abstract.",temperature=run.settings.get("temperature",0.0))
        by_id={p.id:p for p in sources}
        accepted=[]
        for claim in content.get("claims",[]):
            paper_id=claim.get("paper_id") if isinstance(claim,dict) else None
            quote=claim.get("quote","") if isinstance(claim,dict) else ""
            if not isinstance(paper_id,str) or paper_id not in by_id or not isinstance(quote,str) or len(quote.strip())<20:
                continue
            paper=by_id[paper_id]
            source_text=" ".join((paper.full_text or "") .casefold().split())
            abstract_text=" ".join((paper.abstract or "").casefold().split())
            normalized_quote=" ".join(quote.casefold().split())
            if normalized_quote in source_text or normalized_quote in abstract_text:
                accepted.append(claim)
        content["rejected_claim_count"]=max(0,len(content.get("claims",[]))-len(accepted))
        content["claims"]=accepted
    elif stage=="organization":
        extraction=artifact_payload(db,run,"extraction")
        if not extraction: raise WorkflowError("Approve or create an extraction artifact first.")
        content,model,usage=json_answer(provider,"Question: "+project.question+"\nEvidence: "+str(extraction)+("\nReviewer feedback: "+run.settings.get("review_note","") if run.settings.get("review_note") else ""),"Group only provided claims into themes. Preserve claim IDs/paper_id, note agreements, disagreements, methods and evidence gaps. Return JSON: {themes:[{name,summary,claim_indices,agreements,disagreements,gaps}]}.",temperature=run.settings.get("temperature",0.0))
    elif stage=="synthesis":
        org=artifact_payload(db,run,"organization"); extraction=artifact_payload(db,run,"extraction")
        if not org or not extraction: raise WorkflowError("Extraction and organization artifacts are required.")
        content,model,usage=complete(provider,"Question: "+project.question+"\nScope: "+project.scope+"\nThemes: "+str(org)+"\nEvidence claims: "+str(extraction)+"\nWrite a balanced literature synthesis. Use inline citations [paper_id]. Only make claims supported by supplied evidence. Include an evidence limitations section."+("\nReviewer feedback: "+run.settings.get("review_note","") if run.settings.get("review_note") else ""),"You write rigorous scientific literature syntheses. Never invent sources, facts, or citations. Keep citations attached to the claims they support.",temperature=run.settings.get("temperature",0.0))
        content={"draft":content,"citations":list(dict.fromkeys(re.findall(r"\[([0-9a-f-]{36})\]",content))),"word_count":len(content.split())}
        valid={p.id for p in sources}; content["citations"]=[x for x in content["citations"] if x in valid]
    else: raise WorkflowError("Unknown workflow stage.")
    latest=db.scalar(select(Artifact).where(Artifact.run_id==run.id,Artifact.stage==stage).order_by(Artifact.version.desc()))
    version=(latest.version+1) if latest else 1
    art=Artifact(id=str(uuid.uuid4()),run_id=run.id,stage=stage,version=version,content=content,status="pending_review" if needs_review(project.review_policy,stage) else "approved",model=model)
    if stage in ("extraction", "organization", "synthesis"):
        content={**content,"_telemetry":{"usage":usage.get("usage",{}),"prompt_sha256":usage.get("prompt_sha256"),"provider":provider,"model":model,"prompt_version":"1.0","temperature":run.settings.get("temperature",0.0)}}
        art.content=content
    db.add(art); run.current_stage=stage
    if art.status=="pending_review": run.status="awaiting_review"
    else: run.status="running"
    record(db,project.id,run.id,"artifact_created",f"{stage.title()} artifact v{version} created",{"artifact_id":art.id,"model":model,"review_required":art.status=="pending_review"})
    db.commit(); db.refresh(art)
    return art

def advance(db, project, run, from_stage=None):
    start=(STAGES.index(from_stage)+1) if from_stage in STAGES else 0
    for stage in STAGES[start:]:
        try: art=execute_stage(db,project,run,stage)
        except Exception as exc:
            run.status="failed"; record(db,project.id,run.id,"stage_failed",f"{stage.title()} failed: {str(exc)[:500]}",{"stage":stage}); db.commit(); raise
        if art.status=="pending_review": return art
    run.status="completed"; run.current_stage="synthesis"; record(db,project.id,run.id,"run_completed","Synthesis workflow completed"); db.commit(); return None
