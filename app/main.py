from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import json
import os
from app.google_drive import (
    list_files,
    list_folder_files,
    download_file,
    get_spreadsheet_sheets,
    update_file_in_drive,   # ← tambahan
    SHEET_MIME,
    EXCEL_MIME,
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


# ─── ROOT FILES ───────────────────────────────────────────────

@app.get("/files")
def get_files():
    files = list_files()
    return {"success": True, "data": files}


# ─── OPEN FOLDER ──────────────────────────────────────────────

@app.get("/files/{folder_id}")
def get_folder_files(folder_id: str):
    try:
        files = list_folder_files(folder_id)
        return {"success": True, "data": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── LIST SHEETS ──────────────────────────────────────────────

@app.get("/files/{file_id}/sheets")
def get_sheets(file_id: str):
    try:
        result = get_spreadsheet_sheets(file_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── ANALYZE + UPDATE LANGSUNG KE DRIVE ───────────────────────

@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    """
    Alur Baru (Surgical Update):
      1. Jalankan analyze_sheets_api -> membaca, memproses, dan mengupdate 
         langsung cell tertentu via Google Sheets API.
      2. Dropdown, formatting, formulas, warna aman 100% karena file tidak di-download/upload.
    """
    try:
        print(f"[ANALYZE] Memproses secara langsung via Sheets API. ID: {request.file_id}, Sheet: {request.sheet_name}")
        
        # Panggil fungsi analyzer baru kita yang menggunakan Sheets API
        result = analyze_excel(
            spreadsheet_id=request.file_id,
            sheet_name=request.sheet_name
        )

        if result.get("status") != "Success":
            raise HTTPException(
                status_code=422,
                detail=result.get("pesan", "analyze_sheets_api gagal tanpa pesan error.")
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

# ─── SAVE INSTAGRAM SESSION ─────────────────────────────

@app.post("/api/instagram/save-session")
async def save_instagram_session(payload: dict):

    try:

        cookies = payload.get("cookies", [])

        IMPORTANT_COOKIES = [
            "sessionid",
            "csrftoken",
            "ds_user_id",
            "ig_did",
            "mid",
        ]

        filtered = [
            cookie
            for cookie in cookies
            if cookie.get("name") in IMPORTANT_COOKIES
        ]

        os.makedirs("storage", exist_ok=True)

        with open(
            "storage/cookies.json",
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                filtered,
                f,
                indent=2,
                ensure_ascii=False
            )

        print("[INSTAGRAM] Session saved")

        return {
            "success": True,
            "message": "Instagram session saved",
            "total": len(filtered)
        }

    except Exception as e:

        print(f"[SAVE SESSION ERROR] {e}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # hardcode langsung
        port=7860,        # hardcode langsung
        reload=True,
        log_level="info",
    )