"""Read-only exact filesystem SHA-256 witness of four coordinator source modules."""
from pathlib import Path
from hashlib import sha256

NAMES = ("serviceit_b2b_public_mirrors.py", "serviceit_b2b_routerai_deep.py",
         "serviceit_vps_b2b_deep_research.py", "serviceit_vps_worker.py")
for name in NAMES:
    path=Path("/app/leadpilot")/name
    assert path.is_file() and not path.is_symlink()
    print(sha256(path.read_bytes()).hexdigest(), flush=True)
