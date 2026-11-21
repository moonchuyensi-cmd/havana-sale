import os
import re
import sys
import glob
import json
import shutil
import datetime
import hashlib
import concurrent.futures
import subprocess
import unicodedata
import urllib.parse
import urllib.request
import zipfile as zf_mod

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v22 - AUTO CAP NHAT TON KHO LIEN TUC 5 PHUT + PARALLEL SAPO + LIVE RELEASE FEED)
# ==============================================================================

DEFAULT_COOKIE = ""
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
SAPO_IMG_RX = re.compile(r"(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)", re.I)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".jfif", ".avif", ".gif", ".bmp")
VID_EXTS = (".mp4", ".mov", ".webm", ".m4v")
SKIP_DIRS = {".git", ".github", "functions", "__pycache__", "node_modules"}
PK_KNOWN_SUBTYPES = {
    "PK822": "Mũ", "PK887": "Túi xách", "PK893": "Mũ", "PK894": "Mũ",
    "PK897": "Phụ kiện", "PK898": "Túi xách", "PK976": "Túi xách", "PK995": "Túi xách",
    "PK997": "Túi xách", "PK999": "Túi xách", "PK1006": "Giày", "PK1027": "Túi xách", "PK1032": "Giày",
}


def vn_now_str() -> str:
    tz_vn = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(tz_vn).strftime("%H:%M %d/%m")


def norm_nfc(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip()


def strip_accents(s: str) -> str:
    s = str(s or "").replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").upper().strip()


def clean_base_code(val) -> str:
    raw = str(val or "").strip().upper().rstrip("-")
    m = SKU_REGEX.match(raw)
    return m.group(1).upper() if m else ""


def is_sapo_cdn_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u or u.startswith(("./", "images/", "videos/", "sizes/", "data:", "blob:")):
        return False
    if "raw.githubusercontent.com/moonchuyensi-cmd/havana-sale" in u:
        return False
    return bool(SAPO_IMG_RX.search(u) or u.startswith(("http://", "https://")))


def normalize_sapo_img_url(raw_url) -> str:
    u = str(raw_url or "").strip()
    if not u or u.startswith("data:"):
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = f"https://{DOMAIN}{u}"
    return u if u.startswith(("http://", "https://")) else ""


def extract_sapo_product_images(sapo_prod: dict) -> list:
    urls = []

    def add_u(val):
        nu = normalize_sapo_img_url(val)
        if nu and nu not in urls:
            urls.append(nu)

    img_main = sapo_prod.get("image")
    if isinstance(img_main, dict):
        add_u(img_main.get("full_path") or img_main.get("src") or img_main.get("path"))
    elif isinstance(img_main, str):
        add_u(img_main)

    for im in (sapo_prod.get("images") or []):
        if isinstance(im, dict):
            add_u(im.get("full_path") or im.get("src") or im.get("path"))
        elif isinstance(im, str):
            add_u(im)

    for v in (sapo_prod.get("variants") or []):
        for vim in (v.get("images") or []):
            if isinstance(vim, dict):
                add_u(vim.get("full_path") or vim.get("src") or vim.get("path"))
            elif isinstance(vim, str):
                add_u(vim)
        v_single = v.get("image")
        if isinstance(v_single, dict):
            add_u(v_single.get("full_path") or v_single.get("src") or v_single.get("path"))
        elif isinstance(v_single, str):
            add_u(v_single)

    return urls


def encode_rel_url(rel_path: str) -> str:
    clean = rel_path.replace("\\", "/").lstrip("./")
    parts = [urllib.parse.quote(p) for p in clean.split("/")]
    return "./" + "/".join(parts)


def split_custom_and_sapo_media(prod: dict) -> tuple:
    raw_imgs = prod.get("images") if isinstance(prod.get("images"), list) else []
    main_img = str(prod.get("image") or "").strip()
    raw_sapo = prod.get("sapoImages") if isinstance(prod.get("sapoImages"), list) else []

    custom_imgs = []
    sapo_imgs = []

    for u in ([main_img] + list(raw_imgs) + list(raw_sapo)):
        s = str(u or "").strip()
        if not s or s.startswith("data:image/svg+xml"):
            continue
        if is_sapo_cdn_url(s):
            if s not in sapo_imgs:
                sapo_imgs.append(s)
        else:
            if s not in custom_imgs:
                custom_imgs.append(s)

    vid = str(prod.get("video") or "").strip()
    if is_sapo_cdn_url(vid):
        vid = ""
    sz_chart = str(prod.get("sizeChart") or "").strip()
    if is_sapo_cdn_url(sz_chart):
        sz_chart = ""

    return custom_imgs, sapo_imgs, vid, sz_chart


def parse_money(val) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        v = float(val)
        return int(round(v * 1000)) if 0 < v < 1000 else int(round(v))
    s = str(val).strip().lower()
    if not s or "quà" in s or "qua tang" in strip_accents(s).lower() or "#n/a" in s:
        return -1 if "quà" in s else 0
    s_digits = re.sub(r"[^0-9]", "", s)
    if not s_digits:
        return 0
    num = int(s_digits)
    return num * 1000 if 0 < v_safe(num) < 1000 else num


def v_safe(n: int) -> int:
    return n


def parse_pct(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        v = float(val)
        return v * 100.0 if 0 < v <= 1.0 else v
    s = str(val).strip().replace("%", "").replace(",", ".")
    try:
        v = float(s)
        return v * 100.0 if 0 < v <= 1.0 else v
    except ValueError:
        return 0.0


# Danh sach ma can xoa sach anh cu bi up nham de lay lai anh goc tu Sapo
FORCE_RESET_TO_SAPO_SKUS = {"SAT532"}
PURGE_STATE_FILE = ".purged_wrong_media_v21.json"


def purge_wrong_custom_media_once():
    """Xoa triet de file anh/video cu bi nham cua SAT532 tren repo (chay 1 lan duy nhat cho moi ky reset
    de neu sau nay ban up anh moi dung cua SAT532 thi van nhan binh thuong)."""
    already_purged = set()
    if os.path.exists(PURGE_STATE_FILE):
        try:
            with open(PURGE_STATE_FILE, "r", encoding="utf-8") as pf:
                already_purged = set(json.load(pf) or [])
        except Exception:
            already_purged = set()

    pending = FORCE_RESET_TO_SAPO_SKUS - already_purged
    if not pending:
        return set()

    compiled = build_sku_matcher(list(pending))
    removed_files = []
    for folder in ("images", "videos", "sizes"):
        if not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for fname in files:
                if fname.lower() == "keep.txt":
                    continue
                fpath = os.path.join(root, fname)
                for code, rx in compiled:
                    if rx.search(fname) or rx.search(root):
                        try:
                            os.remove(fpath)
                            removed_files.append(fpath)
                        except Exception:
                            pass
                        break

    if removed_files:
        print(f"[+] Da xoa {len(removed_files)} file anh/video cu bi nham cua {sorted(pending)}: {removed_files}")

    try:
        with open(PURGE_STATE_FILE, "w", encoding="utf-8") as pf:
            json.dump(sorted(already_purged | pending), pf, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return pending


def extract_smart_secret(raw_val: str) -> str:
    raw = str(raw_val or "").strip()
    if not raw:
        return ""
    m_b = re.search(r"(?:-b|--cookie)\s+(['\"])(.+?)\1", raw, flags=re.S)
    if m_b:
        raw = m_b.group(2)
    else:
        m_h = re.search(r"(?:-H|--header)\s+(['\"])cookie\s*:\s*(.+?)\1", raw, flags=re.I | re.S)
        if m_h:
            raw = m_h.group(2)
        else:
            m_line = re.search(r"(?:^|[\r\n])cookie\s*:\s*([^\r\n]+)", raw, flags=re.I)
            if m_line:
                raw = m_line.group(1)

    if "^" in raw and ("sapo-admin" in raw or "AspNetCore" in raw):
        raw = re.sub(r"\^([\"%&|<>^])", r"\1", raw)

    s = raw.strip().strip('"').strip("'")
    s = re.sub(r"[\r\n]+", " ", s).strip()
    for pfx in ("cookie:", "x-sapo-access-token:"):
        if s.lower().startswith(pfx):
            s = s[len(pfx):].strip()
    return "".join(ch for ch in s if 32 <= ord(ch) <= 126)


def make_cookie_headers(cookie_str: str) -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "cookie": cookie_str,
        "referer": f"https://{DOMAIN}/admin/products",
        "x-sapo-client": "sapo-frontend-v3",
        "x-sapo-serviceid": "sapo-frontend-v3",
    }


def make_token_headers(token_str: str) -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "X-Sapo-Access-Token": token_str,
    }


def build_header_candidates() -> list:
    candidates = []
    local_cookie = ""
    if os.path.exists(".sapo_cookie.txt"):
        try:
            with open(".sapo_cookie.txt", "r", encoding="utf-8") as cf:
                local_cookie = extract_smart_secret(cf.read())
        except Exception:
            pass

    env_cookie = extract_smart_secret(os.environ.get("SAPO_COOKIE", "") or DEFAULT_COOKIE)
    env_token = extract_smart_secret(os.environ.get("SAPO_ACCESS_TOKEN", ""))

    for label, c_val in [("SAPO_COOKIE", env_cookie), (".sapo_cookie.txt", local_cookie)]:
        if c_val:
            candidates.append((label, make_cookie_headers(c_val)))

    if env_token:
        if "sapo-admin=" in env_token or ".AspNetCore" in env_token or ";" in env_token:
            candidates.append(("SAPO_ACCESS_TOKEN (dang Cookie)", make_cookie_headers(env_token)))
        else:
            candidates.append(("SAPO_ACCESS_TOKEN", make_token_headers(env_token)))
            candidates.append(("SAPO_ACCESS_TOKEN (thu nhu Cookie)", make_cookie_headers(env_token)))

    return candidates


def write_github_summary(md_text: str):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as sf:
                sf.write(md_text + "\n")
        except Exception:
            pass


def load_black_friday_rules() -> dict:
    excel_files = [f for f in glob.glob("*.xlsx") if not os.path.basename(f).startswith("~$")]
    csv_files = glob.glob("*.csv")
    sale_rules = {}

    if excel_files:
        excel_files.sort(key=lambda x: (0 if "black" in x.lower() else 1, x))
        target_xlsx = excel_files[0]
        xlsx_sha = ""
        try:
            with open(target_xlsx, "rb") as xf:
                xlsx_sha = hashlib.sha256(xf.read()).hexdigest()[:16]
        except Exception:
            pass
        sha_marker = ".bf_xlsx_hash.txt"
        prev_sha = ""
        if os.path.exists(sha_marker):
            try:
                with open(sha_marker, "r", encoding="utf-8") as mf:
                    prev_sha = mf.read().strip()
            except Exception:
                pass
        if xlsx_sha and xlsx_sha == prev_sha and os.path.exists("sale_rules.json"):
            try:
                with open("sale_rules.json", "r", encoding="utf-8") as rf:
                    cached_rules = json.load(rf)
                if len(cached_rules) >= 50:
                    for ex_code in EXCLUDED_SKUS:
                        cached_rules.pop(ex_code, None)
                    return cached_rules
            except Exception:
                pass

        print(f"[*] Tu dong doc bang Excel tren repo: {target_xlsx}")
        try:
            import openpyxl
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
            import openpyxl

        wb = openpyxl.load_workbook(target_xlsx, data_only=True)
        ordered_sheets = sorted(
            wb.sheetnames,
            key=lambda n: 0 if ("TỔNG" in n.upper() or "TONG" in strip_accents(n) or "PHỤ" in n.upper() or "PHU" in strip_accents(n)) else 1
        )
        for sname in ordered_sheets:
            if "RÚT" in sname.upper() or "RUT" in strip_accents(sname):
                continue
            ws = wb[sname]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [strip_accents(c) for c in rows[0]]
            col_sku, col_orig, col_sale, col_pct, col_suggest, col_type = 0, None, None, None, None, None
            for idx, h in enumerate(header):
                if h in ("MA", "MA MOI") and col_sku == 0:
                    col_sku = idx
                elif "GIA BAN" in h:
                    col_orig = idx
                elif "DONG GIA" in h or "GIA GIAM" in h or "GIA SALE" in h:
                    col_sale = idx
                elif "GIAM GIA" in h or "%" in h:
                    col_pct = idx
                elif "DE XUAT" in h or "39 - 79" in h:
                    col_suggest = idx
                elif h == "LOAI":
                    col_type = idx

            if (col_sale is None and col_pct is None) and sname not in ("Tổng CH", "Phụ kiện"):
                continue

            for r in rows[1:]:
                if not r or col_sku >= len(r):
                    continue
                code = clean_base_code(r[col_sku])
                if not code or code in EXCLUDED_SKUS:
                    continue
                orig_p = parse_money(r[col_orig]) if (col_orig is not None and col_orig < len(r)) else 0
                sale_p = parse_money(r[col_sale]) if (col_sale is not None and col_sale < len(r)) else 0
                if col_suggest is not None and col_suggest < len(r):
                    sug_p = parse_money(r[col_suggest])
                    if sug_p > 0:
                        sale_p = sug_p
                if sale_p == -1:
                    continue
                pct_v = parse_pct(r[col_pct]) if (col_pct is not None and col_pct < len(r)) else 0.0
                if sale_p <= 0 and orig_p > 0 and pct_v > 0:
                    sale_p = int(round(orig_p * (100.0 - pct_v) / 1000.0) * 1000)

                sub_type = norm_nfc(r[col_type]) if (col_type is not None and col_type < len(r) and r[col_type]) else ""
                if sale_p > 0 or pct_v > 0:
                    prev_sub = sale_rules.get(code, {}).get("subType", "") or PK_KNOWN_SUBTYPES.get(code, "")
                    sale_rules[code] = {
                        "origPrice": orig_p,
                        "salePrice": sale_p,
                        "discountPct": round(pct_v, 1) if pct_v > 0 else (round((1 - sale_p / orig_p) * 100) if orig_p > sale_p > 0 else 30),
                        "subType": sub_type or prev_sub,
                    }
        for ex_code in EXCLUDED_SKUS:
            sale_rules.pop(ex_code, None)
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)
        if xlsx_sha:
            try:
                with open(".bf_xlsx_hash.txt", "w", encoding="utf-8") as mf:
                    mf.write(xlsx_sha)
            except Exception:
                pass
        print(f"[+] Da trich xuat {len(sale_rules)} ma Sale tu {target_xlsx}")
    elif os.path.exists("sale_rules.json"):
        with open("sale_rules.json", "r", encoding="utf-8") as f:
            sale_rules = json.load(f)
    elif csv_files:
        import csv
        with open(csv_files[0], "r", encoding="utf-8-sig") as f:
            for r in list(csv.reader(f))[1:]:
                if len(r) >= 2:
                    code = clean_base_code(r[0])
                    sale_p = parse_money(r[1])
                    if code and sale_p >= 1000:
                        sale_rules[code] = {"origPrice": 0, "salePrice": sale_p, "discountPct": 0, "subType": ""}
    for ex_code in EXCLUDED_SKUS:
        sale_rules.pop(ex_code, None)
    return sale_rules


def build_sku_matcher(codes: list):
    sorted_codes = sorted(set(c.upper() for c in codes if c), key=len, reverse=True)
    compiled = []
    for c in sorted_codes:
        m = re.match(r"^([A-Z]+)(\d+)$", c)
        if m:
            pfx, num = re.escape(m.group(1)), re.escape(m.group(2))
            rx = re.compile(rf"(^|[^A-Z0-9]){pfx}[\s_\-]*{num}([^0-9]|$)", re.I)
        else:
            rx = re.compile(rf"(^|[^A-Z0-9]){re.escape(c)}([^A-Z0-9]|$)", re.I)
        compiled.append((c, rx))
    return compiled


def unpack_any_zip_archives():
    os.makedirs("images", exist_ok=True)
    os.makedirs("videos", exist_ok=True)
    os.makedirs("sizes", exist_ok=True)

    zip_candidates = []
    for pat in ("*.zip", "media/*.zip", "media/**/*.zip", "images/*.zip"):
        zip_candidates.extend(glob.glob(pat, recursive=True))

    for zpath in sorted(set(zip_candidates)):
        low_z = os.path.basename(zpath).lower()
        if "havana_sale" in low_z or "deploy" in low_z or "auto_cloud" in low_z:
            continue
        base_zip_sku = os.path.splitext(os.path.basename(zpath))[0].upper()
        extract_dir = os.path.join("media", "_unzipped_" + base_zip_sku)
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zf_mod.ZipFile(zpath, "r") as zf:
                zf.extractall(extract_dir)
            os.remove(zpath)
            print(f"[+] Da giai nen {zpath} -> {extract_dir}")
        except Exception as e:
            print(f"[!] Loi giai nen {zpath}: {e}")

    if os.path.isdir("media"):
        for root, _, files in os.walk("media"):
            folder_hint = os.path.basename(root).replace("_unzipped_", "").upper()
            sku_prefix = clean_base_code(folder_hint) if folder_hint not in ("MEDIA", "") else ""
            for fname in sorted(files):
                if fname.startswith(".") or fname.lower() == "keep.txt":
                    continue
                src_path = os.path.join(root, fname)
                low = fname.lower()
                target_name = fname
                if sku_prefix and sku_prefix not in re.sub(r"[\s_\-]+", "", fname.upper()):
                    target_name = f"{sku_prefix}_{fname}"

                if low.endswith(VID_EXTS):
                    shutil.copy2(src_path, os.path.join("videos", target_name))
                    os.remove(src_path)
                elif low.endswith(IMG_EXTS):
                    if "size" in low or "bang_size" in low or "bangsize" in low:
                        shutil.copy2(src_path, os.path.join("sizes", target_name))
                    else:
                        shutil.copy2(src_path, os.path.join("images", target_name))
                    os.remove(src_path)

        for entry in os.listdir("media"):
            full_e = os.path.join("media", entry)
            if os.path.isdir(full_e):
                shutil.rmtree(full_e, ignore_errors=True)
        keep_file = os.path.join("media", "keep.txt")
        if not os.path.exists(keep_file):
            with open(keep_file, "w", encoding="utf-8") as f:
                f.write("Drop SKU folders, images, videos, or .zip files here.\n")


def scan_repo_media_folders(codes: list) -> dict:
    unpack_any_zip_archives()
    compiled = build_sku_matcher(codes)
    media_map = {}

    def ensure_entry(c):
        if c not in media_map:
            media_map[c] = {"images": [], "video": "", "sizeChart": ""}
        return media_map[c]

    all_repo_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if fname.startswith(".") or fname.lower() in ("keep.txt", "readme.md"):
                continue
            rel = os.path.join(root, fname).replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]
            all_repo_files.append(rel)

    all_repo_files.sort(key=lambda p: (0 if p.startswith(("images/", "videos/", "sizes/")) else 1, p.lower()))

    for rel_path in all_repo_files:
        low = rel_path.lower()
        is_img = low.endswith(IMG_EXTS)
        is_vid = low.endswith(VID_EXTS)
        if not (is_img or is_vid):
            continue

        fname_only = os.path.basename(rel_path)
        parent_dirs = os.path.dirname(rel_path)
        matched_code = ""
        for code, rx in compiled:
            if rx.search(fname_only):
                matched_code = code
                break
        if not matched_code and parent_dirs:
            for code, rx in compiled:
                if rx.search(parent_dirs):
                    matched_code = code
                    break
        if not matched_code:
            continue

        encoded_url = encode_rel_url(rel_path)
        entry = ensure_entry(matched_code)
        if is_vid:
            if not entry["video"]:
                entry["video"] = encoded_url
        elif is_img:
            if rel_path.startswith("sizes/") or "size" in fname_only.lower() or "bangsize" in fname_only.lower():
                if not entry["sizeChart"]:
                    entry["sizeChart"] = encoded_url
            else:
                if encoded_url not in entry["images"]:
                    entry["images"].append(encoded_url)

    return media_map


def map_exact_havana_branch(raw_name: str, loc_id: str, fallback_idx: int = 9):
    norm = strip_accents(raw_name)
    if norm == "HAVANA" or "HO PHI TICH" in norm or "93-95" in norm or "93 - 95" in norm:
        return ("wh_havana", "HAVANA (93-95 Hồ Phi Tích)", 1)
    if "ONLINE" in norm:
        return ("wh_online", "HAVANA ONLINE", 2)
    if "261" in norm or "PHAN CHU TRINH" in norm or "PHAN CHAU TRINH" in norm:
        return ("wh_pct", "261 - 263 Phan Châu Trinh", 3)
    if "101" in norm or "AU CO" in norm:
        return ("wh_auco", "101 Âu Cơ", 4)
    if "240" in norm or "LE DUAN" in norm:
        return ("wh_leduan", "240 Lê Duẩn", 5)
    clean_lbl = norm_nfc(raw_name) or f"Chi nhánh {loc_id}"
    return (f"wh_{loc_id}", clean_lbl, fallback_idx)


def infer_category(code: str, pname: str, sub_type: str = "") -> str:
    up = code.upper()
    name_l = norm_nfc(pname).lower()
    sub_l = norm_nfc(sub_type).lower()
    hint = f"{sub_l} {name_l}".strip()

    if up.startswith("PK"):
        first_src = sub_l or name_l
        if re.search(r"^(giày|dép|guốc|sandal|sục|boot|bốt)", first_src):
            return "PK_GIAY_DEP"
        if re.search(r"^(túi|ví|balo|ba lô|clutch)", first_src):
            return "PK_TUI_XACH"
        if re.search(r"(giày|dép|guốc|sandal|sục|boot|bốt|cao gót|búp bê|mary jane)", hint):
            return "PK_GIAY_DEP"
        if re.search(r"(túi|\bví\b|balo|ba lô|clutch|tote|đeo chéo|kẹp nách)", hint):
            return "PK_TUI_XACH"
        return "PK_PHU_KIEN"

    if up.startswith("AD"):
        return "AO_DAI"
    if up.startswith("ATH"):
        return "AO_THUN"
    if up.startswith(("AO", "AE")):
        return "AO_SO_MI" if ("sơ mi" in name_l or "sơmi" in name_l) else "AO_KIEU"
    if up.startswith(("AK", "HD")):
        return "AO_KHOAC"
    if up.startswith("LEN"):
        return "AO_LEN"
    if up.startswith("CV"):
        return "CHAN_VAY"
    if up.startswith(("DA", "BE")):
        return "DAM"
    if up.startswith("SAT"):
        return "SET_BO"
    if up.startswith("Q"):
        return "QUAN"
    return "AO_KIEU"


def fetch_json(path: str, headers: dict) -> dict:
    req = urllib.request.Request(f"https://{DOMAIN}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        if raw.lstrip().startswith("<"):
            raise RuntimeError("Sapo Cookie/Token da het han!")
        return json.loads(raw)


def extract_base_code_from_sapo_product(p: dict, sale_rules: dict) -> str:
    # Kiem tra ma SKU nam trong ten san pham Sapo truoc de tranh truong hop 1 bien the go nham SKU cua ma ke ben (VD: SAT532 vs SAT533)
    name_tokens = re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_nfc(p.get("name") or "").upper())
    name_sale_codes = [tok for tok in reversed(name_tokens) if sale_rules and tok in sale_rules]

    var_sale_codes = []
    candidates = []
    for v in p.get("variants", []):
        sku_raw = str(v.get("sku") or "").strip().upper()
        base = clean_base_code(sku_raw.split("-")[0])
        if base:
            if sale_rules and base in sale_rules:
                if base not in var_sale_codes:
                    var_sale_codes.append(base)
            elif not base.startswith("PVN"):
                candidates.append(base)

    if name_sale_codes:
        # Neu ten san pham ghi ro ma (VD: "... SAT533") thi uu tien ma trong ten de khong gop nham anh sang SAT532
        if name_sale_codes[0] in var_sale_codes or len(var_sale_codes) != 1:
            return name_sale_codes[0]
        return name_sale_codes[0]

    if var_sale_codes:
        return var_sale_codes[0]

    for tok in reversed(name_tokens):
        if not tok.startswith("PVN"):
            candidates.append(tok)

    return candidates[0] if candidates else ""


def write_outputs(warehouses_sorted: list, products_out: list, synced_at_str: str, sale_rules: dict = None):
    payload = {
        "synced_at": synced_at_str,
        "warehouses": warehouses_sorted,
        "products": products_out,
    }
    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if os.path.exists("index.html") and products_out:
        with open("index.html", "r", encoding="utf-8") as f:
            html_src = f.read()
        s_i = html_src.find("/* [BLOCK:DATA_PAYLOAD_START] */")
        e_i = html_src.find("/* [BLOCK:DATA_PAYLOAD_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_PAYLOAD_END] */")
            wh_js = json.dumps(warehouses_sorted, ensure_ascii=False)
            pr_js = json.dumps(products_out, ensure_ascii=False)
            sr_js = json.dumps(sale_rules or {}, ensure_ascii=False)
            new_blk = (
                "/* [BLOCK:DATA_PAYLOAD_START] */\n"
                f"    let WAREHOUSE_CONFIG = {wh_js};\n"
                f"    let SALE_RULES_MASTER = {sr_js};\n"
                f"    let BASELINE_PRODUCTS = {pr_js};\n"
                f"    let PRODUCTS = BASELINE_PRODUCTS.map((p) => Object.assign({{}}, p));\n"
                "    /* [BLOCK:DATA_PAYLOAD_END] */"
            )
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html_src[:s_i] + new_blk + html_src[e_i:])


def local_media_url_exists(rel_url: str) -> bool:
    u = str(rel_url or "").strip().split("?")[0]
    if not u:
        return False
    if u.startswith(("http://", "https://")):
        if "raw.githubusercontent.com/moonchuyensi-cmd/havana-sale/" in u:
            rel_part = urllib.parse.unquote(u.split("/main/", 1)[-1])
            return os.path.exists(rel_part)
        return True
    clean_p = urllib.parse.unquote(u.lstrip("./"))
    return os.path.exists(clean_p)


def apply_priority_media_to_list(products_list: list, preserved_by_sku: dict, purged_skus: set = None) -> list:
    """Uu tien so 1: Anh/video tu up tren repo (images/, videos/, sizes/) CON TON TAI TREN DIA.
    Uu tien so 2 (khi ma chua co anh tu up hoac vua xoa anh cu): Dung ngay anh tu Sapo (sapoImages)."""
    purged_skus = purged_skus or set()
    codes = [str(p.get("sku") or "").upper() for p in products_list]
    repo_media = scan_repo_media_folders(codes)

    # Thu thap tap anh Sapo cua SAT533 de dam bao SAT532 khong bao gio bi dinh nham anh SAT533
    sat533_sapo_set = set()
    for p in products_list:
        if str(p.get("sku") or "").upper() == "SAT533":
            _, s533, _, _ = split_custom_and_sapo_media(p)
            sat533_sapo_set.update(s533)
    sat533_sapo_set.update(preserved_by_sku.get("SAT533", {}).get("sapoImages") or [])

    for p in products_list:
        cur_custom, cur_sapo, cur_vid, cur_sz = split_custom_and_sapo_media(p)
        code = str(p.get("sku") or "").upper()
        prev = preserved_by_sku.get(code, {})
        repo_m = repo_media.get(code, {})

        if code in purged_skus or code in FORCE_RESET_TO_SAPO_SKUS:
            # Chi nhan anh tu up neu la file MOI THUC SU dang nam trong repo_m (va khong chua chu SAT533)
            custom_imgs = [
                u for u in (repo_m.get("images") or [])
                if "SAT533" not in u.upper() and local_media_url_exists(u)
            ]
            vid = repo_m.get("video") or ""
            if "SAT533" in vid.upper() or (vid and not local_media_url_exists(vid)):
                vid = ""
            szc = repo_m.get("sizeChart") or ""
            if "SAT533" in szc.upper() or (szc and not local_media_url_exists(szc)):
                szc = ""
        else:
            prev_valid_imgs = [u for u in (prev.get("customImages") or []) if local_media_url_exists(u)]
            cur_valid_imgs = [u for u in cur_custom if local_media_url_exists(u)]
            custom_imgs = repo_m.get("images") or prev_valid_imgs or cur_valid_imgs or []
            vid_cand = repo_m.get("video") or prev.get("video") or cur_vid or ""
            vid = vid_cand if local_media_url_exists(vid_cand) else ""
            sz_cand = repo_m.get("sizeChart") or prev.get("sizeChart") or cur_sz or ""
            szc = sz_cand if local_media_url_exists(sz_cand) else ""

        sapo_imgs = cur_sapo if len(cur_sapo) > 0 else (prev.get("sapoImages") or [])
        if code == "SAT532":
            # Loai bo bat ky URL nao trung voi anh cua SAT533 hoac chua chu sat533
            sapo_imgs = [
                u for u in sapo_imgs
                if u not in sat533_sapo_set and "sat533" not in u.lower()
            ]

        final_imgs = custom_imgs if len(custom_imgs) > 0 else sapo_imgs
        p["sapoImages"] = sapo_imgs
        p["hasCustomMedia"] = bool(len(custom_imgs) > 0 or vid)
        p["images"] = final_imgs
        p["image"] = final_imgs[0] if final_imgs else ""
        p["video"] = vid
        p["sizeChart"] = szc

    return products_list


def build_compact_stock_feed(warehouses_sorted: list, products_out: list, synced_at_str: str) -> str:
    """Dong goi bang ton kho 5 chi nhanh sieu gon (~20KB) de phat truc tiep qua GitHub Release API moi 5 phut
    ma KHONG ton bat ky luot build nao cua Cloudflare Pages."""
    wh_ids = [w.get("id") for w in warehouses_sorted]
    items_compact = {}
    for p in products_out:
        code = str(p.get("sku") or "").upper()
        if not code:
            continue
        v_rows = []
        for v in (p.get("variants") or []):
            st = v.get("stocks") or {}
            qty_arr = [int(st.get(wid) or 0) for wid in wh_ids]
            v_rows.append([v.get("size") or "Freesize", qty_arr])
        entry = {"v": v_rows}
        if p.get("name") and not str(p.get("name")).startswith(("Sản phẩm Havana ", "Áo kiểu thiết kế Havana ")):
            entry["n"] = p["name"]
        items_compact[code] = entry

    feed_obj = {
        "v": 22,
        "synced_at": synced_at_str,
        "synced_epoch": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        "warehouses": warehouses_sorted,
        "items": items_compact,
    }
    return json.dumps(feed_obj, ensure_ascii=False, separators=(",", ":"))


def publish_live_release_feed(warehouses_sorted: list, products_out: list, synced_at_str: str) -> bool:
    """Cap nhat truc tiep Release tag `live-stock` tren GitHub bang GITHUB_TOKEN co san."""
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "moonchuyensi-cmd/havana-sale").strip()
    if not gh_token or not repo_slug:
        return False

    compact_body = build_compact_stock_feed(warehouses_sorted, products_out, synced_at_str)
    if len(compact_body) > 120000:
        # Neu vuot 120KB thi bo truong ten 'n' cho sieu nhe (~18KB)
        data_tmp = json.loads(compact_body)
        for k in data_tmp.get("items", {}).values():
            k.pop("n", None)
        compact_body = json.dumps(data_tmp, ensure_ascii=False, separators=(",", ":"))

    api_headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "havana-sale-auto-sync-v22",
    }

    tag_url = f"https://api.github.com/repos/{repo_slug}/releases/tags/live-stock"
    rel_id = None
    try:
        req = urllib.request.Request(tag_url, headers=api_headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            rel_data = json.loads(resp.read().decode("utf-8"))
            rel_id = rel_data.get("id")
    except urllib.error.HTTPError as he:
        if he.code != 404:
            print(f"[!] Loi kiem tra release live-stock ({he.code}): {he}")
    except Exception as e:
        print(f"[!] Loi mang khi kiem tra release live-stock: {e}")

    payload_bytes = json.dumps({
        "tag_name": "live-stock",
        "name": f"Live Sapo Stock ({synced_at_str})",
        "body": compact_body,
        "draft": False,
        "prerelease": True,
    }, ensure_ascii=False).encode("utf-8")

    try:
        if rel_id:
            patch_url = f"https://api.github.com/repos/{repo_slug}/releases/{rel_id}"
            req = urllib.request.Request(patch_url, data=payload_bytes, headers=api_headers, method="PATCH")
        else:
            create_url = f"https://api.github.com/repos/{repo_slug}/releases"
            req = urllib.request.Request(create_url, data=payload_bytes, headers=api_headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            _ = resp.read()
        print(f"[+] Da phat Live Stock Feed ({len(compact_body)} bytes) len Release [live-stock] luc {synced_at_str}!")
        return True
    except Exception as e:
        print(f"[!] Khong cap nhat duoc Release live-stock: {e}")
        return False


def compute_products_fingerprint(products_list: list) -> str:
    norm_items = []
    for p in sorted(products_list, key=lambda x: str(x.get("sku") or "")):
        norm_items.append({
            "s": p.get("sku"),
            "op": p.get("origPrice"),
            "sp": p.get("salePrice"),
            "im": p.get("images") or [],
            "vd": p.get("video") or "",
            "sz": p.get("sizeChart") or "",
            "vr": p.get("variants") or [],
        })
    raw = json.dumps(norm_items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_media_rules_fingerprint(products_list: list) -> str:
    norm_items = []
    for p in sorted(products_list, key=lambda x: str(x.get("sku") or "")):
        norm_items.append({
            "s": p.get("sku"),
            "op": p.get("origPrice"),
            "sp": p.get("salePrice"),
            "im": p.get("images") or [],
            "vd": p.get("video") or "",
            "sz": p.get("sizeChart") or "",
        })
    raw = json.dumps(norm_items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def last_git_commit_age_seconds() -> int:
    try:
        out = subprocess.check_output(["git", "log", "-1", "--format=%ct"], text=True, timeout=5).strip()
        return max(0, int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - int(out))
    except Exception:
        return 999999


def main():
    purged_now = purge_wrong_custom_media_once()
    sale_rules = load_black_friday_rules()
    filter_only_bf = len(sale_rules) > 0

    existing_warehouses = []
    existing_products = []
    preserved_media_by_sku = {}
    if os.path.exists("sapo_5_kho.json"):
        try:
            with open("sapo_5_kho.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
            existing_warehouses = old_data.get("warehouses") or []
            existing_products = old_data.get("products") or []
            for op in existing_products:
                c_imgs, s_imgs, vid, szc = split_custom_and_sapo_media(op)
                c = clean_base_code(str(op.get("sku") or "").split("-")[0])
                if c:
                    preserved_media_by_sku[c] = {
                        "customImages": c_imgs,
                        "sapoImages": s_imgs,
                        "video": vid,
                        "sizeChart": szc,
                    }
        except Exception:
            pass

    candidates = build_header_candidates()
    loc_meta = {}
    all_products = []
    sapo_err_reason = ""

    if not candidates:
        sapo_err_reason = "CHƯA CÓ SAPO_COOKIE TRONG GITHUB SECRETS (hoặc .sapo_cookie.txt)!"
        print(f"[CẢNH BÁO] {sapo_err_reason}")
    else:
        err_list = []
        for auth_label, hdrs in candidates:
            try:
                print(f"[{vn_now_str()}] Dang ket noi Sapo bang [{auth_label}]...")
                first_page = fetch_json("/admin/products.json?page=1&limit=250", hdrs)
                first_batch = first_page.get("products", [])
                if first_batch is None:
                    raise RuntimeError("Khong co truong products")
                all_products.extend(first_batch)
                try:
                    ldata = fetch_json("/admin/locations.json", hdrs)
                    for idx, loc in enumerate(ldata.get("locations", [])):
                        raw_id = str(loc["id"])
                        raw_loc_name = loc.get("label") or loc.get("name") or loc.get("location_name") or loc.get("address1") or loc.get("code") or ""
                        wid, label, order = map_exact_havana_branch(raw_loc_name, raw_id, 10 + idx)
                        loc_meta[raw_id] = {"id": wid, "sapo_name": raw_loc_name, "name": label, "order": order}
                except Exception as e:
                    print("[*] Loi nhe khi doc locations.json:", e)

                if len(first_batch) == 250:
                    meta_total = int((first_page.get("metadata") or {}).get("total") or 0)
                    if meta_total > 250:
                        max_page = min(60, (meta_total + 249) // 250)
                        pages_to_fetch = list(range(2, max_page + 1))
                        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                            futs = {pool.submit(fetch_json, f"/admin/products.json?page={pg}&limit=250", hdrs): pg for pg in pages_to_fetch}
                            for pg in pages_to_fetch:
                                for fut, f_pg in futs.items():
                                    if f_pg == pg:
                                        pdata = fut.result()
                                        all_products.extend(pdata.get("products") or [])
                                        break
                    else:
                        page = 2
                        while page <= 60:
                            pdata = fetch_json(f"/admin/products.json?page={page}&limit=250", hdrs)
                            batch = pdata.get("products", [])
                            if not batch:
                                break
                            all_products.extend(batch)
                            if len(batch) < 250:
                                break
                            page += 1
                print(f"[+] Ket noi thanh cong bang [{auth_label}]! Da quet {len(all_products)} san pham tren Sapo.")
                break
            except Exception as err:
                err_list.append(f"{auth_label}: {err}")
                print(f"[!] Thu [{auth_label}] loi: {err}")
        if not all_products and err_list:
            sapo_err_reason = " | ".join(err_list)

    if not all_products:
        if existing_products or sale_rules:
            updated_existing = []
            seen_skus = set()
            for ep in existing_products:
                c = clean_base_code(str(ep.get("sku") or "").split("-")[0])
                if c in EXCLUDED_SKUS or (filter_only_bf and c not in sale_rules):
                    continue
                if c in sale_rules:
                    r_info = sale_rules[c]
                    orig_p = r_info.get("origPrice") or int(ep.get("origPrice") or 299000)
                    sale_p = r_info.get("salePrice") or int(ep.get("salePrice") or 159000)
                    if orig_p < sale_p:
                        orig_p = max(orig_p, int(ep.get("origPrice") or sale_p))
                    disc_p = max(1, round((1.0 - sale_p / orig_p) * 100)) if orig_p > sale_p > 0 else r_info.get("discountPct", 30)
                    sub_t = r_info.get("subType") or ep.get("subType") or PK_KNOWN_SUBTYPES.get(c, "")
                    ep["sku"] = c
                    ep["origPrice"] = orig_p
                    ep["salePrice"] = sale_p
                    ep["discountPct"] = disc_p
                    ep["subType"] = sub_t
                    ep["category"] = infer_category(c, ep.get("name") or c, sub_t)
                seen_skus.add(c)
                updated_existing.append(ep)

            for c, r_info in sale_rules.items():
                if c not in seen_skus and c not in EXCLUDED_SKUS:
                    sub_t = r_info.get("subType") or PK_KNOWN_SUBTYPES.get(c, "")
                    pname_fb = f"{sub_t} Havana {c}".strip() if sub_t else f"Sản phẩm Havana {c}"
                    updated_existing.append({
                        "sku": c,
                        "name": pname_fb,
                        "subType": sub_t,
                        "category": infer_category(c, pname_fb, sub_t),
                        "origPrice": r_info.get("origPrice") or 299000,
                        "salePrice": r_info.get("salePrice") or 159000,
                        "discountPct": r_info.get("discountPct") or 30,
                        "image": "",
                        "images": [],
                        "sapoImages": [],
                        "video": "",
                        "sizeChart": "",
                        "variants": [{"size": "Freesize", "stocks": {"wh_pct": 1}}],
                    })

            cleaned = apply_priority_media_to_list(updated_existing, preserved_media_by_sku, purged_now)
            wh_out = existing_warehouses or [
                {"id": "wh_havana", "sapo_name": "HAVANA", "name": "HAVANA (93-95 Hồ Phi Tích)"},
                {"id": "wh_online", "sapo_name": "HAVANA ONLINE", "name": "HAVANA ONLINE"},
                {"id": "wh_pct", "sapo_name": "261 - 263 phan chu trinh", "name": "261 - 263 Phan Châu Trinh"},
                {"id": "wh_auco", "sapo_name": "101 Âu Cơ", "name": "101 Âu Cơ"},
                {"id": "wh_leduan", "sapo_name": "240 Lê Duẩn", "name": "240 Lê Duẩn"},
            ]
            write_outputs(wh_out, cleaned, vn_now_str(), sale_rules)
            with_custom = sum(1 for x in cleaned if x.get("hasCustomMedia"))
            with_any = sum(1 for x in cleaned if x.get("image") or x.get("video"))
            print(f"[+] Da cap nhat {len(cleaned)} ma ({with_custom} ma anh tu up, {with_any - with_custom} ma dung tam anh Sapo)!")
            write_github_summary(f"### ⚠️ Chưa kéo được ảnh mới từ Sapo\n- **Nguyên nhân:** `{sapo_err_reason or 'Sapo trả về 0 sản phẩm'}`\n- ** Trạng thái hiện tại:** `{len(cleaned)}` mã Sale (`{with_custom}` mã đã có ảnh tự up, `{with_any - with_custom}` mã có ảnh tạm Sapo).\n- **Cách khắc phục:** Vào `Settings -> Secrets and variables -> Actions` cập nhật biến `SAPO_COOKIE` (hoặc `SAPO_ACCESS_TOKEN`) rồi bấm `Run workflow` lại.")
        else:
            unpack_any_zip_archives()
        return

    grouped_by_code = {}
    for p in all_products:
        variants_raw = p.get("variants", [])
        if not variants_raw:
            continue
        base_code = extract_base_code_from_sapo_product(p, sale_rules)
        if base_code in EXCLUDED_SKUS or (filter_only_bf and base_code not in sale_rules):
            continue

        rule = sale_rules.get(base_code, {})
        first_v = variants_raw[0]
        sapo_price = parse_money(first_v.get("variant_retail_price") or first_v.get("price") or 0)
        excel_orig = rule.get("origPrice") or 0
        orig_price = excel_orig if excel_orig > 0 else (sapo_price if sapo_price > 0 else 299000)

        sale_price = rule.get("salePrice", 0)
        disc_pct = rule.get("discountPct", 30)
        if sale_price <= 0 and disc_pct > 0:
            sale_price = int(round(orig_price * (100 - disc_pct) / 1000.0) * 1000)
        if orig_price > 0 and 0 < sale_price < orig_price:
            disc_pct = max(1, round((1.0 - sale_price / orig_price) * 100))

        sapo_imgs_found = extract_sapo_product_images(p)

        v_list = []
        for v in variants_raw:
            opt_parts = [norm_nfc(v.get(k)) for k in ("opt1", "opt2", "opt3") if v.get(k)]
            sz_label = " - ".join(opt_parts) if opt_parts else norm_nfc(v.get("title") or "Freesize")
            if sz_label.lower() == "default title":
                sz_label = "Freesize"
            stocks = {}
            for inv in v.get("inventories", []):
                raw_id = str(inv.get("location_id"))
                if raw_id not in loc_meta:
                    raw_name = inv.get("location_label") or inv.get("location_name") or inv.get("name") or ""
                    wid, label, order = map_exact_havana_branch(raw_name, raw_id, 20 + len(loc_meta))
                    loc_meta[raw_id] = {"id": wid, "sapo_name": raw_name, "name": label, "order": order}
                avail = inv.get("available")
                if avail is None:
                    avail = inv.get("on_hand", 0)
                wid = loc_meta[raw_id]["id"]
                stocks[wid] = stocks.get(wid, 0) + max(0, int(avail or 0))
            v_list.append({"size": sz_label, "stocks": stocks})

        pname_nfc = norm_nfc(p.get("name") or base_code)
        sub_type = rule.get("subType", "")
        key = base_code or (first_v.get("sku") or f"SP{p['id']}").strip()

        if key in grouped_by_code:
            grouped_by_code[key]["variants"].extend(v_list)
            for u in sapo_imgs_found:
                if u not in grouped_by_code[key]["sapoImages"]:
                    grouped_by_code[key]["sapoImages"].append(u)
        else:
            grouped_by_code[key] = {
                "sku": key,
                "name": pname_nfc,
                "subType": sub_type,
                "category": infer_category(key, pname_nfc, sub_type),
                "origPrice": orig_price,
                "salePrice": sale_price,
                "discountPct": disc_pct,
                "image": "",
                "images": [],
                "sapoImages": sapo_imgs_found,
                "video": "",
                "sizeChart": "",
                "variants": v_list,
            }

    if filter_only_bf and sale_rules:
        existing_by_code = {clean_base_code(str(ep.get("sku") or "").split("-")[0]): ep for ep in existing_products}
        for c, r_info in sale_rules.items():
            if c not in grouped_by_code and c not in EXCLUDED_SKUS:
                ep_prev = existing_by_code.get(c)
                sub_t = r_info.get("subType") or (ep_prev.get("subType") if ep_prev else "") or PK_KNOWN_SUBTYPES.get(c, "")
                pname_fb = (ep_prev.get("name") if ep_prev else "") or (f"{sub_t} Havana {c}".strip() if sub_t else f"Sản phẩm Havana {c}")
                grouped_by_code[c] = {
                    "sku": c,
                    "name": pname_fb,
                    "subType": sub_t,
                    "category": infer_category(c, pname_fb, sub_t),
                    "origPrice": r_info.get("origPrice") or (ep_prev.get("origPrice") if ep_prev else 299000),
                    "salePrice": r_info.get("salePrice") or (ep_prev.get("salePrice") if ep_prev else 159000),
                    "discountPct": r_info.get("discountPct") or (ep_prev.get("discountPct") if ep_prev else 30),
                    "image": "",
                    "images": [],
                    "sapoImages": (ep_prev.get("sapoImages") if ep_prev and isinstance(ep_prev.get("sapoImages"), list) else []),
                    "video": "",
                    "sizeChart": "",
                    "variants": (ep_prev.get("variants") if (ep_prev and ep_prev.get("variants")) else [{"size": "Freesize", "stocks": {"wh_pct": 1}}]),
                }

    products_out = apply_priority_media_to_list(list(grouped_by_code.values()), preserved_media_by_sku, purged_now)
    warehouses_sorted = [
        {k: v for k, v in item.items() if k != "order"}
        for item in sorted(loc_meta.values(), key=lambda x: x["order"])
    ]

    now_stamp = vn_now_str()
    old_full_fp = compute_products_fingerprint(existing_products) if existing_products else ""
    old_media_fp = compute_media_rules_fingerprint(existing_products) if existing_products else ""
    new_full_fp = compute_products_fingerprint(products_out)
    new_media_fp = compute_media_rules_fingerprint(products_out)

    # 1. LUON LUON phat Live Stock Feed tuc thi moi 5 phut (0 ton build Cloudflare!)
    live_feed_ok = publish_live_release_feed(warehouses_sorted, products_out, now_stamp)

    # 2. Quyet dinh co can commit len nhanh main hay khong (bao ve han muc 500 builds/thang cua Cloudflare Pages)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    commit_age = last_git_commit_age_seconds()
    media_or_price_changed = (old_media_fp != new_media_fp)
    stock_changed = (old_full_fp != new_full_fp)

    should_commit_main = True
    if event_name == "schedule" and live_feed_ok and not purged_now and not media_or_price_changed:
        if not stock_changed:
            should_commit_main = False
        elif commit_age < 1800:
            # Ton kho da len Live Feed ngay lap tuc; gom commit tren main moi 30 phut de tiet kiem quota Cloudflare
            should_commit_main = False

    if should_commit_main:
        if os.path.exists(".skip_git_commit"):
            os.remove(".skip_git_commit")
        write_outputs(warehouses_sorted, products_out, now_stamp, sale_rules)
    else:
        with open(".skip_git_commit", "w", encoding="utf-8") as sf:
            sf.write(now_stamp)

    with_custom = sum(1 for x in products_out if x.get("hasCustomMedia"))
    with_sapo_fb = sum(1 for x in products_out if (not x.get("hasCustomMedia")) and x.get("image"))
    mode_note = "Đã đẩy Live Feed tức thì + Commit lên nhánh chính" if should_commit_main else "Đã đẩy Live Feed tức thì (tiết kiệm lượt build Cloudflare)"
    print(f"[+] Hoan tat Auto-Sync v22: {len(products_out)} ma ({with_custom} anh tu up, {with_sapo_fb} anh Sapo) luc {now_stamp}! [{mode_note}]")
    sat532_item = next((x for x in products_out if x.get("sku") == "SAT532"), None)
    sat532_imgs = (sat532_item.get("images") if sat532_item else []) or []
    sat532_status = f"Đã cập nhật `{len(sat532_imgs)}` ảnh từ Sapo (`{sat532_imgs[0]}`)" if sat532_imgs else "Trên Sapo mã SAT532 hiện chưa có ảnh đính kèm"
    write_github_summary(f"### ✅ Tự động đồng bộ Tồn Kho Liên Tục v22 ({now_stamp})\n- **Chế độ cập nhật:** `{mode_note}`\n- **Tổng sản phẩm quét trên Sapo:** `{len(all_products)}`\n- **Tổng mã Sale trên web:** `{len(products_out)}` (`{with_custom}` ảnh tự up • `{with_sapo_fb}` ảnh Sapo)\n- **Trạng thái mã `SAT532`:** {sat532_status}")


if __name__ == "__main__":
    main()
