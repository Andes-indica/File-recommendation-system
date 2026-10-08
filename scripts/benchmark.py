"""Synthetic 10k-file/100k-chunk CPU benchmark with native vector retrieval."""

import argparse
import hashlib
import json
import platform
import random
import statistics
import struct
import tempfile
import time
from pathlib import Path

from file_recommender.agent import Agent
from file_recommender.index import IndexStore
from file_recommender.storage import LibraryDB, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.files < 1 or args.runs < 2:
        parser.error("Use positive file counts and at least two runs.")
    randomizer = random.Random(42)
    with tempfile.TemporaryDirectory(prefix="folio-benchmark-") as temporary:
        library = LibraryDB(IndexStore(Path(temporary) / "index.sqlite3"))
        with library.connect() as db:
            db.execute(
                "INSERT INTO sources(id,name,root,created_at) VALUES('bench','Synthetic benchmark',?,?)",
                (temporary, now()),
            )
            for i in range(args.files):
                name = f"document-{i}.md"
                text = (
                    f"Project project{i} department{i % 100} migration milestone plan and technical deployment report. "
                    * 10
                )
                doc_id = db.execute(
                    "INSERT INTO documents(path,source_root,name,extension,size,modified_at,content) VALUES(?,?,?,'.md',?,?,?)",
                    (f"{temporary}/{name}", temporary, name, len(text), now(), text),
                ).lastrowid
                db.execute(
                    "INSERT INTO app_files(document_id,source_id,fingerprint) VALUES(?,'bench','synthetic')",
                    (doc_id,),
                )
                db.execute(
                    "INSERT INTO document_fts(rowid,name,path,content) VALUES(?,?,?,?)",
                    (doc_id, name, name, text),
                )
                for n in range(10):
                    chunk = f"{text} Segment {n}."
                    content_hash = hashlib.sha256(chunk.encode()).hexdigest()
                    chunk_id = db.execute(
                        "INSERT INTO file_chunks(document_id,chunk_index,content_hash,content) VALUES(?,?,?,?)",
                        (doc_id, n, content_hash, chunk),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO chunk_fts(rowid,name,document_id,content) VALUES(?,?,?,?)",
                        (chunk_id, name, doc_id, chunk),
                    )
                    vector = [randomizer.uniform(-1, 1) for _ in range(384)]
                    db.execute(
                        "INSERT INTO chunk_embeddings VALUES(?,?,'benchmark-384',384,?)",
                        (chunk_id, content_hash, struct.pack("<384f", *vector)),
                    )
        library.set_settings({"embedding_model": "benchmark-384", "embedding_status": "ready"})
        agent = Agent(library)
        query_vector = [randomizer.uniform(-1, 1) for _ in range(384)]
        agent.embedding = lambda *_: query_vector
        timings = {"lexical": [], "hybrid": []}
        for mode in timings:
            for n in range(args.runs + 2):
                started = time.monotonic()
                agent.candidates(
                    {
                        "terms": [f"project{n % args.files}", "migration"],
                        "filters": {},
                        "route": "keyword" if mode == "lexical" else "hybrid",
                        "effective_query": f"project{n % args.files} migration",
                        "started": started,
                    }
                )
                elapsed = (time.monotonic() - started) * 1000
                if n >= 2:
                    timings[mode].append(elapsed)
        result = {
            "kind": "synthetic_retrieval_only",
            "files": args.files,
            "chunks": args.files * 10,
            "embedding_dimensions": 384,
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "runs": args.runs,
            "excludes": "Model encoding, graph execution, UI, and indexing throughput",
            "metrics": {
                mode: {
                    "median_ms": round(statistics.median(values), 2),
                    "p95_ms": round(
                        sorted(values)[__import__("math").ceil(0.95 * len(values)) - 1], 2
                    ),
                }
                for mode, values in timings.items()
            },
        }
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
