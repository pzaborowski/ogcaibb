"""Docker-backed runners for the two operations that absolutely require it:
  - bblocks-postprocess (validation + build artefacts)
  - pygeoapi (local test harness)

We use the docker SDK rather than shelling out so we can stream logs and
clean up containers reliably on cancel/timeout.
"""

from __future__ import annotations

import logging
from pathlib import Path

import docker

from ..config import settings
from .registry import register

log = logging.getLogger(__name__)


def _client():
    return docker.from_env()


@register(
    "BblocksPostprocess",
    "Run ogcincubator/bblocks-postprocess against a bblock repository to "
    "validate and generate build artefacts. Returns container stdout.",
)
async def bblocks_postprocess(repo_path: str, base_url: str = "http://localhost") -> str:
    p = Path(repo_path).expanduser().resolve()
    root = settings.workspace_root.expanduser().resolve()
    p.relative_to(root)  # raises if outside

    container = _client().containers.run(
        image="ogcincubator/bblocks-postprocess:latest",
        command=[
            "python",
            "-m",
            "bblocks.process_config",
            ".",
            "--split-docs",
            f"--base-url={base_url}",
        ],
        volumes={str(p): {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        detach=True,
        remove=False,
    )
    try:
        result = container.wait(timeout=600)
        logs = container.logs().decode("utf-8", errors="replace")
        status = result.get("StatusCode", -1)
        return f"exit={status}\n{logs}"
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@register(
    "PygeoapiUp",
    "Start a pygeoapi container with a generated config mounted. "
    "Returns container id and the local URL it is bound to.",
)
async def pygeoapi_up(
    config_path: str,
    examples_path: str,
    port: int = 5000,
    image: str = "geopython/pygeoapi:latest",
) -> str:
    cfg = Path(config_path).expanduser().resolve()
    ex = Path(examples_path).expanduser().resolve()
    container = _client().containers.run(
        image=image,
        ports={"80/tcp": port},
        volumes={
            str(cfg): {"bind": "/pygeoapi/local.config.yml", "mode": "ro"},
            str(ex): {"bind": "/data", "mode": "ro"},
        },
        environment={"PYGEOAPI_CONFIG": "/pygeoapi/local.config.yml"},
        detach=True,
        remove=False,
    )
    return f"container={container.id[:12]} url=http://localhost:{port}"


@register("PygeoapiDown", "Stop and remove a pygeoapi container by id.")
async def pygeoapi_down(container_id: str) -> str:
    c = _client().containers.get(container_id)
    c.stop(timeout=10)
    c.remove(force=True)
    return f"removed {container_id}"
