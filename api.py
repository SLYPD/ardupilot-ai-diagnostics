"""
FastAPI Dashboard — REST API for the Hardware & Telemetry Copilot.

Provides endpoints for telemetry upload, component selection, and
async diagnostic processing.

Usage:
    uvicorn api:app --reload --port 8000
"""
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile

from main import (
    build_system_prompt,
    diagnose_anomalies_parallel,
    format_profile_for_llm,
)
from parser import extract_anomalies
from template_builder import (
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
    auto_detect_profile,
    build_profile,
    list_all_components,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {".csv", ".bin", ".tlog", ".rlog"}
TEMP_DIR = "temp_uploads"

# In-memory job store (replace with Redis/DB for production)
_jobs: dict[str, dict] = {}

# Logger
_log = logging.getLogger("api")

# ---------------------------------------------------------------------------
# File content validation
# ---------------------------------------------------------------------------
def _validate_file_content(content: bytes, ext: str) -> str | None:
    """Return an error message if the content doesn't match the claimed
    extension, or None if it passes."""
    if len(content) == 0:
        return "Uploaded file is empty."
    if ext == ".csv":
        if not re.match(rb'^[\x20-\x7E\r\n\t,]+', content[:200]):
            return "File does not appear to contain valid CSV text."
    elif ext in (".tlog", ".rlog"):
        # MAVLink telemetry — pymavlink is the authority.  Scan the first
        # 256 bytes for a magic marker rather than requiring it at byte 0.
        window = content[:256]
        if b'\xfd' in window or b'\xfe' in window:
            return None
        if len(content) >= 8 and content[:8] == b'\x00\x00\x00\x00\x00\x00\x00\x00':
            return None
        # No magic found — defer to pymavlink for final validation.
        return None
    elif ext == ".bin":
        # ArduPilot Dataflash logs have their own binary format —
        # pymavlink auto-detects it. Emptiness already checked by caller.
        return None

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(TEMP_DIR, exist_ok=True)
    yield
    # Cleanup temp files on shutdown
    for job in list(_jobs.values()):
        tp = job.get("temp_path", "")
        if tp and os.path.exists(tp):
            try:
                os.remove(tp)
            except Exception:
                pass
    try:
        if os.path.isdir(TEMP_DIR) and not os.listdir(TEMP_DIR):
            os.rmdir(TEMP_DIR)
    except OSError:
        pass

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Hardware & Telemetry Copilot API",
    description="Diagnostic engine for ArduPilot/MAVLink telemetry logs",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {
        "service": "Hardware & Telemetry Copilot API",
        "version": "1.0.0",
        "endpoints": {
            "GET /components": "List available hardware components",
            "POST /diagnose": "Upload and diagnose a telemetry log",
            "GET /diagnose/{job_id}": "Get diagnostic results for a job",
            "DELETE /diagnose/{job_id}": "Remove a completed/failed job",
            "GET /health": "Health check",
        },
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/components")
async def list_components():
    """Return all available hardware components by category."""
    return list_all_components()

@app.post("/diagnose")
async def diagnose(
    file: UploadFile = File(...),
    fc: str = Form(DEFAULT_FC),
    power: str = Form(DEFAULT_POWER),
    propulsion: str = Form(DEFAULT_PROPULSION),
    api_key: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Upload a telemetry log and start async diagnostic processing.

    Returns a job_id that can be polled via GET /diagnose/{job_id}.
    """
    # --- Validate extension ---
    ext = Path(file.filename or "unknown.bin").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {ALLOWED_EXTENSIONS}",
        )

    # --- Read and validate content ---
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / (1024*1024):.1f} MB). "
                   f"Maximum is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    content_error = _validate_file_content(content, ext)
    if content_error:
        raise HTTPException(status_code=400, detail=content_error)

    # --- Write temp file ---
    job_id = uuid.uuid4().hex
    tmp_path = os.path.join(TEMP_DIR, f"{job_id}{ext}")
    with open(tmp_path, "wb") as f:
        f.write(content)

    # --- Auto-detect hardware profile from log metadata ---
    detected_components = None
    try:
        detected_components = auto_detect_profile(tmp_path)
    except Exception:
        _log.debug("Auto-detect skipped for %s", file.filename)

    # Use detected components when the user hasn't explicitly chosen
    # different values (i.e. left the defaults unchanged).
    user_chose_explicit = (
        fc != DEFAULT_FC or power != DEFAULT_POWER or propulsion != DEFAULT_PROPULSION
    )
    if not user_chose_explicit and detected_components:
        fc = detected_components.get("fc", fc)
        power = detected_components.get("power", power)
        propulsion = detected_components.get("propulsion", propulsion)

    # --- Create job record ---
    _jobs[job_id] = {
        "status": "queued",
        "filename": file.filename,
        "fc": fc,
        "power": power,
        "propulsion": propulsion,
        "temp_path": tmp_path,
        "error": None,
        "anomalies_count": 0,
        "reports": [],
        "detected_components": detected_components,
    }

    # --- Process in background ---
    background_tasks.add_task(_process_diagnostic, job_id, api_key)

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": file.filename,
        "components": {"fc": fc, "power": power, "propulsion": propulsion},
        "detected_components": detected_components,
    }

async def _process_diagnostic(job_id: str, api_key: str):
    """Background task: build profile, scan anomalies, call LLM."""
    job = _jobs.get(job_id)
    if not job:
        return

    try:
        job["status"] = "processing"

        # 1. Build profile
        profile = build_profile(
            fc=job["fc"], power=job["power"], propulsion=job["propulsion"]
        )
        job["profile_name"] = profile["profile"]["name"]

        # 2. Scan for anomalies
        anomalies = extract_anomalies(job["temp_path"], profile=profile)
        job["anomalies_count"] = len(anomalies)

        if not anomalies:
            job["status"] = "complete"
            job["result"] = "nominal"
            return

        # 3. Build system prompt
        hw_text = format_profile_for_llm(profile)
        system_prompt = build_system_prompt(hw_text)

        # 4. Diagnose all anomalies in parallel (retries handled by utility)
        reports = diagnose_anomalies_parallel(
            anomalies, system_prompt, api_key=api_key
        )

        job["reports"] = reports
        job["status"] = "complete"
        job["result"] = "anomalies_found"

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        # Cleanup temp file
        tp = job.get("temp_path", "")
        if tp and os.path.exists(tp):
            try:
                os.remove(tp)
            except Exception:
                pass

@app.get("/diagnose/{job_id}")
async def get_diagnostic_result(job_id: str):
    """Poll for diagnostic results by job_id."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {
        "job_id": job_id,
        "status": job["status"],
        "filename": job.get("filename", "unknown"),
        "anomalies_count": job.get("anomalies_count", 0),
        "components": {
            "fc": job.get("fc"),
            "power": job.get("power"),
            "propulsion": job.get("propulsion"),
        },
        "detected_components": job.get("detected_components"),
    }

    if job["status"] == "error":
        response["error"] = job.get("error")
    elif job["status"] == "complete":
        response["result"] = job.get("result")
        response["reports"] = job.get("reports", [])

    return response

@app.delete("/diagnose/{job_id}")
async def delete_job(job_id: str):
    """Remove a completed or failed job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    del _jobs[job_id]
    return {"status": "deleted"}

# ---------------------------------------------------------------------------
# Direct runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)  # nosec B104
