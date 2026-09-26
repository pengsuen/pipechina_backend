"""Durable single-worker parser queue. No caller-supplied URLs or filesystem paths."""

import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from app.shared.media.documents import validate_document

ROOT = Path(os.environ.get("PARSER_DATA", "/data"))
API_KEY = os.environ["API_KEY"]
if not API_KEY:
    raise RuntimeError("API_KEY is required")
SUFFIXES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


@contextmanager
def database():
    connection = sqlite3.connect(ROOT / "jobs.sqlite", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def status(job):
    value = {
        "id": job["id"],
        "status": job["status"],
        "blocks": [],
        "warnings": [],
        "parser_version": "docling-structure-v1",
    }
    if job["status"] == "succeeded":
        value.update(json.loads((ROOT / (job["id"] + ".json")).read_text()))
    if job["status"] == "failed":
        value["warnings"] = ["conversion_failed_or_timed_out"]
    return value


async def worker():
    while True:
        with database() as db:
            job = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if job:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        if not job:
            await asyncio.sleep(0.5)
            continue
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("convert.py")),
                str(ROOT / job["filename"]),
                str(job["pages"]),
                str(ROOT / (job["id"] + ".json")),
                stdout=asyncio.subprocess.DEVNULL,
                # Operator diagnostics stay in container logs, never in the public result.
                stderr=None,
            )
            async with asyncio.timeout(840):
                code = await process.wait()
            state = "succeeded" if code == 0 else "failed"
        except TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            state = "failed"
        except BaseException:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            with database() as db:
                db.execute("UPDATE jobs SET status='failed' WHERE id=?", (job["id"],))
            raise
        with database() as db:
            db.execute("UPDATE jobs SET status=? WHERE id=?", (state, job["id"]))


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(ROOT.mkdir, parents=True, exist_ok=True)
    with database() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, "
            "digest TEXT, filename TEXT, pages INTEGER, status TEXT)"
        )
        db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    task = asyncio.create_task(worker())
    app.state.worker = task
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def authenticate(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {API_KEY}"):
        raise HTTPException(401, "invalid service credential")


app = FastAPI(lifespan=lifespan, dependencies=[Depends(authenticate)])


@app.get("/health/live")
async def health():
    if app.state.worker.done():
        raise HTTPException(503, "worker unavailable")
    return {"status": "ready"}


@app.post("/v1/parse-jobs", status_code=202)
async def submit(
    file: UploadFile = File(),
    max_pages: int = Form(ge=1, le=2000),
    idempotency_key: str = Header(min_length=1, max_length=200),
):
    suffix = SUFFIXES.get(file.content_type)
    if not suffix:
        raise HTTPException(415, "unsupported document type")
    task_id = str(uuid4())
    path = ROOT / (task_id + suffix)
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("xb") as output:
            while data := await file.read(1024 * 1024):
                size += len(data)
                if size > 200 * 1024 * 1024:
                    raise HTTPException(413, "document too large")
                digest.update(data)
                output.write(data)
        if size == 0:
            raise HTTPException(422, "empty document")
        try:
            await asyncio.to_thread(validate_document, path, file.content_type, 200 * 1024 * 1024)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with database() as db:
            previous = db.execute(
                "SELECT * FROM jobs WHERE request_key=?", (idempotency_key,)
            ).fetchone()
            if previous:
                if previous["digest"] != digest.hexdigest() or previous["pages"] != max_pages:
                    raise HTTPException(409, "idempotency content mismatch")
                if previous["status"] == "failed":
                    db.execute("UPDATE jobs SET status='queued' WHERE id=?", (previous["id"],))
                    previous = dict(previous)
                    previous["status"] = "queued"
                return status(previous)
            if (
                db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 100
            ):
                raise HTTPException(429, "parser queue full")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (task_id, idempotency_key, digest.hexdigest(), path.name, max_pages, "queued"),
            )
            job = db.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        path = None
        return status(job)
    finally:
        if path:
            path.unlink(missing_ok=True)
        await file.close()


@app.get("/v1/parse-jobs/{job_id}")
async def get_job(job_id: str):
    with database() as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "parse job not found")
    return status(job)
