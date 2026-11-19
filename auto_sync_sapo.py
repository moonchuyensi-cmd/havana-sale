import os
import re
import sys
import glob
import json
import datetime
import subprocess
import unicodedata
import urllib.request

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v10 - DYNAMIC CATEGORIES + PK TUI/GIAY/PHU KIEN + 5 KHO)
# ==============================================================================

DEFAULT_COOKIE = '_ga=GA1.1.1979031352.1792116728; _fbp=fb.1.1792116728809.817733756256028406; ajs_anonymous_id=ca11853b-0818-4793-bcfe-026c09b55a6d; report_ver=3; ajs_user_id=havanago.mysapogo.com; _hjSessionUser_1081489=eyJpZCI6Ijc5NWM1NzQxLWZjMWYtNTFkMy1iYzQyLTAzNzY1MmJiYjIzYiIsImNyZWF0ZWQiOjE3OTI0NjQ1NjcwMTAsImV4aXN0aW5nIjp0cnVlfQ==; _gcl_au=1.1.1746560884.1792979800; _ga=GA1.3.1979031352.1792116728; promotion-v3=true; limitSuppliers=20; saveSettingPrintBulk=false; lang=vi; .AspNetCore.Session=CfDJ8DA1016STPdNrAaTdK2HgWdcBxfo8AgqDVnes%2F0679ty0Hk%2Fmg0l2OFbSkFlWiZMNf7TznALBbwZb%2Bj1LL4cKHruxmK%2B%2Bgc3O4DP%2BMdt6r8gRgWEEXBq24oGVhpCWIf96vOIRC4HvuCgvajQ1BM9tHUCWVyMtUKC67PjdxT4odBS; sapo-admin=CfDJ8DA1016STPdNrAaTdK2HgWeaKcobyzxTOswWEeLMFIbAXSNA-6RepjyX8BwGQdJV8vr80m30h-3bwPP5w7PdcEstsI9yx95WMieUYoditvJxOH5UZ-IgB_XSbM-CvHnXTrJ7UG0b0G_VtOeqDkeluNnncLmCPDdrfr7-1XTtBOk7Wy7byqfK6h1Ei1hz5wxg_L2v-aFcG2OkHPJZX4BWvc2LALPzaCWvV1_3NmX-gSvGhcTIzCVccvHJ1FI35WA2SrrW5G8XY7mlwBedGZfBZZPcLvsO_pjO3rocy2orMQXk9_HgeDeZ_UFm07Md5wsFlaD-o_0HV0xA4Y948wzusKI1bzYscwdGrYk3hZ7A33Hp-5yPevNkR_FkCqbCZxcy9ltjtG31iN1MkwZWKVouJMw07KycXZHgDIUPeGY45xLtNjUi532BYM8bSWJEgmJ3qVI_zbEaUTL6W6KyG46mHkHbF_iFIFzIQ12qDZeBEKnJh3kt8TmuJyZP7Z0xUK5CdyUV3ySFiV2704_CBUYMtoNxoHGVGNtwWcOVn4deWRumuU4rMMti8kymFTq4g9Ed9Yk502WLYNdJat8ex1oORnTE7xOu0JC4GbYcivfI7Q8Ahd_Z0bUUZxMhWyIiJofq_DKgKiGEQ7G4b5gzSebP3_LDIJ_rz1G2Vf_kA25KYBwhrMgATyGMKTm1bMyevINfDBtUfyKGErEIPH9x334pPOxb5G9tKQKFzMXQBNb0NDt370xo9VYYFsXoPJvop_m34Y2RhJhelMs23F8QYMLik6w; _admin_session_id=2c2a5c7d-fb8d-0054-4940-5a6f41dfb19e; _ga_4NX0F91DEX=GS2.1.s1794983461$o159$g1$t1794984328$j54$l0$h0; _ga_Z1V9QDBF2Q=GS2.1.s1794983460$o137$g1$t1794984329$j53$l0$h0'

DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)


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


def parse_money(val) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        v = float(val)
        if 0 < v < 1000:
            return int(round(v * 1000))
        return int(round(v))
    s = str(val).strip().lower()
    if not s or "quà" in s or "qua tang" in strip_accents(s).lower() or "#n/a" in s:
        return -1 if "quà" in s else 0
    s_digits = re.sub(r"[^0-9]", "", s)
    if not s_digits:
        return 0
    num = int(s_digits)
    if 0 < num < 1000:
        num *= 1000
    return num


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
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
    }
    token = os.environ.get("SAPO_ACCESS_TOKEN", "").strip()
    cookie = os.environ.get("SAPO_COOKIE", "").strip() or DEFAULT_COOKIE
    if token:
        headers["X-Sapo-Access-Token"] = token
    else:
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
                if not code:
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
                    prev_sub = sale_rules.get(code, {}).get("subType", "")
                    sale_rules[code] = {
                        "origPrice": orig_p,
                        "salePrice": sale_p,
                        "discountPct": round(pct_v, 1) if pct_v > 0 else (round((1 - sale_p / orig_p) * 100) if orig_p > sale_p > 0 else 30),
                        "subType": sub_type or prev_sub,
                    }
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
    return sale_rules


def scan_repo_media_folders(codes: list) -> dict:
    sorted_codes = sorted(set(codes), key=len, reverse=True)
    media_map = {}
    if not os.path.isdir("images"):
        return media_map
    for fname in sorted(os.listdir("images")):
        if fname.startswith(".") or not fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        up_name = fname.upper()
        for code in sorted_codes:
            pattern = rf"(^|[^A-Z0-9]){re.escape(code)}([^A-Z0-9]|$)"
            if re.search(pattern, up_name):
                if code not in media_map:
                    media_map[code] = f"./images/{fname}"
                break
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
        if re.search(r"(túi|ví|balo|ba lô|clutch|tote|đeo chéo|kẹp nách)", hint):
            return "PK_TUI_XACH"
        return "PK_PHU_KIEN"

    if up.startswith("AD"):
        return "AO_DAI"
    if up.startswith("ATH"):
        return "AO_THUN"
    if up.startswith(("AO", "AE")):
        if "sơ mi" in name_l or "sơmi" in name_l:
            return "AO_SO_MI"
        return "AO_KIEU"
    if up.startswith(("AK", "HD")):
        return "AO_KHOAC"
    if up.startswith("LEN"):
        return "AO_LEN"
    if up.startswith("CV"):
        return "CHAN_VAY"
    if up.startswith("DA"):
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


def main():
    sale_rules = load_black_friday_rules()
    filter_only_bf = len(sale_rules) > 0
    headers = build_headers()

    loc_meta = {}
    all_products = []

    try:
        print(f"[{vn_now_str()}] Dang dong bo tu dong 5 chi nhanh Sapo ({DOMAIN})...")
        try:
            ldata = fetch_json("/admin/locations.json", headers)
            for idx, loc in enumerate(ldata.get("locations", [])):
                raw_id = str(loc["id"])
                # Sapo Go tra ten chi nhanh trong truong 'label' hoac 'name'
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
        print(f"[*] Da quet tong cong {len(all_products)} san pham tren Sapo.")
    except Exception as err:
        print(f"[!] Khong ket noi duoc Sapo API ({err}). Giu nguyen du lieu cu an toan!")
        return

    if not all_products:
        return

    grouped_by_code = {}
    for p in all_products:
        variants_raw = p.get("variants", [])
        if not variants_raw:
            continue
        base_code = extract_base_code_from_sapo_product(p, sale_rules)
        if filter_only_bf and base_code not in sale_rules:
            continue

        rule = sale_rules.get(base_code, {})
        first_v = variants_raw[0]
        sapo_price = parse_money(first_v.get("variant_retail_price") or first_v.get("price") or 0)
        orig_price = sapo_price if sapo_price > 0 else (rule.get("origPrice") or 299000)

        sale_price = rule.get("salePrice", 0)
        disc_pct = rule.get("discountPct", 30)
        if sale_price <= 0 and disc_pct > 0:
            sale_price = int(round(orig_price * (100 - disc_pct) / 1000.0) * 1000)
        if orig_price > 0 and 0 < sale_price < orig_price:
            disc_pct = max(1, round((1.0 - sale_price / orig_price) * 100))

        img_url = ""
        if p.get("images"):
            img_url = p["images"][0].get("full_path") or p["images"][0].get("path") or ""

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
            if not grouped_by_code[key]["image"] and img_url:
                grouped_by_code[key]["image"] = img_url
        else:
            grouped_by_code[key] = {
                "sku": key,
                "name": pname_nfc,
                "subType": sub_type,
                "category": infer_category(key, pname_nfc, sub_type),
                "origPrice": orig_price,
                "salePrice": sale_price,
                "discountPct": disc_pct,
                "image": img_url,
                "variants": v_list,
            }

    custom_media = scan_repo_media_folders(list(grouped_by_code.keys()))
    for code, custom_img in custom_media.items():
        if code in grouped_by_code:
            grouped_by_code[code]["image"] = custom_img

    products_out = list(grouped_by_code.values())
    warehouses_sorted = [
        {k: v for k, v in item.items() if k != "order"}
        for item in sorted(loc_meta.values(), key=lambda x: x["order"])
    ]

    payload = {
        "synced_at": vn_now_str(),
        "warehouses": warehouses_sorted,
        "products": products_out,
    }

    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if os.path.exists("index.html") and products_out:
        with open("index.html", "r", encoding="utf-8") as f:
            html_src = f.read()
        s_i = html_src.find("/* [BLOCK:DATA_CONFIG_START] */")
        e_i = html_src.find("/* [BLOCK:DATA_CONFIG_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_CONFIG_END] */")
            wh_js = json.dumps(warehouses_sorted, ensure_ascii=False, indent=6)
            pr_js = json.dumps(products_out, ensure_ascii=False, indent=6)
            new_blk = f"/* [BLOCK:DATA_CONFIG_START] */\n    let WAREHOUSE_CONFIG = {wh_js};\n    let PRODUCTS = {pr_js};\n    /* [BLOCK:DATA_CONFIG_END] */"
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html_src[:s_i] + new_blk + html_src[e_i:])

    print(f"[+] Hoan tat Auto-Sync v10: {len(products_out)} mau Black Friday & 5 kho Sapo luc {payload['synced_at']}!")


if __name__ == "__main__":
    main()
