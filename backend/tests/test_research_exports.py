import csv
import io
import unittest
import uuid
import zipfile

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base
from app.models import Artifact, EvaluationRecord, Project, ReviewRecord, Run
from app.schemas import RunResultRecordCreate
from app.research_exports import (
    PRIMARY_HEADERS,
    build_five_tables,
    build_run_summary,
    run_research_tables_zip,
    run_summary_csv,
)


class ResearchExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.project = Project(
            id=str(uuid.uuid4()),
            title="Research export test",
            question="Does intermediate human supervision improve literature synthesis?",
            scope="Controlled corpus",
        )
        self.session.add(self.project)
        self.run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            status="completed",
            current_stage="synthesis",
            models={stage: "openai" for stage in ("search", "extraction", "organization", "synthesis")},
            settings={
                "condition": "with_human_supervision_all_stages",
                "replicate": "pair-01",
                "protocol_version": "2.0",
                "source_mode": "saved_corpus",
                "source_paper_ids": [],
                "source_snapshot_hash": "frozen-hash-001",
                "model_ids": {stage: "gpt-test-model" for stage in ("search", "extraction", "organization", "synthesis")},
            },
        )
        self.session.add(self.run)
        self.session.flush()

        self.artifacts = {}
        for stage, content in (
            ("search", {"count": 0, "papers": [], "ranked_paper_ids": []}),
            ("extraction", {"papers": [{"paper_id": "paper-1", "fields": {"methodology": "Experimental"}, "evidence": []}]}),
            ("organization", {"themes": [{"name": "Theme 1", "agreements": [], "disagreements": [], "gaps": []}]}),
            ("synthesis", {"draft": "A short synthesis.", "word_count": 3, "citations": []}),
        ):
            artifact = Artifact(
                id=str(uuid.uuid4()), run_id=self.run.id, stage=stage, version=1,
                content=content, status="approved", model="gpt-test-model",
            )
            self.session.add(artifact)
            self.artifacts[stage] = artifact
        self.session.flush()

        for stage, scores, evidence in (
            ("extraction", {"factual_accuracy": 4, "evidence_support": 5}, [{"claim": "The paper used an experiment.", "status": "supported", "source": "p. 2"}]),
            ("organization", {"organization_quality": 4}, []),
            ("synthesis", {"synthesis_quality": 5}, []),
        ):
            self.session.add(EvaluationRecord(
                id=str(uuid.uuid4()), run_id=self.run.id,
                artifact_id=self.artifacts[stage].id, stage=stage,
                evaluator_id="evaluator-1", rubric_version="v1", scores=scores,
                comments="Independently evaluated", evidence_references=evidence,
                reference_paper_ids=[],
            ))
        self.session.add(ReviewRecord(
            id=str(uuid.uuid4()), run_id=self.run.id,
            artifact_id=self.artifacts["search"].id, stage="search",
            revision_number=1, decision="approve", comments="Approved",
            correction=None, screening_decisions={}, ratings={},
            review_duration_ms=120000, original_content={"papers": []},
        ))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_primary_csv_has_only_six_research_outcomes_plus_run_identifiers(self):
        result = run_summary_csv(self.session, self.run)
        rows = list(csv.DictReader(io.StringIO(result)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), PRIMARY_HEADERS)
        self.assertEqual(rows[0]["condition"], "B_HUMAN_SUPERVISED")
        self.assertEqual(rows[0]["extraction_accuracy_mean_1_to_5"], "4.0")
        self.assertEqual(rows[0]["evidence_traceability_mean_1_to_5"], "5.0")
        self.assertEqual(rows[0]["organization_quality_mean_1_to_5"], "4.0")
        self.assertEqual(rows[0]["literature_synthesis_quality_mean_1_to_5"], "5.0")
        self.assertEqual(rows[0]["human_review_time_minutes"], "2.0")
        self.assertEqual(rows[0]["corpus_fingerprint_sha256"], "frozen-hash-001")

    def test_download_endpoint_returns_one_run_csv(self):
        response = main.download_run_research_summary(self.run.id, self.session)
        self.assertEqual(response.media_type, "text/csv; charset=utf-8")
        self.assertIn("six-research-outcomes.csv", response.headers["content-disposition"])
        rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], self.run.id)

    def test_download_endpoint_rejects_incomplete_runs(self):
        self.run.status = "running"
        self.session.commit()
        with self.assertRaises(HTTPException) as context:
            main.download_run_research_summary(self.run.id, self.session)
        self.assertEqual(context.exception.status_code, 409)

    def test_missing_independent_scores_remain_blank(self):
        self.session.query(EvaluationRecord).delete()
        self.session.commit()
        summary = build_run_summary(self.session, self.run)
        self.assertIsNone(summary["extraction_accuracy_mean_1_to_5"])
        self.assertIsNone(summary["evidence_traceability_mean_1_to_5"])
        self.assertIsNone(summary["organization_quality_mean_1_to_5"])
        self.assertIsNone(summary["literature_synthesis_quality_mean_1_to_5"])

    def test_compact_result_record_saves_and_updates_five_scores(self):
        body = RunResultRecordCreate(
            evaluator_id="evaluator-1",
            rubric_version="v1",
            extraction_accuracy=4,
            extraction_completeness=3,
            evidence_traceability=5,
            organization_quality=4,
            synthesis_quality=5,
            comments="Ratings checked against the source artifacts.",
        )
        response = main.save_run_result_record(self.run.id, body, self.session)
        self.assertEqual(set(response["saved_stages"]), {"extraction", "organization", "synthesis"})

        summary = build_run_summary(self.session, self.run)
        self.assertEqual(summary["extraction_accuracy_mean_1_to_5"], 4.0)
        self.assertEqual(summary["extraction_completeness_score_mean_1_to_5"], 3.0)
        self.assertEqual(summary["evidence_traceability_mean_1_to_5"], 5.0)
        self.assertEqual(summary["organization_quality_mean_1_to_5"], 4.0)
        self.assertEqual(summary["literature_synthesis_quality_mean_1_to_5"], 5.0)

        body.extraction_accuracy = 2
        response = main.save_run_result_record(self.run.id, body, self.session)
        self.assertEqual(set(response["saved_stages"]), {"extraction", "organization", "synthesis"})
        rows = self.session.query(EvaluationRecord).filter_by(
            run_id=self.run.id, evaluator_id="evaluator-1", rubric_version="v1"
        ).all()
        self.assertEqual(len(rows), 3)
        updated = build_run_summary(self.session, self.run)
        self.assertEqual(updated["extraction_accuracy_mean_1_to_5"], 2.0)

    def test_compact_result_record_rejects_incomplete_run(self):
        self.run.status = "running"
        self.session.commit()
        body = RunResultRecordCreate(
            evaluator_id="evaluator-1",
            rubric_version="v1",
            synthesis_quality=4,
        )
        with self.assertRaises(HTTPException) as context:
            main.save_run_result_record(self.run.id, body, self.session)
        self.assertEqual(context.exception.status_code, 409)

    def test_five_linked_tables_are_created_and_join_on_run_id(self):
        tables = build_five_tables(self.session, self.run)
        self.assertEqual(set(tables), {
            "experiment_runs.csv", "quality_evaluation.csv", "claim_evidence.csv",
            "human_interventions.csv", "system_performance.csv",
        })
        for rows, headers in tables.values():
            for row in rows:
                if row:
                    self.assertEqual(row["run_id"], self.run.id)
        with zipfile.ZipFile(io.BytesIO(run_research_tables_zip(self.session, self.run))) as archive:
            self.assertEqual(set(archive.namelist()), set(tables) | {"README.txt"})


if __name__ == "__main__":
    unittest.main()
