import os
import re
import sys
import glob
import json
import shutil
import datetime
import subprocess
import unicodedata
import urllib.parse
import urllib.request
import zipfile as zf_mod

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v19 - UU TIEN ANH TU UP -> CHUA CO ANH THI DUNG TAM ANH SAPO)
# ==============================================================================

DEFAULT_COOKIE = ""
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
SAPO_IMG_RX = re.compile(r"(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com)", re.I)

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
    return bool(u and SAPO_IMG_RX.search(u))


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


def build_headers() -> dict:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    token = os.environ.get("SAPO_ACCESS_TOKEN", "").strip()
    cookie = os.environ.get("SAPO_COOKIE", "").strip() or DEFAULT_COOKIE
    if token:
        headers["X-Sapo-Access-Token"] = token
    elif cookie:
        headers["cookie"] = cookie
        headers["referer"] = f"https://{DOMAIN}/admin/products"
        headers["x-sapo-client"] = "sapo-frontend-v3"
        headers["x-sapo-serviceid"] = "sapo-frontend-v3"
    return headers


def load_black_friday_rules() -> dict:
    excel_files = [f for f in glob.glob("*.xlsx") if not os.path.basename(f).startswith("~$")]
    csv_files = glob.glob("*.csv")
    sale_rules = {}

    if excel_files:
        excel_files.sort(key=lambda x: (0 if "black" in x.lower() else 1, x))
        target_xlsx = excel_files[0]
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
    candidates = []
    for v in p.get("variants", []):
        sku_raw = str(v.get("sku") or "").strip().upper()
        base = clean_base_code(sku_raw.split("-")[0])
        if base:
            if sale_rules and base in sale_rules:
                return base
            if not base.startswith("PVN"):
                candidates.append(base)

    name_tokens = re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_nfc(p.get("name") or "").upper())
    for tok in reversed(name_tokens):
        if sale_rules and tok in sale_rules:
            return tok
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


def apply_priority_media_to_list(products_list: list, preserved_by_sku: dict) -> list:
    """Uu tien so 1: Anh/video tu up tren repo (images/, videos/, sizes/).
    Uu tien so 2 (khi ma chua co anh tu up): Dung tam anh tu Sapo (sapoImages).
    Ngay khi user up anh moi cho ma do -> tu dong chuyen sang anh tu up!"""
    codes = [str(p.get("sku") or "").upper() for p in products_list]
    repo_media = scan_repo_media_folders(codes)

    for p in products_list:
        cur_custom, cur_sapo, cur_vid, cur_sz = split_custom_and_sapo_media(p)
        code = str(p.get("sku") or "").upper()
        prev = preserved_by_sku.get(code, {})
        repo_m = repo_media.get(code, {})

        custom_imgs = repo_m.get("images") or prev.get("customImages") or cur_custom or []
        sapo_imgs = cur_sapo or prev.get("sapoImages") or []
        vid = repo_m.get("video") or prev.get("video") or cur_vid or ""
        szc = repo_m.get("sizeChart") or prev.get("sizeChart") or cur_sz or ""

        # Neu da co anh tu up (hoac video tu up kem anh) -> CHỈ dung anh tu up; chua co anh tu up -> dung tam anh Sapo!
        final_imgs = custom_imgs if len(custom_imgs) > 0 else sapo_imgs
        p["sapoImages"] = sapo_imgs
        p["hasCustomMedia"] = bool(len(custom_imgs) > 0 or vid)
        p["images"] = final_imgs
        p["image"] = final_imgs[0] if final_imgs else ""
        p["video"] = vid
        p["sizeChart"] = szc

    return products_list


def main():
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

    headers = build_headers()
    loc_meta = {}
    all_products = []

    if "cookie" in headers or "X-Sapo-Access-Token" in headers:
        try:
            print(f"[{vn_now_str()}] Dang dong bo ton kho 5 chi nhanh & anh du phong Sapo...")
            try:
                ldata = fetch_json("/admin/locations.json", headers)
                for idx, loc in enumerate(ldata.get("locations", [])):
                    raw_id = str(loc["id"])
                    raw_loc_name = loc.get("label") or loc.get("name") or loc.get("location_name") or loc.get("address1") or loc.get("code") or ""
                    wid, label, order = map_exact_havana_branch(raw_loc_name, raw_id, 10 + idx)
                    loc_meta[raw_id] = {"id": wid, "sapo_name": raw_loc_name, "name": label, "order": order}
            except Exception as e:
                print("[*] Loi nhe khi doc locations.json:", e)

            page = 1
            while page <= 60:
                pdata = fetch_json(f"/admin/products.json?page={page}&limit=250", headers)
                batch = pdata.get("products", [])
                if not batch:
                    break
                all_products.extend(batch)
                if len(batch) < 250:
                    break
                page += 1
            print(f"[*] Da quet {len(all_products)} san pham tren Sapo.")
        except Exception as err:
            print(f"[!] Sapo API tam ngat ({err}) -> Van quet toan bo repo & cap nhat anh/video!")

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

            cleaned = apply_priority_media_to_list(updated_existing, preserved_media_by_sku)
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

    products_out = apply_priority_media_to_list(list(grouped_by_code.values()), preserved_media_by_sku)
    warehouses_sorted = [
        {k: v for k, v in item.items() if k != "order"}
        for item in sorted(loc_meta.values(), key=lambda x: x["order"])
    ]

    now_stamp = vn_now_str()
    write_outputs(warehouses_sorted, products_out, now_stamp, sale_rules)
    with_custom = sum(1 for x in products_out if x.get("hasCustomMedia"))
    with_sapo_fb = sum(1 for x in products_out if (not x.get("hasCustomMedia")) and x.get("image"))
    print(f"[+] Hoan tat Auto-Sync v19: {len(products_out)} ma ({with_custom} ma dung anh tu up, {with_sapo_fb} ma dung tam anh Sapo) luc {now_stamp}!")


if __name__ == "__main__":
    main()
