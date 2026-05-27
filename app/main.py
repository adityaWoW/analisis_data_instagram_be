from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
import httpx

from app.google_drive import (
    list_files,
    list_folder_files,
    get_spreadsheet_sheets,
)
from app.analyzer import analyze_excel
from app.model import AnalyzeRequest

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://analisis-data-instagram-fe.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    os.makedirs("storage", exist_ok=True)
    print("[STARTUP] Folder 'storage' siap digunakan.")


@app.get("/files")
def get_files():
    files = list_files()
    return {"success": True, "data": files}


@app.get("/files/{folder_id}")
def get_folder_files(folder_id: str):
    try:
        files = list_folder_files(folder_id)
        return {"success": True, "data": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/{file_id}/sheets")
def get_sheets(file_id: str):
    try:
        result = get_spreadsheet_sheets(file_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    try:
        print(f"[ANALYZE] ID: {request.file_id}, Sheet: {request.sheet_name}")

        # Validasi cookies tersedia (dari env var atau file lokal)
        has_cookies = (
            bool(os.environ.get("INSTAGRAM_COOKIES")) or
            os.path.exists("storage/cookies.json")
        )
        if not has_cookies:
            raise HTTPException(
                status_code=400,
                detail="Sesi Instagram belum tersimpan. Silakan simpan sesi Instagram terlebih dahulu."
            )

        result = analyze_excel(
            spreadsheet_id=request.file_id,
            sheet_name=request.sheet_name
        )

        if result.get("status") != "Success":
            raise HTTPException(
                status_code=422,
                detail=result.get("pesan", "Analyze gagal tanpa pesan error.")
            )

        return {
            "success": True,
            "data": {
                **result,
                "drive_update": {
                    "file_id": request.file_id,
                    "sheet_name": request.sheet_name,
                    "message": "Surgical update cells success via Google Sheets API"
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ANALYZE ERROR] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/instagram/save-session")
async def save_instagram_session(payload: dict):
    try:
        cookies = payload.get("cookies", [])
        IMPORTANT_COOKIES = ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid"]
        filtered = [c for c in cookies if c.get("name") in IMPORTANT_COOKIES]

        if not filtered:
            raise HTTPException(status_code=400, detail="Tidak ada cookies valid")

        cookies_json = json.dumps(filtered)

        # Simpan ke memory
        os.environ["INSTAGRAM_COOKIES"] = cookies_json

        # Simpan permanen ke HF Secrets
        hf_token = os.environ.get("HF_TOKEN")
        if hf_token:
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    "https://huggingface.co/api/spaces/adityaUHU/my-fastapi-analisis/secrets",
                    headers={"Authorization": f"Bearer {hf_token}"},
                    json={"key": "INSTAGRAM_COOKIES", "value": cookies_json}
                )
                if resp.status_code == 200:
                    print("[HF SECRET] Cookies berhasil disimpan ke HF Secrets")
                else:
                    print(f"[HF SECRET] Gagal: {resp.status_code} {resp.text}")
        else:
            print("[HF SECRET] HF_TOKEN tidak ditemukan, cookies hanya di memory!")

        # Reset loader agar pakai cookies baru
        from app import analyzer
        analyzer._loader_instance = None

        return {"success": True, "message": "Session saved", "total": len(filtered)}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SAVE SESSION ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=7860,
        reload=False,
        log_level="info",
    )