from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_PARTS = {".git", ".pytest_cache", ".venv", "__pycache__"}
TEXT_SUFFIXES = {
    "",
    ".dockerignore",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}


def test_codebase_uses_docker_health_agent_naming() -> None:
    retired_name = "".join(["watch", "dog"])
    retired_terms = [
        retired_name,
        "docker-health-agent-" + retired_name,
    ]

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or SKIPPED_PARTS.intersection(path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_SUFFIXES:
            continue

        content = path.read_text(encoding="utf-8")
        lower_content = content.lower()
        assert not any(term in lower_content for term in retired_terms), path.relative_to(REPO_ROOT)


def test_compose_service_image_and_container_use_same_name() -> None:
    compose = (REPO_ROOT / "compose.yml").read_text(encoding="utf-8")
    deploy_script = (REPO_ROOT / "scripts" / "deploy_server.sh").read_text(encoding="utf-8")

    assert "  docker-health-agent:" in compose
    assert "image: docker-health-agent:latest" in compose
    assert "container_name: docker-health-agent" in compose
    assert "up -d --build docker-health-agent" in deploy_script
