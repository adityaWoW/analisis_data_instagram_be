import pandas as pd
import json
import os
import io, re, time, random
import asyncio
import instaloader
from threading import Lock
from app.google_drive import get_spreadsheet_values, update_spreadsheet_values

IG_USERNAME = "Ace.Shuttle"
_loader_instance = None
# ─── LOAD INSTAGRAM COOKIES ───────────────────────────────────

def load_instagram_cookies():
    cookies_env = os.environ.get("INSTAGRAM_COOKIES")
    if cookies_env:
        try:
            cookies = json.loads(cookies_env)
            cookie_map = {c["name"]: c["value"] for c in cookies}
            if cookie_map.get("sessionid"):
                print(f"✅ Cookies dari env var | sessionid: {cookie_map['sessionid'][:10]}...")
                return cookie_map
        except Exception as e:
            print(f"⚠️ Gagal parse env var: {e}")

    try:
        with open("storage/cookies.json", "r", encoding="utf-8") as f:
            cookies = json.load(f)
        cookie_map = {c["name"]: c["value"] for c in cookies}
        if cookie_map.get("sessionid"):
            print("✅ Cookies dari file lokal")
            return cookie_map
    except FileNotFoundError:
        pass

    print("❌ Tidak ada cookies tersedia!")
    return {}

def reset_loader():
    global _loader_instance
    _loader_instance = None
    print("[LOADER] Instance direset")

def get_loader() -> instaloader.Instaloader:
    global _loader_instance
    if _loader_instance is not None:
        return _loader_instance

    ig_cookies = load_instagram_cookies()

    if not ig_cookies.get("sessionid"):
        print("⚠️ sessionid tidak ditemukan!")

    L = instaloader.Instaloader(
        quiet=True,
        request_timeout=30,      # tetap 30 untuk GraphQL fallback
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        compress_json=False,
    )

    # Batasi retry jadi 1x saja (bukan 3x default)
    L.context.max_connection_attempts = 1

    L.context._session.cookies.update({
        "sessionid":  ig_cookies.get("sessionid", ""),
        "csrftoken":  ig_cookies.get("csrftoken", ""),
        "ds_user_id": ig_cookies.get("ds_user_id", ""),
        "ig_did":     ig_cookies.get("ig_did", ""),
        "mid":        ig_cookies.get("mid", ""),
    })

    L.context._session.headers.update({
        "x-csrftoken":      ig_cookies.get("csrftoken", ""),
        "x-ig-app-id":      "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "referer":          "https://www.instagram.com/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "accept":          "*/*",
        "accept-language": "en-US,en;q=0.9,id;q=0.8",
        "origin":          "https://www.instagram.com",
    })
    L.context.username = IG_USERNAME

    try:
        L.context.graphql_query("d6f4427fbe92d846298cf93df0b937d3", {})
        print(f"✅ Session aktif — login sebagai: {IG_USERNAME}")
    except Exception as e:
        print(f"⚠️ Verifikasi session gagal ({e}). Melanjutkan...")

    _loader_instance = L
    return L

# ─── HELPER ───────────────────────────────────────────────────

def extract_shortcode(url: str) -> str | None:
    url = url.strip().rstrip('/')
    match = re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
    return match.group(1) if match else None


def fetch_fresh_post(shortcode: str, loader: instaloader.Instaloader):
    for url in [
        f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis",
        f"https://www.instagram.com/reel/{shortcode}/?__a=1&__d=dis",
    ]:
        try:
            response = loader.context._session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if items:
                    item = items[0]
                    class MockPost:
                        is_video       = item.get("media_type", 1) in (1, 2)
                        _full_metadata = item
                        likes          = item.get("like_count", 0)
                        comments       = item.get("comment_count", 0)
                    return MockPost()
        except Exception:
            pass

    # Fallback GraphQL
    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        post._full_metadata_dict = None
        return post
    except Exception as e:
        print(f"  [FAIL] {shortcode}: {type(e).__name__}")

    class EmptyPost:
        is_video       = True
        _full_metadata = {}
        likes          = 0
        comments       = 0
    return EmptyPost()


def get_views_from_post(post) -> tuple[int, int, bool]:
    raw       = getattr(post, "_full_metadata", {}) or {}
    shortcode = raw.get('code', 'unknown')

    if not raw.get('is_video', True) and raw.get('__typename') != 'GraphVideo':
        likes_count = raw.get('edge_media_preview_like', {}).get('count', 0) or getattr(post, "likes", 0) or 0
        return likes_count, likes_count, False

    total_views = raw.get('video_play_count', 0)
    if total_views == 0:
        for key in ("play_count", "ig_play_count"):
            if isinstance(raw.get(key), (int, float)) and raw.get(key) > 0:
                total_views = int(raw.get(key))
                break
    if total_views == 0:
        total_views = raw.get("edge_media_to_media_video_view", {}).get("count", 0)
    if total_views == 0:
        return 0, 0, False

    views_organik = raw.get('video_view_count', 0)

    is_boosted  = False
    boost_flags = ["is_ad", "is_boosted_post", "is_commercial", "is_paid_partnership"]
    debug_logs  = []
    for flag in boost_flags:
        val = raw.get(flag)
        debug_logs.append(f"{flag}: {val} ({type(val).__name__})")
        if val is True:
            is_boosted = True
    print(f"    [DEBUG-FLAGS] {shortcode} -> {' | '.join(debug_logs)}")

    if is_boosted:
        if 0 < views_organik < total_views:
            pass
        else:
            views_organik = int(total_views * 0.40)
    else:
        views_organik = total_views

    if views_organik > total_views:
        views_organik = total_views
    if views_organik <= 0:
        views_organik = total_views

    return total_views, views_organik, is_boosted


# ─── FETCH SINGLE (SINKRON SAMA PERSIS SEPERTI KODE ANDA) ──────

def fetch_single(args):
    index, url, loader, print_lock, delay_range = args

    shortcode = extract_shortcode(url)
    if not shortcode:
        return index, 0, 0, "invalid_url", False

    time.sleep(random.uniform(*delay_range))

    for attempt in range(2):  # maksimal 2 kali percobaan
        try:
            post = fetch_fresh_post(shortcode, loader)
            total_views, views_organik, is_boosted = get_views_from_post(post)
            
            if total_views > 0:  # berhasil dapat data
                with print_lock:
                    boost_status = "[BOOSTED]" if is_boosted else "[ORGANIC]"
                    print(f"  ✓ {shortcode}: Total:{total_views:,} | Ori:{views_organik:,} {boost_status}")
                return index, total_views, views_organik, "ok", is_boosted
            
            if attempt == 0:
                # Gagal dapat data, tunggu lebih lama lalu retry
                with print_lock:
                    print(f"  [RETRY] {shortcode} dapat 0, tunggu 15s lalu retry...")
                time.sleep(15)

        except Exception as e:
            with print_lock:
                print(f"  ✗ {shortcode} Error attempt {attempt+1}: {type(e).__name__}")
            if attempt == 0:
                time.sleep(15)

    with print_lock:
        print(f"  ✗ {shortcode}: gagal setelah 2 percobaan")
    return index, 0, 0, "error", False


# ─── BULK PARALLEL (DIUBAH MENJADI OPERASI ASYNC DENGAN SEMAPHORE) ───

async def worker_async(args, semaphore):
    """Worker untuk membungkus fungsi fetch_single agar berjalan secara non-blocking"""
    async with semaphore:
        # Menjalankan fungsi blocking (fetch_single) ke thread pool internal asyncio
        return await asyncio.to_thread(fetch_single, args)


async def bulk_fetch_views_async(
    url_index_pairs: list[tuple[int, str]],
    loader: instaloader.Instaloader,
    max_concurrent_tasks: int = 3,
    delay_range: tuple = (5.0, 10.0),
) -> dict[int, tuple[int, int, bool, str]]:
    
    results    = {}
    print_lock = Lock()
    
    # Membatasi task konkurensi yang berjalan simultan agar aman dari ban Instagram
    semaphore  = asyncio.Semaphore(max_concurrent_tasks)
    
    args_list  = [
        (idx, url, loader, print_lock, delay_range)
        for idx, url in url_index_pairs
    ]
    total = len(args_list)
    print(f"\n[ASYNC] Bulk fetch: {total} URL | {max_concurrent_tasks} Slot Simultan | delay {delay_range[0]}–{delay_range[1]}s")
    print("─" * 55)
    
    # Membuat list task asinkron
    tasks = [worker_async(args, semaphore) for args in args_list]
    
    done = 0
    # Mengeksekusi task dan membaca hasilnya segera setelah ada yang selesai (as_completed)
    for future in asyncio.as_completed(tasks):
        try:
            index, total_views, views_organik, status, is_boosted = await future
            results[index] = (total_views, views_organik, is_boosted, status)
        except Exception as e:
            # Fallback handling jika ada kegagalan fatal pada proses internal task
            print(f"  ✗ Task crash: {e}")
            
        done += 1
        print(f"  [{done}/{total}] selesai diproses")
        
    print("─" * 55)
    return results


def _safe_int_val(val) -> int:
    try:
        if val is None:
            return 0
        val_str = str(val).strip()
        if val_str == '' or val_str.lower() == 'nan':
            return 0
        return int(float(val_str.replace(',', '')))
    except (ValueError, TypeError):
        return 0

# ─── MAIN ─────────────────────────────────────────────────────

def analyze_excel(spreadsheet_id: str, sheet_name: str, max_workers: int = 1, delay_range: tuple = (4.0, 7.0)):
    try:
        # ── STEP 1: Ambil semua data dari sheet menggunakan Sheets API ──
        range_to_fetch = f"'{sheet_name}'!A1:ZZ"
        raw_rows = get_spreadsheet_values(spreadsheet_id, range_to_fetch)
        
        if not raw_rows:
            return {"status": "error", "pesan": f"Sheet '{sheet_name}' kosong atau tidak bisa dibaca."}
            
        # ── STEP 2: Konversi ke Pandas DataFrame ──
        header = raw_rows[0]
        data_rows = raw_rows[1:]
        
        max_cols = len(header)
        padded_rows = [row + [''] * (max_cols - len(row)) for row in data_rows]
        
        df = pd.DataFrame(padded_rows, columns=header)
        
        # ── STEP 3: Deteksi posisi kolom ──
        col_link = next((c for c in df.columns if 'link post' in str(c).lower()), None)
        col_imp = next((c for c in df.columns if 'total imp by job(play count)' in str(c).lower()), None)
        col_nama = next((c for c in df.columns if 'kol name' in str(c).lower() or str(c).lower() == 'name'), None)
        col_boost_excel = next((c for c in df.columns if 'boost' in str(c).lower()), None)
        
        col_imp_ori_name = "TOTAL IMP ORGANIK(VIEW COUNT)"
        col_status_name = "STATUS(JOB)"
        
        if not col_link:
            return {"status": "error", "pesan": "Kolom 'link post' tidak ditemukan."}
            
        def get_col_letter(index_0_based):
            result = ""
            idx = index_0_based + 1
            while idx > 0:
                idx, remainder = divmod(idx - 1, 26)
                result = chr(65 + remainder) + result
            return result

        col_letter_imp = get_col_letter(df.columns.get_loc(col_imp)) if col_imp else None
        
        if col_imp_ori_name in df.columns:
            col_letter_imp_ori = get_col_letter(df.columns.get_loc(col_imp_ori_name))
        else:
            header.append(col_imp_ori_name)
            col_letter_imp_ori = get_col_letter(len(header) - 1)
            update_spreadsheet_values(spreadsheet_id, f"'{sheet_name}'!{col_letter_imp_ori}1", [[col_imp_ori_name]])
            df[col_imp_ori_name] = 0
            
        if col_status_name in df.columns:
            col_letter_status = get_col_letter(df.columns.get_loc(col_status_name))
        else:
            header.append(col_status_name)
            col_letter_status = get_col_letter(len(header) - 1)
            update_spreadsheet_values(spreadsheet_id, f"'{sheet_name}'!{col_letter_status}1", [[col_status_name]])
            df[col_status_name] = "[ORGANIC]"

        df[col_imp_ori_name] = 0
        df[col_status_name] = "[ORGANIC]"

        # ── STEP 4: Fetch data dari Instagram (DIUBAH MENJADI CALL ASYNC RUN) ──
        url_index_pairs = [
            (idx, str(row[col_link]).strip())
            for idx, row in df.iterrows()
            if pd.notna(row[col_link]) and "instagram.com" in str(row[col_link])
        ]
        
        if url_index_pairs:
            loader = get_loader()
            
            # Memanggil fungsi Event Loop Asyncio untuk memproses penjemputan data secara asinkron
            results = asyncio.run(bulk_fetch_views_async(
                url_index_pairs, 
                loader, 
                max_concurrent_tasks=max_workers, 
                delay_range=delay_range
            ))
            
            for idx, (total_views, views_organik, is_boosted_api, status) in results.items():
                if col_imp:
                    df.at[idx, col_imp] = total_views
                
                is_boosted = is_boosted_api
                if col_boost_excel and col_boost_excel in df.columns:
                    val_excel = str(df.at[idx, col_boost_excel]).strip().lower()
                    if val_excel in ['yes', 'y', 'true', 'boosting', '1']:
                        is_boosted = True
                        
                if not is_boosted:
                    df.at[idx, col_imp_ori_name] = total_views
                    df.at[idx, col_status_name] = "[ORGANIC]"
                else:
                    df.at[idx, col_imp_ori_name] = views_organik
                    df.at[idx, col_status_name] = "[BOOSTED]"
        else:
            print("⚠️ Tidak ada URL Instagram valid ditemukan.")

        # ── STEP 5: Surgical Write Via Sheets API ──
        bulk_data_to_update = []
        updated_count = 0

        for idx, row in df.iterrows():
            raw_url = str(row.get(col_link, "")).strip()
            if "instagram.com" not in raw_url:
                continue  
                
            gs_row = idx + 2  
            
            if col_letter_imp:
                val_imp = _safe_int_val(row.get(col_imp))
                bulk_data_to_update.append({
                    'range': f"'{sheet_name}'!{col_letter_imp}{gs_row}",
                    'values': [[val_imp]]
                })
                
            val_imp_ori = _safe_int_val(row.get(col_imp_ori_name))
            bulk_data_to_update.append({
                'range': f"'{sheet_name}'!{col_letter_imp_ori}{gs_row}",
                'values': [[val_imp_ori]]
            })
            
            val_status = str(row.get(col_status_name, "[ORGANIC]")).strip()
            if val_status == '' or val_status.lower() == 'nan':
                val_status = "[ORGANIC]"
                
            bulk_data_to_update.append({
                'range': f"'{sheet_name}'!{col_letter_status}{gs_row}",
                'values': [[val_status]]
            })
            
            updated_count += 1
            
        # ── EXECUTE BULK WRITE ──
        if bulk_data_to_update:
            print(f"[API] Mengirimkan {len(bulk_data_to_update)} data cell sekaligus ke Google Sheets...")
            update_spreadsheet_values(spreadsheet_id, bulk_data_to_update)
            print(f"[WRITE] Sukses menulis {updated_count} baris data ke Google Sheet tanpa terkena Rate Limit!")
        else:
            print("⚠️ Tidak ada data baru yang perlu diupdate ke Google Sheets.")

        return {
            "status": "Success",
            "total_baris": len(df),
            "KOL NAME": df[col_nama].fillna("").astype(str).tolist() if col_nama else [],
            "TOTAL IMP": [_safe_int_val(x) for x in df[col_imp].tolist()] if col_imp else [],
            "TOTAL IMP original": [_safe_int_val(x) for x in df[col_imp_ori_name].tolist()],
            "STATUS": df[col_status_name].fillna("[ORGANIC]").astype(str).tolist(),
        }

    except Exception as e:
        print(f"❌ Gagal memproses sheet via API: {type(e).__name__}: {e}")
        return {"status": "error", "pesan": f"Error tidak terduga: {str(e)}"}
    
def analyze_excel_batched(
    spreadsheet_id: str,
    sheet_name: str,
    batch_size: int = 100,
    max_workers: int = 3,
    delay_range: tuple = (3.0, 6.0),
    on_progress=None,
):
    try:
        # STEP 1: Ambil data
        if on_progress: on_progress(0, 1, 0, 1, "Membaca data dari Google Sheets...")
        raw_rows = get_spreadsheet_values(spreadsheet_id, f"'{sheet_name}'!A1:ZZ")
        if not raw_rows:
            return {"status": "error", "pesan": f"Sheet '{sheet_name}' kosong."}

        header    = raw_rows[0]
        data_rows = raw_rows[1:]
        max_cols  = len(header)
        padded    = [r + [''] * (max_cols - len(r)) for r in data_rows]
        df        = pd.DataFrame(padded, columns=header)

        # STEP 2: Deteksi kolom
        col_link        = next((c for c in df.columns if 'link post' in str(c).lower()), None)
        col_imp         = next((c for c in df.columns if 'total imp by job(play count)' in str(c).lower()), None)
        col_nama        = next((c for c in df.columns if 'kol name' in str(c).lower() or str(c).lower() == 'name'), None)
        col_boost_excel = next((c for c in df.columns if 'boost' in str(c).lower()), None)
        col_imp_ori_name = "TOTAL IMP ORGANIK(VIEW COUNT)"
        col_status_name  = "STATUS(JOB)"

        if not col_link:
            return {"status": "error", "pesan": "Kolom 'link post' tidak ditemukan."}

        def get_col_letter(i):
            r, idx = "", i + 1
            while idx > 0:
                idx, rem = divmod(idx - 1, 26)
                r = chr(65 + rem) + r
            return r

        col_letter_imp = get_col_letter(df.columns.get_loc(col_imp)) if col_imp else None

        if col_imp_ori_name not in df.columns:
            header.append(col_imp_ori_name)
            update_spreadsheet_values(spreadsheet_id, [[col_imp_ori_name]], f"'{sheet_name}'!{get_col_letter(len(header)-1)}1")
            df[col_imp_ori_name] = 0
        col_letter_imp_ori = get_col_letter(df.columns.get_loc(col_imp_ori_name))

        if col_status_name not in df.columns:
            header.append(col_status_name)
            update_spreadsheet_values(spreadsheet_id, [[col_status_name]], f"'{sheet_name}'!{get_col_letter(len(header)-1)}1")
            df[col_status_name] = "[ORGANIC]"
        col_letter_status = get_col_letter(df.columns.get_loc(col_status_name))

        df[col_imp_ori_name] = 0
        df[col_status_name]  = "[ORGANIC]"

        # STEP 3: Kumpulkan URL valid
        url_index_pairs = [
            (idx, str(row[col_link]).strip())
            for idx, row in df.iterrows()
            if pd.notna(row[col_link]) and "instagram.com" in str(row[col_link])
        ]
        total_url = len(url_index_pairs)

        if not url_index_pairs:
            return {"status": "error", "pesan": "Tidak ada URL Instagram valid."}

        # STEP 4: Proses per batch 100
        loader = get_loader()
        batches = [url_index_pairs[i:i+batch_size] for i in range(0, total_url, batch_size)]
        total_batch = len(batches)
        done_count  = 0

        print(f"[BATCH] Total {total_url} URL → {total_batch} batch × {batch_size}")

        for b_idx, batch in enumerate(batches):
            if on_progress:
                on_progress(done_count, total_url, b_idx, total_batch,
                            f"Batch {b_idx+1}/{total_batch} — {done_count}/{total_url} URL selesai")

            print(f"\n[BATCH {b_idx+1}/{total_batch}] Memproses {len(batch)} URL...")

            results = asyncio.run(bulk_fetch_views_async(
                batch, loader,
                max_concurrent_tasks=max_workers,
                delay_range=delay_range,
            ))

            # Update DataFrame
            for idx, (total_views, views_organik, is_boosted_api, status) in results.items():
                if col_imp:
                    df.at[idx, col_imp] = total_views

                is_boosted = is_boosted_api
                if col_boost_excel and col_boost_excel in df.columns:
                    val = str(df.at[idx, col_boost_excel]).strip().lower()
                    if val in ['yes', 'y', 'true', 'boosting', '1']:
                        is_boosted = True

                df.at[idx, col_imp_ori_name] = views_organik if is_boosted else total_views
                df.at[idx, col_status_name]  = "[BOOSTED]" if is_boosted else "[ORGANIC]"

            # Tulis batch ini langsung ke Sheets (tidak tunggu semua selesai)
            bulk = []
            for idx, _ in batch:
                gs_row = idx + 2
                row    = df.loc[idx]
                if col_letter_imp:
                    bulk.append({'range': f"'{sheet_name}'!{col_letter_imp}{gs_row}", 'values': [[_safe_int_val(row.get(col_imp))]]})
                bulk.append({'range': f"'{sheet_name}'!{col_letter_imp_ori}{gs_row}", 'values': [[_safe_int_val(row.get(col_imp_ori_name))]]})
                val_s = str(row.get(col_status_name, "[ORGANIC]")).strip()
                bulk.append({'range': f"'{sheet_name}'!{col_letter_status}{gs_row}", 'values': [[val_s if val_s and val_s.lower() != 'nan' else "[ORGANIC]"]]})

            if bulk:
                update_spreadsheet_values(spreadsheet_id, bulk)
                print(f"[BATCH {b_idx+1}] ✅ Tulis {len(batch)} baris ke Sheets")

            done_count += len(batch)

            # Jeda antar batch agar tidak trigger rate limit Instagram
            if b_idx < total_batch - 1:
                jeda = random.uniform(10, 20)
                print(f"[BATCH] Jeda {jeda:.0f}s sebelum batch berikutnya...")
                if on_progress:
                    on_progress(done_count, total_url, b_idx+1, total_batch,
                                f"Jeda sebentar sebelum batch {b_idx+2}...")
                time.sleep(jeda)

        if on_progress:
            on_progress(total_url, total_url, total_batch, total_batch, "Semua selesai!")

        return {
            "status":      "Success",
            "total_baris": len(df),
            "KOL NAME":    df[col_nama].fillna("").astype(str).tolist() if col_nama else [],
            "TOTAL IMP":   [_safe_int_val(x) for x in df[col_imp].tolist()] if col_imp else [],
            "TOTAL IMP original": [_safe_int_val(x) for x in df[col_imp_ori_name].tolist()],
            "STATUS":      df[col_status_name].fillna("[ORGANIC]").astype(str).tolist(),
        }

    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        return {"status": "error", "pesan": str(e)}