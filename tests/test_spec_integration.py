from pathlib import Path

from skillcall_spec.conformance import validate_repository


REPO = Path(__file__).resolve().parents[1]


def test_router_implementations_conform_to_external_spec():
    assert validate_repository(
        REPO / "packages" / "capability-spec",
        REPO / "skills",
    ) == {
        "capabilities": 5,
        "examples": 11,
        "manifests": 10,
        "provisions": 19,
    }


def test_eval_example_implementations_conform_to_external_spec():
    assert validate_repository(
        REPO / "packages" / "capability-spec",
        REPO / "packages" / "skill-eval" / "implementations",
    ) == {
        "capabilities": 5,
        "examples": 11,
        "manifests": 2,
        "provisions": 2,
    }
