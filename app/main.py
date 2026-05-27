from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
from app import analyzer

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

# ─── LIFECYCLE / STARTUP EVENT ───────────────────────────────
# Memastikan folder storage selalu siap saat container menyala
@app.on_event("startup")
def startup_event():
    os.makedirs("storage", exist_ok=True)
    print("[STARTUP] Folder 'storage' siap digunakan.")


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
    try:
        print(f"[ANALYZE] Memproses secara langsung via Sheets API. ID: {request.file_id}, Sheet: {request.sheet_name}")
        
        # Validasi tambahan sebelum dilempar ke analyzer
        if not os.path.exists("storage/cookies.json"):
            print("[⚠️ WARNING] cookies.json belum ada saat /analyze dipanggil!")
            # Opsional: Anda bisa mengembalikan error ke FE agar user tahu harus login IG dulu
            raise HTTPException(
                status_code=400, 
                detail="Sesi Instagram belum tersimpan. Silakan simpan sesi Instagram terlebih dahulu di frontend."
            )

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
        IMPORTANT_COOKIES = ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid"]
        filtered = [c for c in cookies if c.get("name") in IMPORTANT_COOKIES]

        os.environ["INSTAGRAM_COOKIES"] = json.dumps(filtered)

        # ← RESET loader agar rebuild dengan cookies baru
        analyzer._loader_instance = None
        print(f"[SESSION] Cookies baru disimpan, loader direset. Total: {len(filtered)} cookies")

        os.makedirs("storage", exist_ok=True)
        with open("storage/cookies.json", "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2, ensure_ascii=False)

        return {"success": True, "message": "Session saved & loader reset", "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=7860,
        reload=False,
        log_level="info",
    )