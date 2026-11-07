import csv
import io
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base
from app.experiment_log import SUMMARY_CSV_HEADERS, experiment_log_csv
from app.models import Project, Run


class ExperimentLogTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.project = Project(
            id=str(uuid.uuid4()),
            title="Experiment log test",
            question="How does supervised synthesis compare with autonomous synthesis?",
            scope="Literature synthesis methods",
        )
        self.session.add(self.project)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_csv_export_contains_clear_rows_for_all_run_statuses(self):
        run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            status="completed",
            current_stage="synthesis",
            models={
                stage: "openai"
                for stage in ("search", "extraction", "organization", "synthesis")
            },
            settings={
                "condition": "with_human_supervision_all_stages",
                "experiment_label": "pilot-1",
                "replicate": "pair-01",
                "protocol_version": "2.0",
                "temperature": 0,
                "random_seed": 42,
                "source_paper_ids": ["paper-1", "paper-2"],
                "source_snapshot_hash": "abc123",
                "model_ids": {
                    stage: "test-model"
                    for stage in ("search", "extraction", "organization", "synthesis")
                },
            },
        )
        self.session.add(run)
        self.session.commit()

        response = main.download_experiment_log(self.project.id, self.session)
        rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))

        self.assertEqual(response.media_type, "text/csv")
        self.assertIn("experiment-log.csv", response.headers["content-disposition"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), len(SUMMARY_CSV_HEADERS))
        self.assertEqual(rows[0]["Experimental condition"], "Condition B - Human review")
        self.assertEqual(rows[0]["Human review between agents"], "Yes - reviewer approves each stage")
        self.assertEqual(rows[0]["Condition code (internal)"], "with_human_supervision_all_stages")
        self.assertEqual(rows[0]["Corpus papers"], "2")
        self.assertEqual(rows[0]["Corpus fingerprint (SHA-256)"], "abc123")
        self.assertEqual(rows[0]["Run status"], "completed")
        self.assertEqual(rows[0]["Provider-reported cost"], "")

    def test_csv_condition_labels_are_plain_language_for_both_conditions(self):
        for number, condition in enumerate(
            (
                "without_intermediate_human_supervision",
                "with_human_supervision_all_stages",
            ),
            start=1,
        ):
            self.session.add(
                Run(
                    id=str(uuid.uuid4()),
                    project_id=self.project.id,
                    status="completed",
                    current_stage="synthesis",
                    models={stage: "openai" for stage in ("search", "extraction", "organization", "synthesis")},
                    settings={"condition": condition, "replicate": f"pair-{number}"},
                )
            )
        self.session.commit()

        rows = list(csv.DictReader(io.StringIO(experiment_log_csv(self.session, self.project))))

        self.assertEqual(
            [row["Experimental condition"] for row in rows],
            ["Condition A - Automatic", "Condition B - Human review"],
        )
        self.assertEqual(
            [row["Human review between agents"] for row in rows],
            ["No - automatic progression", "Yes - reviewer approves each stage"],
        )

    def test_csv_export_returns_headers_when_project_has_no_runs(self):
        output = experiment_log_csv(self.session, self.project)
        rows = list(csv.reader(io.StringIO(output)))

        self.assertEqual(rows[0], SUMMARY_CSV_HEADERS)
        self.assertEqual(len(rows), 1)

    def test_checked_in_template_stays_in_sync_with_download_headers(self):
        template = (
            Path(__file__).resolve().parents[2] / "experiment_run_log_template.csv"
        )
        with template.open(newline="", encoding="utf-8") as source:
            rows = list(csv.reader(source))

        self.assertEqual(rows[0], SUMMARY_CSV_HEADERS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[1]), len(SUMMARY_CSV_HEADERS))


if __name__ == "__main__":
    unittest.main()
