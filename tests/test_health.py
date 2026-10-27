from fastapi.testclient import TestClient

from agentic_workflow_for_literature_synthesis.main import app

client = TestClient(app)


def test_health_check_returns_expected_response() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "literature-synthesis-api",
        "version": "0.1.0",
    }
