"""Start the persistent server; settings come from its local environment/config."""
import os

import uvicorn
from backend.config import get_config


def main():
    config = get_config()
    if config.vault_dir is None:
        raise RuntimeError("Server requires OBSIDIAN_VAULT_DIR or an explicit vault_dir")
    config.delete_video_after_ingest = False
    uvicorn.run("backend.main:app", host="127.0.0.1", port=int(os.getenv("KNOWLEDGE_PORT", "8787")))


if __name__ == "__main__":
    main()
