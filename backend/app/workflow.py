import hashlib
import json
import logging
import re
import time
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents import AI_STAGES, STAGES, complete, json_answer
from .config import settings
from .models import Artifact, Event, Paper, Project, Run

log = logging.getLogger("synthesis.workflow")


class WorkflowError(Exception):
    pass


def record(db, project_id, run_id, kind, message, details=None):
    db.add(
        Event(
            id=str(uuid.uuid4()),
            project_id=project_id,
            run_id=run_id,
            type=kind,
            message=message,
            details=details or {},
        )
    )


def needs_review(policy, stage):
    return stage in {
        "every_stage": STAGES,
        "search_and_final": ["search", "synthesis"],
        "final_only": ["synthesis"],
        "after_extraction": ["extraction"],
        "none": [],
    }.get(policy, [])


EXPERIMENT_CONDITION_POLICIES = {
    "search_discovery": "every_stage",
    "without_intermediate_human_supervision": "none",
    "with_human_supervision_all_stages": "every_stage",
    # Legacy policies remain loadable for existing project history.
    "full_audit": "every_stage",
    # Target source screening and the final deliverable; retries are reviewer initiated.
    "targeted_retry": "search_and_final",
    "endpoint_oversight": "final_only",
    "midpoint_gating": "after_extraction",
}


def stage_provider(run, stage):
    return (
        run.models.get(stage)
        or {
            "search": "openai",
            "extraction": "qwen",
            "organization": "deepseek",
            "synthesis": "openai",
        }[stage]
    )


def search_sources(project, run, db):
    question = (
        project.question
        + ("\nScope: " + project.scope if project.scope else "")
        + (
            "\nReviewer feedback: " + run.settings.get("review_note", "")
            if run.settings.get("review_note")
            else ""
        )
    )
    params = {
        "search": question,
        "per-page": run.settings.get("max_papers", 25),
        "sort": "relevance_score:desc",
        "select": "id,doi,title,publication_year,authorships,primary_location,cited_by_count,abstract_inverted_index,locations",
    }
    if settings.openalex_email:
        params["mailto"] = settings.openalex_email
    with httpx.Client(
        timeout=httpx.Timeout(35, connect=8), follow_redirects=True
    ) as client:
        r = client.get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
        rows = r.json().get("results", [])
    papers = []
    seen_doi = set()
    seen_ids = set()
    duplicate_count = 0
    for row in rows:
        title = row.get("title") or "[Title unavailable]"
        doi = (row.get("doi") or "").replace("https://doi.org/", "") or None
        external_id = row.get("id") or ""
        dedupe_key = doi.casefold() if doi else external_id
        if dedupe_key and dedupe_key in seen_doi:
            duplicate_count += 1
            continue
        if dedupe_key:
            seen_doi.add(dedupe_key)
        if doi:
            old = db.scalar(
                select(Paper).where(Paper.project_id == project.id, Paper.doi == doi)
            )
            if old:
                if old.id not in seen_ids:
                    papers.append(old)
                    seen_ids.add(old.id)
                else:
                    duplicate_count += 1
                continue
        inverted = row.get("abstract_inverted_index") or {}
        words = [""] * (
            1 + max((max(pos) for pos in inverted.values() if pos), default=-1)
        )
        for word, positions in inverted.items():
            for pos in positions:
                if pos < len(words):
                    words[pos] = word
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in row.get("authorships", [])[:8]
        ]
        loc = row.get("primary_location") or {}
        src = Paper(
            id=str(uuid.uuid4()),
            project_id=project.id,
            run_id=run.id,
            title=title,
            authors=authors,
            year=row.get("publication_year"),
            doi=doi,
            url=(
                loc.get("landing_page_url")
                or (f"https://doi.org/{doi}" if doi else row.get("id", ""))
            ),
            abstract=" ".join(words),
            venue=((loc.get("source") or {}).get("display_name") or ""),
            citation_count=row.get("cited_by_count") or 0,
            in_corpus=False,
        )
        db.add(src)
        papers.append(src)
        seen_ids.add(src.id)
    run.settings = {
        **run.settings,
        "search_duplicate_count": duplicate_count,
        "search_query": question,
        "search_endpoint": "https://api.openalex.org/works",
        "search_parameters": params,
        "search_retrieved_count": len(rows),
    }
    db.flush()
    return papers


def source_dict(p):
    return {
        "id": p.id,
        "title": p.title,
        "authors": p.authors,
        "year": p.year,
        "doi": p.doi,
        "url": p.url,
        "abstract": p.abstract,
        "full_text": p.full_text,
        "venue": p.venue,
        "citation_count": p.citation_count,
    }


def artifact_payload(db, run, stage):
    a = db.scalar(
        select(Artifact)
        .where(Artifact.run_id == run.id, Artifact.stage == stage)
        .order_by(Artifact.version.desc())
    )
    return {k: v for k, v in a.content.items() if k != "_telemetry"} if a else {}


def sanitize_citations(draft, valid_paper_ids):
    valid = set(valid_paper_ids)
    cited = list(dict.fromkeys(re.findall(r"\[([0-9a-f-]{36})\]", draft)))
    accepted = [paper_id for paper_id in cited if paper_id in valid]
    rejected = [paper_id for paper_id in cited if paper_id not in valid]
    sanitized = re.sub(
        r"\[([0-9a-f-]{36})\]",
        lambda match: (
            match.group(0) if match.group(1) in valid else "[UNVERIFIED CITATION]"
        ),
        draft,
    )
    return sanitized, accepted, rejected


def validate_organization(content, claim_count):
    """Keep only theme/comparison references that point to supplied evidence claims."""
    if not isinstance(content.get("themes"), list):
        raise WorkflowError(
            "Organization response must contain a themes array. Retry this stage."
        )
    dropped_themes = 0
    dropped_comparisons = 0

    def valid_indices(values):
        if not isinstance(values, list):
            return []
        return list(
            dict.fromkeys(
                value
                for value in values
                if type(value) is int and 0 <= value < claim_count
            )
        )

    normalized = []
    for theme in content["themes"]:
        if not isinstance(theme, dict) or not isinstance(theme.get("name"), str):
            dropped_themes += 1
            continue
        theme_indices = valid_indices(theme.get("claim_indices"))
        if not theme_indices:
            dropped_themes += 1
            continue
        item = {
            "name": theme["name"][:500],
            "summary": str(theme.get("summary", ""))[:5000],
            "claim_indices": theme_indices,
        }
        for field, minimum_refs in (
            ("agreements", 2),
            ("disagreements", 2),
            ("methodological_comparisons", 2),
            ("gaps", 1),
        ):
            entries = theme.get(field, [])
            accepted = []
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(
                        entry.get("text"), str
                    ):
                        dropped_comparisons += 1
                        continue
                    refs = valid_indices(entry.get("claim_indices"))
                    if len(refs) < minimum_refs:
                        dropped_comparisons += 1
                        continue
                    accepted.append(
                        {"text": entry["text"][:3000], "claim_indices": refs}
                    )
            item[field] = accepted
        normalized.append(item)
    return {
        "themes": normalized,
        "dropped_unreferenced_themes": dropped_themes,
        "dropped_unreferenced_comparisons": dropped_comparisons,
    }


def organization_claim_payload(extraction):
    """Return only the evidence claims needed by organization, preserving their indices.

    The extraction artifact also contains per-paper fields and telemetry. Sending
    that duplicated material to the organization model wastes context and can cause
    long requests. The claim list retains each claim's paper and source provenance.
    """
    claims = extraction.get("claims")
    if not isinstance(claims, list):
        raise WorkflowError("Extraction artifact does not contain a claims array.")
    compact = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise WorkflowError(
                f"Extraction claim at index {index} is not a structured object."
            )
        compact.append(
            {
                "paper_id": claim.get("paper_id"),
                "field": claim.get("field"),
                "claim": claim.get("claim"),
                "quote": claim.get("quote"),
                "page_or_section": claim.get("page_or_section"),
                "source_basis": claim.get("source_basis"),
            }
        )
    return compact


def execute_stage(db: Session, project: Project, run: Run, stage: str):
    stage_started = time.perf_counter()
    provider = stage_provider(run, stage)
    usage = {}
    selected_ids = run.settings.get("source_paper_ids", [])
    sources = (
        db.scalars(
            select(Paper)
            .where(Paper.project_id == project.id, Paper.id.in_(selected_ids))
            .order_by(Paper.created_at.desc())
        ).all()
        if selected_ids
        else []
    )
    if stage == "search":
        if run.settings.get("source_mode", "openalex") == "saved_corpus":
            papers = db.scalars(
                select(Paper)
                .where(Paper.project_id == project.id, Paper.in_corpus.is_(True))
                .order_by(Paper.created_at.desc())
                .limit(run.settings.get("max_papers", 25))
            ).all()
            retrieval_source = "Saved corpus"
        else:
            papers = search_sources(project, run, db)
            retrieval_source = "OpenAlex"
        if not papers:
            raise WorkflowError(
                "No papers were selected. Add papers to the saved corpus or broaden the OpenAlex search."
            )
        ranking_input = [
            {
                "paper_id": p.id,
                "title": p.title,
                "year": p.year,
                "abstract": (p.abstract or "")[:1600],
                "doi": p.doi,
            }
            for p in papers
        ]
        ranking, model, usage = json_answer(
            provider,
            "Research question: "
            + project.question
            + "\nScope: "
            + project.scope
            + "\nCandidate papers (rank these; do not discard any): "
            + json.dumps(ranking_input, ensure_ascii=False),
            "You are the Search agent in a literature review. Rank every supplied candidate by relevance to the research question using only its title, year, and abstract. Return JSON with ranked_paper_ids (all supplied paper_id values, each exactly once, in relevance order) and relevance_notes (objects with paper_id and a concise reason). Do not invent IDs, facts, or citations. Ranking is advisory; a human will screen the candidate papers.",
            temperature=run.settings.get("temperature", 0.0),
        )
        by_id = {p.id: p for p in papers}
        ranked_ids = []
        for paper_id in (
            ranking.get("ranked_paper_ids", [])
            if isinstance(ranking.get("ranked_paper_ids"), list)
            else []
        ):
            if (
                isinstance(paper_id, str)
                and paper_id in by_id
                and paper_id not in ranked_ids
            ):
                ranked_ids.append(paper_id)
        ranked_ids.extend(p.id for p in papers if p.id not in ranked_ids)
        notes = {}
        for item in (
            ranking.get("relevance_notes", [])
            if isinstance(ranking.get("relevance_notes"), list)
            else []
        ):
            if (
                isinstance(item, dict)
                and item.get("paper_id") in by_id
                and isinstance(item.get("reason"), str)
            ):
                notes[item["paper_id"]] = item["reason"][:1000]
        papers = [by_id[paper_id] for paper_id in ranked_ids]
        snapshot = [
            {
                "paper_id": p.id,
                "rank": index + 1,
                "doi": p.doi,
                "title": p.title,
                "year": p.year,
                "text_sha256": hashlib.sha256(
                    (p.full_text or p.abstract or "").encode("utf-8")
                ).hexdigest(),
            }
            for index, p in enumerate(papers)
        ]
        canonical_snapshot = sorted(
            (
                {key: value for key, value in item.items() if key != "rank"}
                for item in snapshot
            ),
            key=lambda item: item["paper_id"],
        )
        snapshot_hash = hashlib.sha256(
            json.dumps(
                canonical_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        run.settings = {
            **run.settings,
            "source_paper_ids": [p.id for p in papers],
            "source_snapshot_hash": snapshot_hash,
        }
        content = {
            "query": project.question,
            "source_mode": run.settings.get("source_mode", "openalex"),
            "retrieval_source": retrieval_source,
            "search_provenance": {
                "query": run.settings.get("search_query", project.question),
                "endpoint": run.settings.get("search_endpoint", "saved corpus"),
                "parameters": run.settings.get("search_parameters", {}),
                "retrieved_count": run.settings.get(
                    "search_retrieved_count", len(papers)
                ),
                "duplicate_count": run.settings.get("search_duplicate_count", 0),
            },
            "count": len(papers),
            "papers": [
                {
                    **{k: v for k, v in source_dict(x).items() if k != "full_text"},
                    "full_text_available": bool(x.full_text),
                    "relevance_rank": index + 1,
                    "relevance_note": notes.get(x.id, ""),
                }
                for index, x in enumerate(papers)
            ],
            "provider": provider,
            "model": model,
            "ranked_paper_ids": ranked_ids,
            "source_snapshot": snapshot,
            "source_snapshot_hash": snapshot_hash,
        }
    elif stage == "extraction":
        if not sources:
            raise WorkflowError("No saved papers. Run Search first.")
        compact = [
            {
                k: v
                for k, v in source_dict(p).items()
                if k
                in ("id", "title", "authors", "year", "doi", "abstract", "full_text")
            }
            for p in sources[: run.settings.get("max_papers", 25)]
        ]
        compact = [
            {
                **p,
                "abstract": p.get("abstract", "")[:12000],
                "full_text": p.get("full_text", "")[:12000],
            }
            for p in compact
        ]
        extraction_fields = [
            "research_objective",
            "research_problem",
            "research_question",
            "methodology",
            "research_design",
            "dataset_or_sample",
            "data_collection_methods",
            "analysis_methods",
            "findings",
            "limitations",
            "research_gaps",
            "contributions",
        ]
        # Keep each request small enough for hosted models to finish reliably. A single
        # request containing 25 full texts can exceed provider latency limits. The
        # deterministic paper order and batch size are recorded with the artifact.
        batch_size = max(1, min(int(settings.extraction_batch_size), 5))
        system = (
            "For each input paper, extract the specified research fields. Never infer "
            "missing information: write 'Not reported' when unavailable. Return JSON "
            "{papers:[{paper_id,fields:{research_objective,research_problem,research_question,"
            "methodology,research_design,dataset_or_sample,data_collection_methods,"
            "analysis_methods,findings,limitations,research_gaps,contributions},"
            "evidence:[{field,quote,page_or_section}]}]}. Every substantive non-empty "
            "field must have at least one verbatim evidence quote tied to that same field "
            "and paper. Quotes must be copied exactly from the provided full text or "
            "abstract. Do not invent page or section references; use null when the source "
            "does not expose them. The source has an explicit source basis (full_text or abstract)."
        )
        response_papers = []
        response_claims = []
        usage_batches = []
        models = []
        response_format = None
        for offset in range(0, len(compact), batch_size):
            batch = compact[offset : offset + batch_size]
            prompt = (
                "Research question: "
                + project.question
                + "\nSources: "
                + json.dumps(batch, ensure_ascii=False)
                + (
                    "\nReviewer feedback: " + run.settings.get("review_note", "")
                    if run.settings.get("review_note")
                    else ""
                )
            )
            batch_response, batch_model, batch_usage = json_answer(
                provider,
                prompt,
                system,
                temperature=run.settings.get("temperature", 0.0),
            )
            models.append(batch_model)
            usage_batches.append(batch_usage)
            batch_format = (
                "papers"
                if isinstance(batch_response.get("papers"), list)
                else "claims"
                if isinstance(batch_response.get("claims"), list)
                else None
            )
            if batch_format is None:
                raise WorkflowError(
                    "Extraction response must contain a papers or claims array. Retry this stage."
                )
            if response_format is not None and batch_format != response_format:
                raise WorkflowError(
                    "Extraction batches returned inconsistent response formats. Retry this stage."
                )
            response_format = batch_format
            if batch_format == "papers":
                response_papers.extend(batch_response["papers"])
            else:
                response_claims.extend(batch_response["claims"])

        def sum_usage(key):
            values = [
                batch.get("usage", {}).get(key)
                for batch in usage_batches
                if isinstance(batch.get("usage"), dict)
            ]
            numeric = [value for value in values if isinstance(value, (int, float))]
            return sum(numeric) if numeric else None

        model = models[0]
        usage = {
            "usage": {
                key: sum_usage(key)
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cost",
                )
                if sum_usage(key) is not None
            },
            "prompt_sha256": (
                usage_batches[0].get("prompt_sha256")
                if len(usage_batches) == 1
                else hashlib.sha256(
                    "".join(
                        batch.get("prompt_sha256", "") for batch in usage_batches
                    ).encode()
                ).hexdigest()
            ),
            "request_duration_ms": sum(
                batch.get("request_duration_ms", 0)
                for batch in usage_batches
                if isinstance(batch.get("request_duration_ms", 0), (int, float))
            ),
        }
        if len(set(models)) > 1:
            raise WorkflowError(
                "The provider served different model IDs within one extraction stage."
            )
        raw_content = {
            response_format: response_papers
            if response_format == "papers"
            else response_claims
        }
        by_id = {p.id: p for p in sources}
        if isinstance(raw_content.get("papers"), list):
            normalized_papers = []
            accepted_claims = []
            rejected_evidence = 0
            unsupported_fields = 0
            for item in raw_content["papers"]:
                paper_id = item.get("paper_id") if isinstance(item, dict) else None
                if not isinstance(paper_id, str) or paper_id not in by_id:
                    continue
                paper = by_id[paper_id]
                full_text = " ".join((paper.full_text or "").casefold().split())
                abstract = " ".join((paper.abstract or "").casefold().split())
                evidence = []
                for ev in (
                    item.get("evidence", [])
                    if isinstance(item.get("evidence"), list)
                    else []
                ):
                    quote = ev.get("quote", "") if isinstance(ev, dict) else ""
                    field = ev.get("field") if isinstance(ev, dict) else None
                    if (
                        field not in extraction_fields
                        or not isinstance(quote, str)
                        or len(quote.strip()) < 12
                    ):
                        rejected_evidence += 1
                        continue
                    normalized_quote = " ".join(quote.casefold().split())
                    if normalized_quote in full_text:
                        basis = "full_text"
                    elif normalized_quote in abstract:
                        basis = "abstract"
                    else:
                        rejected_evidence += 1
                        continue
                    requested_location = ev.get("page_or_section")
                    page_markers = re.findall(
                        r"\[\[PAGE (\d+)\]\]",
                        paper.full_text or "",
                        flags=re.IGNORECASE,
                    )
                    location = (
                        requested_location
                        if isinstance(requested_location, str)
                        and (
                            requested_location in page_markers
                            or requested_location.casefold()
                            in (paper.full_text or "").casefold()
                        )
                        else None
                    )
                    evidence.append(
                        {
                            "field": field,
                            "quote": quote.strip(),
                            "source_basis": basis,
                            "page_or_section": location,
                        }
                    )
                raw_fields = (
                    item.get("fields") if isinstance(item.get("fields"), dict) else {}
                )
                fields = {}
                for field in extraction_fields:
                    value = raw_fields.get(field, "Not reported")
                    supported = any(e["field"] == field for e in evidence)
                    if (
                        not isinstance(value, str)
                        or not value.strip()
                        or value.strip().casefold() == "not reported"
                    ):
                        fields[field] = "Not reported"
                    elif supported:
                        fields[field] = value.strip()
                    else:
                        fields[field] = "Not reported"
                        unsupported_fields += 1
                normalized_papers.append(
                    {
                        "paper_id": paper_id,
                        "fields": fields,
                        "evidence": evidence,
                        "source_basis": "full_text"
                        if any(e["source_basis"] == "full_text" for e in evidence)
                        else "abstract"
                        if evidence
                        else "none",
                    }
                )
                for ev in evidence:
                    accepted_claims.append(
                        {
                            "claim": fields.get(ev["field"], ""),
                            "field": ev["field"],
                            "paper_id": paper_id,
                            "quote": ev["quote"],
                            "page_or_section": ev["page_or_section"],
                            "source_basis": ev["source_basis"],
                        }
                    )
            content = {
                "papers": normalized_papers,
                "claims": accepted_claims,
                "rejected_evidence_count": rejected_evidence,
                "unsupported_field_count": unsupported_fields,
                "processing": {
                    "strategy": "bounded_batches",
                    "batch_size": batch_size,
                    "batch_count": len(usage_batches),
                    "paper_count": len(compact),
                },
            }
        else:
            # Read older model responses from local pilots without treating them as fully structured records.
            raw_claims = raw_content.get("claims", [])
            if not isinstance(raw_claims, list):
                raise WorkflowError(
                    "Extraction response must contain a papers or claims array. Retry this stage."
                )
            accepted = []
            for claim in raw_claims:
                paper_id = claim.get("paper_id") if isinstance(claim, dict) else None
                quote = claim.get("quote", "") if isinstance(claim, dict) else ""
                if (
                    not isinstance(paper_id, str)
                    or paper_id not in by_id
                    or not isinstance(quote, str)
                    or len(quote.strip()) < 20
                ):
                    continue
                paper = by_id[paper_id]
                source_text = " ".join((paper.full_text or "").casefold().split())
                abstract_text = " ".join((paper.abstract or "").casefold().split())
                normalized_quote = " ".join(quote.casefold().split())
                if normalized_quote in source_text or normalized_quote in abstract_text:
                    accepted.append(claim)
            content = {
                "claims": accepted,
                "rejected_claim_count": max(0, len(raw_claims) - len(accepted)),
                "legacy_unstructured_response": True,
                "processing": {
                    "strategy": "bounded_batches",
                    "batch_size": batch_size,
                    "batch_count": len(usage_batches),
                    "paper_count": len(compact),
                },
            }
    elif stage == "organization":
        extraction = artifact_payload(db, run, "extraction")
        if not extraction:
            raise WorkflowError("Approve or create an extraction artifact first.")
        organization_claims = organization_claim_payload(extraction)
        organization_prompt = (
            "Question: "
            + project.question
            + "\nEvidence claims (array positions are the claim indices): "
            + json.dumps(organization_claims, ensure_ascii=False, separators=(",", ":"))
            + (
                "\nReviewer feedback: " + run.settings.get("review_note", "")
                if run.settings.get("review_note")
                else ""
            )
        )
        content, model, usage = json_answer(
            provider,
            organization_prompt,
            "Group only supplied evidence claims into themes. claim_indices are zero-based indexes into the supplied evidence claims array and must identify every statement's evidence. Do not invent comparisons or gaps. Return JSON: {themes:[{name,summary,claim_indices:[int],agreements:[{text,claim_indices:[int]}],disagreements:[{text,claim_indices:[int]}],methodological_comparisons:[{text,claim_indices:[int]}],gaps:[{text,claim_indices:[int]}]}]}. Agreements, disagreements, and methodological comparisons require at least two evidence claim references; gaps require at least one. Omit a comparison when evidence is insufficient.",
            temperature=run.settings.get("temperature", 0.0),
        )
        claim_count = len(organization_claims)
        content = validate_organization(content, claim_count)
    elif stage == "synthesis":
        org = artifact_payload(db, run, "organization")
        extraction = artifact_payload(db, run, "extraction")
        if not org or not extraction:
            raise WorkflowError("Extraction and organization artifacts are required.")
        content, model, usage = complete(
            provider,
            "Question: "
            + project.question
            + "\nScope: "
            + project.scope
            + "\nThemes: "
            + str(org)
            + "\nEvidence claims: "
            + str(extraction)
            + "\nWrite an academic literature synthesis with sections for Introduction and context, Thematic discussion, Methodological comparison, Agreements and disagreements, Limitations and research gaps, and Conclusion. Use inline citations [paper_id]. Only make claims supported by supplied evidence; citations must be attached to the claims they support. Include an evidence limitations section. Do not invent a reference list; citations must use supplied paper IDs."
            + (
                "\nReviewer feedback: " + run.settings.get("review_note", "")
                if run.settings.get("review_note")
                else ""
            ),
            "You write rigorous scientific literature syntheses. Never invent sources, facts, or citations. Keep citations attached to the claims they support.",
            temperature=run.settings.get("temperature", 0.0),
        )
        valid = {p.id for p in sources}
        draft, citations, rejected = sanitize_citations(content, valid)
        content = {
            "draft": draft,
            "citations": citations,
            "rejected_citations": rejected,
            "rejected_citation_count": len(rejected),
            "word_count": len(draft.split()),
        }
    else:
        raise WorkflowError("Unknown workflow stage.")
    latest = db.scalar(
        select(Artifact)
        .where(Artifact.run_id == run.id, Artifact.stage == stage)
        .order_by(Artifact.version.desc())
    )
    version = (latest.version + 1) if latest else 1
    review_policy = run.settings.get("review_policy", project.review_policy)
    art = Artifact(
        id=str(uuid.uuid4()),
        run_id=run.id,
        stage=stage,
        version=version,
        content=content,
        status="pending_review" if needs_review(review_policy, stage) else "approved",
        model=model,
    )
    telemetry = {
        "stage_duration_ms": round((time.perf_counter() - stage_started) * 1000, 3),
        "stage": "search" if stage == "search" else stage,
    }
    if stage in AI_STAGES:
        telemetry.update(
            {
                "usage": usage.get("usage", {}),
                "prompt_sha256": usage.get("prompt_sha256"),
                "provider": provider,
                "model": model,
                "prompt_version": "1.2" if stage == "organization" else "1.1" if stage in {"search", "extraction"} else "1.0",
                "temperature": run.settings.get("temperature", 0.0),
                "request_duration_ms": usage.get("request_duration_ms"),
            }
        )
    content = {**content, "_telemetry": telemetry}
    art.content = content
    db.add(art)
    run.current_stage = stage
    if art.status == "pending_review":
        run.status = "awaiting_review"
    else:
        run.status = "running"
    record(
        db,
        project.id,
        run.id,
        "artifact_created",
        f"{stage.title()} artifact v{version} created",
        {
            "artifact_id": art.id,
            "model": model,
            "review_required": art.status == "pending_review",
        },
    )
    db.commit()
    db.refresh(art)
    return art


def advance(db, project, run, from_stage=None):
    if run.settings.get("run_type") == "search_discovery":
        if from_stage is None:
            try:
                artifact = execute_stage(db, project, run, "search")
            except Exception as exc:
                run.status = "failed"
                record(
                    db,
                    project.id,
                    run.id,
                    "stage_failed",
                    f"Search discovery failed: {str(exc)[:500]}",
                    {"stage": "search"},
                )
                db.commit()
                raise
            if artifact.status == "pending_review":
                return artifact
        run.status = "completed"
        run.current_stage = "search"
        record(
            db,
            project.id,
            run.id,
            "search_discovery_completed",
            "Candidate discovery and corpus selection completed",
            {
                "source_snapshot_hash": run.settings.get("source_snapshot_hash"),
                "source_paper_ids": run.settings.get("source_paper_ids", []),
            },
        )
        db.commit()
        return None
    start = (STAGES.index(from_stage) + 1) if from_stage in STAGES else 0
    for stage in STAGES[start:]:
        try:
            art = execute_stage(db, project, run, stage)
        except Exception as exc:
            run.status = "failed"
            record(
                db,
                project.id,
                run.id,
                "stage_failed",
                f"{stage.title()} failed: {str(exc)[:500]}",
                {"stage": stage},
            )
            db.commit()
            raise
        if art.status == "pending_review":
            return art
    run.status = "completed"
    run.current_stage = "synthesis"
    record(db, project.id, run.id, "run_completed", "Synthesis workflow completed")
    db.commit()
    return None
