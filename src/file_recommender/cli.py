"""A single local launch command and a separately restartable indexing worker."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Local intelligent file workspace")
    parser.add_argument("command", nargs="?", choices=["serve", "worker"], default="serve")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("FILE_RECOMMENDER_DATA_DIR", ".file-recommender")),
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-worker", action="store_true")
    args = parser.parse_args()
    args.data_dir = args.data_dir.expanduser().resolve()
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.data_dir.chmod(0o700)
    database = os.environ.get("FILE_RECOMMENDER_DB", str(args.data_dir / "index.sqlite3"))
    os.environ["FILE_RECOMMENDER_DB"] = database
    if args.command == "worker":
        from .worker import run_worker

        run_worker(database)
        return
    process = None
    if not args.no_worker:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "file_recommender.cli",
                "worker",
                "--data-dir",
                str(args.data_dir),
            ]
        )
    try:
        import uvicorn

        print(
            f"\nFile workspace → http://127.0.0.1:{args.port}\nData → {Path(database).parent}\n",
            flush=True,
        )
        uvicorn.run("file_recommender.api:app", host="127.0.0.1", port=args.port, log_level="info")
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
