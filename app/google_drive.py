import io
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import openpyxl

COOKIES_FILE_NAME = "instagram_cookies.json"

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

# ─── BACA CREDENTIALS DARI ENV VAR (bukan dari file) ──────────
credentials_json = os.environ.get("GOOGLE_CREDENTIALS")

if credentials_json is None:
    raise ValueError("Environment variable GOOGLE_CREDENTIALS tidak ditemukan!")

credentials_info = json.loads(credentials_json)

credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=SCOPES
)
# ──────────────────────────────────────────────────────────────

drive_service  = build('drive',  'v3', credentials=credentials)
sheets_service = build('sheets', 'v4', credentials=credentials)

FOLDER_MIME = 'application/vnd.google-apps.folder'
SHEET_MIME  = 'application/vnd.google-apps.spreadsheet'
EXCEL_MIME  = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


# ─── FORMAT HELPER ────────────────────────────────────────────

def format_files(files):
    formatted = []
    for file in files:
        file_type = "file"
        if file["mimeType"] == FOLDER_MIME:
            file_type = "folder"
        elif file["mimeType"] in (SHEET_MIME, EXCEL_MIME):
            file_type = "sheet"

        formatted.append({
            "id":       file["id"],
            "name":     file["name"],
            "mimeType": file["mimeType"],
            "type":     file_type
        })
    return formatted


# ─── LIST FILES ───────────────────────────────────────────────

def list_files():
    results = drive_service.files().list(
        q="sharedWithMe = true and trashed=false",
        pageSize=100,
        fields="files(id, name, mimeType)"
    ).execute()
    return format_files(results.get('files', []))


def list_folder_files(folder_id: str):
    results = drive_service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        pageSize=100,
        fields="files(id, name, mimeType)"
    ).execute()
    return format_files(results.get('files', []))


# ─── DOWNLOAD ─────────────────────────────────────────────────

def download_file(file_id: str, mime_type: str) -> io.BytesIO:
    """
    Download file dari Google Drive ke BytesIO.
    Jika berupa Google Sheets native, diekspor ke format .xlsx terlebih dahulu.
    """
    if mime_type == SHEET_MIME:
        request = drive_service.files().export_media(
            fileId=file_id,
            mimeType=EXCEL_MIME
        )
    else:
        request = drive_service.files().get_media(fileId=file_id)

    file_stream = io.BytesIO()
    downloader  = MediaIoBaseDownload(file_stream, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_stream.seek(0)
    return file_stream


# ─── UPDATE FILE KE DRIVE ─────────────────────────────────────

def update_file_in_drive(file_id: str, updated_stream: io.BytesIO, mime_type: str) -> dict:
    """
    Upload ulang (overwrite) konten file yang sudah dimodifikasi ke Google Drive.

    Catatan penting:
    - Jika file aslinya adalah Google Sheets native (SHEET_MIME), Drive API akan
      mengkonversinya kembali ke format Google Sheets saat di-upload dengan
      parameter convert=True (melalui query param di URL). Namun pendekatan paling
      aman adalah upload sebagai .xlsx biasa — Google Drive akan otomatis update
      kontennya tanpa mengubah file_id.
    - Jika file aslinya sudah .xlsx (EXCEL_MIME), cukup replace binary-nya.

    Parameter:
        file_id        : ID file Google Drive yang akan di-overwrite
        updated_stream : BytesIO berisi file .xlsx hasil proses analyze
        mime_type      : mimeType file asli (SHEET_MIME atau EXCEL_MIME)

    Return:
        dict berisi metadata file yang sudah diupdate dari Drive API
    """
    updated_stream.seek(0)

    # Selalu upload dalam format .xlsx; Drive akan handle konversi jika perlu
    media = MediaIoBaseUpload(
        updated_stream,
        mimetype=EXCEL_MIME,
        resumable=True,
        chunksize=5 * 1024 * 1024,  # 5MB per chunk
    )

    # Gunakan files().update() — bukan files().create() — agar file_id tetap sama
    updated_file = drive_service.files().update(
        fileId=file_id,
        media_body=media,
        # Jangan kirim body metadata agar nama/folder tidak berubah
        fields="id, name, mimeType, modifiedTime"
    ).execute()

    print(
        f"[DRIVE UPDATE] File '{updated_file.get('name')}' berhasil diupdate. "
        f"ID: {updated_file.get('id')} | Modified: {updated_file.get('modifiedTime')}"
    )

    return updated_file


# ─── SHEETS LIST ──────────────────────────────────────────────

def get_spreadsheet_sheets(file_id: str) -> dict:
    try:
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=file_id,
            fields="sheets.properties.title"
        ).execute()

        sheet_names = [
            sheet['properties']['title']
            for sheet in spreadsheet.get('sheets', [])
        ]
        return {"success": True, "data": sheet_names}

    except Exception as sheets_err:
        print(
            f"Sheets API gagal, mencoba fallback openpyxl untuk file {file_id}. "
            f"Detail: {sheets_err}"
        )
        try:
            file_stream = download_file(file_id, SHEET_MIME)

            if hasattr(file_stream, 'seek'):
                file_stream.seek(0)

            wb = openpyxl.load_workbook(file_stream, read_only=True)
            sheet_names = wb.sheetnames
            wb.close()

            if hasattr(file_stream, 'seek'):
                file_stream.seek(0)

            return {"success": True, "data": sheet_names, "stream": file_stream}

        except Exception as final_err:
            print(f"Gagal total membaca sheet untuk ID {file_id}: {final_err}")
            raise Exception(
                f"Tidak dapat membaca daftar sheet. Pastikan file berupa Spreadsheet/Excel "
                f"dan akun Service Account sudah diberi akses (Share). Detail: {str(final_err)}"
            )
        

def update_spreadsheet_values(spreadsheet_id: str, data_updates: list):
    """
    Mengupdate banyak cell di koordinat yang berbeda sekaligus dalam 1 kali request API.
    data_updates format: [
        {'range': "'Sheet1'!A2", 'values': [[val]]},
        {'range': "'Sheet1'!B5", 'values': [[val]]},
    ]
    """
    if not data_updates:
        return None

    body = {
        'valueInputOption': 'USER_ENTERED',
        'data': data_updates
    }
    
    response = sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body=body
    ).execute()
    
    return response

def get_spreadsheet_values(spreadsheet_id: str, range_name: str) -> list:
    """
    Mengambil data dari Google Sheets secara langsung tanpa download file
    """
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name
    ).execute()
    return result.get('values', [])     



def save_cookies_to_drive(cookies: list) -> None:
    """Simpan cookies Instagram ke Google Drive secara permanen"""
    content = json.dumps(cookies, indent=2).encode("utf-8")
    stream = io.BytesIO(content)

    # Cek apakah file sudah ada
    results = drive_service.files().list(
        q=f"name='{COOKIES_FILE_NAME}' and trashed=false",
        fields="files(id, name)"
    ).execute()
    existing = results.get("files", [])

    media = MediaIoBaseUpload(stream, mimetype="application/json", resumable=False)

    if existing:
        drive_service.files().update(
            fileId=existing[0]["id"],
            media_body=media
        ).execute()
        print(f"[DRIVE] Cookies diupdate. ID: {existing[0]['id']}")
    else:
        file = drive_service.files().create(
            body={"name": COOKIES_FILE_NAME},
            media_body=media,
            fields="id"
        ).execute()
        print(f"[DRIVE] Cookies disimpan baru. ID: {file.get('id')}")


def load_cookies_from_drive() -> list:
    """Baca cookies Instagram dari Google Drive"""
    results = drive_service.files().list(
        q=f"name='{COOKIES_FILE_NAME}' and trashed=false",
        fields="files(id)"
    ).execute()

    files = results.get("files", [])
    if not files:
        print("[DRIVE] File cookies tidak ditemukan di Drive")
        return []

    stream = download_file(files[0]["id"], "application/json")
    cookies = json.loads(stream.read().decode("utf-8"))
    print(f"[DRIVE] Cookies loaded dari Drive: {len(cookies)} cookies")
    return cookies   