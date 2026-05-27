from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
import httpx
import uuid
from datetime import datetime

from app.google_drive import (
    list_files,
    list_folder_files,
    get_spreadsheet_sheets,
)
from app.analyzer import analyze_excel_batched
from app.model import AnalyzeRequest

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://analisis-data-instagram-fe.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── IN-MEMORY JOB STORE ──────────────────────────────────────
jobs = {}  # job_id → status dict

@app.on_event("startup")
def startup_event():
    os.makedirs("storage", exist_ok=True)
    print("[STARTUP] Siap.")

@app.get("/files")
def get_files():
    return {"success": True, "data": list_files()}

@app.get("/files/{folder_id}")
def get_folder_files(folder_id: str):
    try:
        return {"success": True, "data": list_folder_files(folder_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/{file_id}/sheets")
def get_sheets(file_id: str):
    try:
        return get_spreadsheet_sheets(file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── ANALYZE — LANGSUNG RETURN JOB ID ─────────────────────────
@app.post("/analyze")
def analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    has_cookies = (
        bool(os.environ.get("INSTAGRAM_COOKIES")) or
        os.path.exists("storage/cookies.json")
    )
    if not has_cookies:
        raise HTTPException(status_code=400, detail="Sesi Instagram belum tersimpan.")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id":     job_id,
        "status":     "running",
        "progress":   0,
        "total":      0,
        "done":       0,
        "batch_done": 0,
        "batch_total":0,
        "message":    "Memulai analisis...",
        "result":     None,
        "error":      None,
        "started_at": datetime.utcnow().isoformat(),
    }

    background_tasks.add_task(
        run_analyze_job,
        job_id,
        request.file_id,
        request.sheet_name,
    )

    return {"success": True, "job_id": job_id, "message": "Job dimulai, gunakan job_id untuk cek status."}


def run_analyze_job(job_id: str, file_id: str, sheet_name: str):
    try:
        def on_progress(done, total, batch_done, batch_total, msg):
            jobs[job_id].update({
                "done":        done,
                "total":       total,
                "batch_done":  batch_done,
                "batch_total": batch_total,
                "progress":    round(done / total * 100) if total > 0 else 0,
                "message":     msg,
            })

        result = analyze_excel_batched(
            spreadsheet_id=file_id,
            sheet_name=sheet_name,
            batch_size=100,
            on_progress=on_progress,
        )

        jobs[job_id].update({
            "status":   "done" if result.get("status") == "Success" else "error",
            "progress": 100,
            "message":  "Selesai!" if result.get("status") == "Success" else result.get("pesan"),
            "result":   result,
        })

    except Exception as e:
        jobs[job_id].update({
            "status":  "error",
            "message": str(e),
            "error":   str(e),
        })


# ─── STATUS POLLING ────────────────────────────────────────────
@app.get("/analyze/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    return job


# ─── SAVE SESSION ─────────────────────────────────────────────
@app.post("/api/instagram/save-session")
async def save_instagram_session(payload: dict):
    try:
        cookies = payload.get("cookies", [])
        IMPORTANT_COOKIES = ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid"]
        filtered = [c for c in cookies if c.get("name") in IMPORTANT_COOKIES]

        if not filtered:
            raise HTTPException(status_code=400, detail="Tidak ada cookies valid")

        cookies_json = json.dumps(filtered)
        os.environ["INSTAGRAM_COOKIES"] = cookies_json

        hf_token = os.environ.get("HF_TOKEN")
        if hf_token:
            try:
                from huggingface_hub import HfApi
                api = HfApi(token=hf_token)
                api.add_space_secret(
                    repo_id="adityaUHU/my-fastapi-analisis",
                    key="INSTAGRAM_COOKIES",
                    value=cookies_json
                )
                print("[HF SECRET] Tersimpan permanen!")
            except Exception as e:
                print(f"[HF SECRET] Gagal: {e}")

        from app.analyzer import reset_loader
        reset_loader()

        return {"success": True, "message": "Session saved", "total": len(filtered)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=7860, reload=False)