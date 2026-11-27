import os, re, sys, glob, json, shutil, datetime, concurrent.futures, subprocess, unicodedata
import urllib.error, urllib.parse, urllib.request
import zipfile as zf_mod

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v38 - DAY AO THUN ATH LEN TRUOC NOI BAT + GON LOC MUC GIA)
# Mo mac dinh tab ATH dau tien, an nut gia le o ATH, gom 1 hop chon gon o BF
# ==============================================================================

MIN_TOTAL_STOCK = 3
MIN_VARIANT_STOCK = 3
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
FORCE_RESET_TO_SAPO_SKUS = {"SAT532", "CV538"}
PRICE_PATCH_BF = {
    "AO1010": (194000, 79000, 59),
    "AO726":  (225000, 39000, 83),
    "AO1016": (214000, 79000, 63),
}
SUFFIX_COLOR_MAP = {
    "D": "Đen", "DEN": "Đen", "T": "Trắng", "TR": "Trắng", "XDU": "Xanh Dương",
    "XDE": "Xanh Đen", "XLA": "Xanh Lá", "XA": "Xám", "XM": "Xám", "H": "Hồng",
    "V": "Vàng", "N": "Nâu", "K": "Kem", "G": "Ghi", "DO": "Đỏ", "TI": "Tím",
    "R": "Rêu", "REU": "Rêu", "B": "Be", "BE": "Be", "C": "Cam",
}
SAPO_IMG_RX = re.compile(r"(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)", re.I)
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".jfif", ".avif", ".gif", ".bmp")
VID_EXTS = (".mp4", ".mov", ".webm", ".m4v")
SKIP_DIRS = {".git", ".github", "functions", "__pycache__", "node_modules"}
PK_KNOWN_SUBTYPES = {
    "PK822": "Mũ", "PK887": "Túi xách", "PK893": "Mũ", "PK894": "Mũ",
    "PK897": "Phụ kiện", "PK898": "Túi xách", "PK976": "Túi xách", "PK995": "Túi xách",
    "PK997": "Túi xách", "PK999": "Túi xách", "PK1006": "Giày", "PK1027": "Túi xách", "PK1032": "Giày",
}

CF_STOCK_JS_V38 = r'''// Cloudflare Pages Function: /api/stock (v38 ATH Priority + Single ATH Price)
const MIN_TOTAL_STOCK = 3;
const MIN_VARIANT_STOCK = 3;
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
const FORCE_SAPO_SKUS = new Set(["SAT532", "CV538"]);
const GITHUB_REPO_SLUG = "moonchuyensi-cmd/havana-sale";

function filterVars(arr) {
  if (!Array.isArray(arr)) return [];
  const ord = [], mp = {};
  for (const v of arr) {
    if (!v) continue;
    const sz = String(v.size || "Freesize").trim() || "Freesize";
    if (!mp[sz]) { ord.push(sz); mp[sz] = {}; }
    for (const [w, q] of Object.entries(v.stocks || {})) {
      mp[sz][w] = (mp[sz][w] || 0) + Math.max(0, Number(q) || 0);
    }
  }
  return ord.filter((sz) => Object.values(mp[sz]).reduce((a, b) => a + b, 0) >= MIN_VARIANT_STOCK)
            .map((sz) => ({ size: sz, stocks: mp[sz] }));
}

function calcTot(p) {
  return (p && Array.isArray(p.variants))
    ? p.variants.reduce((s, v) => s + Object.values(v.stocks || {}).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0), 0)
    : 0;
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const baseAsset = await env.ASSETS.fetch(new URL("/sapo_5_kho.json", request.url));
  if (!baseAsset.ok) {
    return new Response(JSON.stringify({ error: "Missing sapo_5_kho.json" }), {
      status: 404, headers: { "content-type": "application/json; charset=utf-8" }
    });
  }
  const payload = await baseAsset.json();
  let saleRules = null;
  try {
    const rAsset = await env.ASSETS.fetch(new URL("/sale_rules.json", request.url));
    if (rAsset.ok) saleRules = await rAsset.json();
  } catch (_) {}

  try {
    const ghRes = await fetch(`https://api.github.com/repos/${GITHUB_REPO_SLUG}/releases/tags/live-stock`, {
      headers: { "Accept": "application/vnd.github+json", "User-Agent": "havana-cf-v38" }
    });
    if (ghRes.ok) {
      const rel = await ghRes.json();
      const feed = rel && rel.body ? JSON.parse(rel.body) : null;
      if (feed && feed.items && Array.isArray(feed.warehouses)) {
        const fEp = Number(feed.synced_epoch || 0), bEp = Number(payload.synced_epoch || 0);
        if (fEp >= bEp && Array.isArray(payload.products)) {
          const whIds = feed.warehouses.map((w) => w.id);
          payload.warehouses = feed.warehouses;
          const seen = new Set();
          for (const it of payload.products) {
            const c = String(it.sku || "").trim().toUpperCase().split("-")[0];
            seen.add(c);
            const fe = feed.items[c];
            if (fe && Array.isArray(fe.v)) {
              const built = fe.v.map(([sz, qa]) => {
                const st = {};
                whIds.forEach((w, i) => { st[w] = Number((qa && qa[i]) || 0); });
                return { size: sz || "Freesize", stocks: st };
              });
              if (built.length > 0 || !it.excel_override) it.variants = built;
              if (fe.n) it.name = fe.n;
              if (Array.isArray(fe.si) && fe.si.length) it.sapoImages = fe.si;
            }
          }
          if (saleRules) {
            for (const [c, fe] of Object.entries(feed.items)) {
              if (!seen.has(c) && saleRules[c] && !EXCLUDED_SKUS.has(c) && Array.isArray(fe.v) && fe.v.length) {
                const r = saleRules[c];
                const isAth = r.section === "ATH" || Boolean(r.singlePrice);
                const sp = r.salePrice || r.origPrice || 159000;
                const op = isAth ? sp : (r.origPrice || sp);
                const built = fe.v.map(([sz, qa]) => {
                  const st = {};
                  whIds.forEach((w, i) => { st[w] = Number((qa && qa[i]) || 0); });
                  return { size: sz || "Freesize", stocks: st };
                });
                payload.products.push({
                  sku: c, name: fe.n || ("Áo thun Havana " + c), subType: r.subType || "",
                  section: isAth ? "ATH" : "BF", singlePrice: isAth,
                  origPrice: op, salePrice: sp, discountPct: isAth ? 0 : (r.discountPct || 30),
                  image: "", images: [], sapoImages: fe.si || [], video: "", sizeChart: "", variants: built
                });
              }
            }
          }
          payload.synced_at = feed.synced_at || payload.synced_at;
          payload.synced_epoch = fEp || bEp;
          payload.sapo_cookie_expired = Boolean(feed.cookie_expired);
        }
      }
    }
  } catch (_) {}

  if (Array.isArray(payload.products)) {
    payload.products = payload.products.filter((p) => {
      const c = String(p.sku || "").trim().toUpperCase().split("-")[0];
      if (!c || EXCLUDED_SKUS.has(c)) return false;
      if (saleRules && saleRules[c]) {
        const r = saleRules[c];
        const isAth = r.section === "ATH" || Boolean(r.singlePrice);
        p.section = isAth ? "ATH" : "BF";
        p.singlePrice = isAth;
        if (isAth) {
          const athP = Number(r.salePrice || r.origPrice || p.salePrice || p.origPrice || 0);
          if (athP > 0) { p.origPrice = athP; p.salePrice = athP; }
          p.discountPct = 0;
        } else {
          if (r.origPrice > 0) p.origPrice = r.origPrice;
          if (r.salePrice > 0) p.salePrice = r.salePrice;
          if (p.origPrice > p.salePrice && p.salePrice > 0) {
            p.discountPct = Math.max(1, Math.round((1 - p.salePrice / p.origPrice) * 100));
          }
        }
      }
      if (FORCE_SAPO_SKUS.has(c)) {
        const sImgs = Array.isArray(p.sapoImages) ? p.sapoImages : [];
        p.images = sImgs; p.image = sImgs[0] || ""; p.video = ""; p.sizeChart = "";
      }
      p.variants = filterVars(p.variants);
      return p.variants.length > 0 && calcTot(p) >= MIN_TOTAL_STOCK;
    });
  }
  payload.sync_source = "v38_ath_first";
  return new Response(JSON.stringify(payload), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store, max-age=0" }
  });
}
'''

V38_INDEX_JS_ADDON = r'''/* === v38: ATH FIRST & PROMINENT + ULTRA-CLEAN PRICE FILTER === */
    const MIN_STOCK_V38 = 3;
    const FORCE_SAPO_V38 = new Set(["SAT532", "CV538"]);
    const FINAL_DEADLINE_V38 = new Date("2026-11-27T23:59:59+07:00").getTime();
    const BILL_NOTE_VI_V38 = "📌 Lưu ý: Chương trình chỉ áp dụng cho Online. Hàng số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";
    const BILL_NOTE_EN_V38 = "📌 Note: Valid for Online orders only. Limited stock may slightly differ from actual inventory.";
    // Day muc AO THUN ATH len truoc va mo mac dinh ngay khi khach vao link
    let currentMainSection = (location.hash.toLowerCase() === "#bf" || location.search.toLowerCase().includes("tab=bf")) ? "BF" : "ATH";
    let v38SyncBusy = false;

    if (!document.getElementById("v38CleanUiStyle")) {
      const st = document.createElement("style");
      st.id = "v38CleanUiStyle";
      st.textContent = `
        .v38-title-wrap{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:3px}
        #mainBrandTitle,#subBrandText{color:#be123c!important;font-weight:800!important}
        .v38-countdown-pill{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#e11d48 0%,#9f1239 100%);color:#fff;padding:4px 10px;border-radius:999px;box-shadow:0 4px 14px rgba(225,29,72,.38);border:1px solid rgba(255,255,255,.35);font-variant-numeric:tabular-nums;animation:v38Pulse 1.9s ease-in-out infinite;white-space:nowrap}
        .v38-cd-label{font-size:11px;font-weight:800;letter-spacing:.02em;color:#ffe4e6;text-transform:uppercase}
        .v38-cd-digits{display:inline-flex;align-items:center;gap:3px}
        .v38-cd-box{background:rgba(15,23,42,.44);color:#fff;font-weight:800;font-size:12.5px;min-width:24px;height:21px;padding:0 4px;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,.24);font-variant-numeric:tabular-nums}
        .v38-cd-sep{font-weight:800;font-size:12px;color:#fecdd3}
        @keyframes v38Pulse{0%,100%{transform:scale(1);box-shadow:0 4px 14px rgba(225,29,72,.34)}50%{transform:scale(1.025);box-shadow:0 6px 20px rgba(225,29,72,.58)}}

        /* Thanh 2 Muc Chinh: ATH dung dau va noi bat ruc ro */
        .v38-main-tabs{display:grid;grid-template-columns:1.12fr 0.88fr;gap:10px;margin:16px 0 10px;padding:6px;border-radius:20px;background:rgba(255,255,255,.95);border:1.5px solid rgba(145,210,206,.85);box-shadow:0 12px 28px rgba(22,89,84,.09)}
        .v38-main-tab{position:relative;overflow:hidden;border:1.5px solid transparent;border-radius:14px;padding:13px 16px;font-size:15.5px;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;background:var(--havana-mint-soft);color:var(--havana-teal-dark);transition:all .2s ease;white-space:nowrap;font-variant-numeric:tabular-nums}
        .v38-main-tab:hover{transform:translateY(-1px)}

        /* Tab AO THUN ATH luon noi bat ke ca khi dang o trang thai cho */
        #tabBtnATH{border-color:rgba(225,29,72,.36);background:linear-gradient(135deg,#fff1f2 0%,#ffe4e6 100%);color:#9f1239;box-shadow:0 4px 14px rgba(225,29,72,.12)}
        #tabBtnATH.active-ath{border-color:#fda4af;background:linear-gradient(135deg,#f43f5e 0%,#e11d48 52%,#be123c 100%);color:#fff;box-shadow:0 8px 24px rgba(225,29,72,.38);transform:translateY(-1px)}
        #tabBtnATH::after{content:"";position:absolute;top:0;left:-120%;width:65%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.36),transparent);transform:skewX(-22deg);animation:v38Sheen 3.4s ease-in-out infinite;pointer-events:none}
        @keyframes v38Sheen{0%{left:-120%}48%,100%{left:160%}}

        .v38-hot-badge{display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:900;letter-spacing:.04em;background:#e11d48;color:#fff;text-transform:uppercase;box-shadow:0 2px 6px rgba(225,29,72,.28)}
        #tabBtnATH.active-ath .v38-hot-badge{background:#fef08a;color:#9f1239;box-shadow:0 2px 8px rgba(0,0,0,.16)}

        #tabBtnBF.active-bf{background:linear-gradient(135deg,#247c76,#165954);color:#fff;border-color:#247c76;box-shadow:0 7px 20px rgba(22,89,84,.28)}

        .v38-tab-cnt{display:inline-flex;align-items:center;justify-content:center;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:800;background:rgba(15,23,42,.09);color:inherit;font-variant-numeric:tabular-nums}
        #tabBtnATH.active-ath .v38-tab-cnt,#tabBtnBF.active-bf .v38-tab-cnt{background:rgba(255,255,255,.24);color:#fff}

        /* Gon gang tuyet doi khu vuc loc gia: 1 hang duy nhat, khong bao gio ep o tim kiem */
        #tierFilters.v38-tier-hidden{display:none!important}
        #tierFilters.v38-tier-compact{display:inline-flex!important;flex:0 0 auto!important;width:auto!important;order:2!important;padding:0!important;overflow:visible!important}
        .toolbar-top-row>.filter-group:last-child{order:3!important;margin-left:0!important}
        .v38-price-select{appearance:none;-webkit-appearance:none;height:39px;padding:0 32px 0 12px;border-radius:12px;border:1.5px solid rgba(36,124,118,.28);background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23165954' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E") no-repeat right 10px center;color:var(--havana-teal-dark);font-size:13px;font-weight:800;cursor:pointer;outline:none;font-variant-numeric:tabular-nums;box-shadow:0 2px 6px rgba(22,89,84,.05);transition:border-color .18s,box-shadow .18s}
        .v38-price-select.is-filtered{background-color:var(--havana-mint-soft);border-color:var(--havana-teal);color:#0d3b38}
        .v38-price-select:focus{border-color:var(--havana-teal);box-shadow:0 0 0 3px rgba(145,210,206,.35)}

        @media(max-width:768px){
          .brand>div>div:last-child{flex-wrap:wrap!important;row-gap:3px!important}
          .brand-title{font-size:14.5px!important}
          .v38-title-wrap{gap:6px}
          .v38-countdown-pill{padding:3px 8px;gap:4px}
          .v38-cd-label{font-size:10px}
          .v38-cd-box{font-size:11px;min-width:20px;height:18px;padding:0 3px;border-radius:5px}
          .v38-main-tabs{grid-template-columns:1.08fr 0.92fr;margin:10px 0 6px;padding:5px;gap:6px;border-radius:16px}
          .v38-main-tab{padding:10px 8px;font-size:12.5px;gap:5px;border-radius:11px}
          .v38-hot-badge{padding:1px 5px;font-size:9px}
          .v38-tab-cnt{padding:1px 6px;font-size:11px}
          .v38-price-select{height:35px!important;padding:0 26px 0 9px!important;font-size:16px!important;border-radius:10px!important}
          .toolbar{padding-bottom:8px!important}
        }
      `;
      document.head.appendChild(st);
    }

    const v38IsAthItem = (p) => {
      if (!p) return false;
      const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
      const r = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
      return Boolean(p.section === "ATH" || p.singlePrice || (r && (r.section === "ATH" || r.singlePrice)));
    };

    const v38EnsureMainTabsDOM = () => {
      const toolbarEl = document.querySelector("main .toolbar");
      if (!toolbarEl) return;
      let navDiv = document.getElementById("mainCatalogTabs");
      if (!navDiv || navDiv.dataset.ver !== "38") {
        if (navDiv) navDiv.remove();
        navDiv = document.createElement("div");
        navDiv.id = "mainCatalogTabs";
        navDiv.dataset.ver = "38";
        navDiv.className = "v38-main-tabs";
        navDiv.innerHTML = `
          <button type="button" class="v38-main-tab" id="tabBtnATH" onclick="setMainCatalogSection('ATH')">
            <span class="v38-hot-badge" id="tabHotATH">MỚI</span>
            <span id="tabLblATH">✨ ÁO THUN ATH</span>
            <span class="v38-tab-cnt" id="tabCntATH">0</span>
          </button>
          <button type="button" class="v38-main-tab" id="tabBtnBF" onclick="setMainCatalogSection('BF')">
            <span id="tabLblBF">🔥 BLACK FRIDAY</span>
            <span class="v38-tab-cnt" id="tabCntBF">0</span>
          </button>
        `;
        toolbarEl.parentNode.insertBefore(navDiv, toolbarEl);
      }

      const pool = (PRODUCTS || []).filter((p) => !onlyInStock || getProductTotal(p) >= MIN_STOCK_V38);
      const athCount = pool.filter((p) => v38IsAthItem(p)).length;
      const bfCount = pool.length - athCount;

      const btnATH = document.getElementById("tabBtnATH"), btnBF = document.getElementById("tabBtnBF");
      const cntATH = document.getElementById("tabCntATH"), cntBF = document.getElementById("tabCntBF");
      const lblATH = document.getElementById("tabLblATH"), lblBF = document.getElementById("tabLblBF");
      const hotATH = document.getElementById("tabHotATH");
      if (hotATH) hotATH.textContent = currentLang === "EN" ? "NEW" : "MỚI";
      if (lblATH) lblATH.textContent = currentLang === "EN" ? "✨ ATH T-SHIRTS" : "✨ ÁO THUN ATH";
      if (lblBF) lblBF.textContent = currentLang === "EN" ? "🔥 BLACK FRIDAY" : "🔥 BLACK FRIDAY";
      if (cntATH) cntATH.textContent = String(athCount);
      if (cntBF) cntBF.textContent = String(bfCount);
      if (btnATH) btnATH.classList.toggle("active-ath", currentMainSection === "ATH");
      if (btnBF) btnBF.classList.toggle("active-bf", currentMainSection === "BF");

      const catRow = document.getElementById("catFilters");
      if (catRow) catRow.style.display = currentMainSection === "ATH" ? "none" : "";

      const searchEl = document.getElementById("searchInput");
      if (searchEl) {
        const compact = window.innerWidth <= 768;
        if (currentMainSection === "ATH") {
          searchEl.placeholder = currentLang === "EN"
            ? (compact ? "Search ATH SKU or name..." : "Search ATH T-Shirts (e.g. ATH977, ATH945, ATH941)...")
            : (compact ? "Tìm mã ATH (ATH977, ATH945...)..." : "Tìm nhanh mã Áo Thun ATH (VD: ATH977, ATH945, ATH941...) hoặc tên mẫu...");
        } else {
          searchEl.placeholder = currentLang === "EN"
            ? (compact ? "Search SKU or name..." : "Search Black Friday SKU (e.g. CV589, LEN389, AO1016)...")
            : (compact ? "Tìm mã (CV589, LEN389...)..." : "Tìm theo mã Black Friday (VD: CV589, LEN389, AO1016, PK...) hoặc tên mẫu...");
        }
      }
    };

    window.setMainCatalogSection = function(sec) {
      currentMainSection = sec === "BF" ? "BF" : "ATH";
      currentCategory = "ALL";
      currentPriceTier = "ALL";
      try {
        history.replaceState(null, "", currentMainSection === "BF" ? "#bf" : location.pathname + location.search);
      } catch (_) {}
      renderCatalog();
    };

    const v38EnsureCountdown = () => {
      const mainT = document.getElementById("mainBrandTitle");
      if (!mainT) return;
      let wrap = mainT.parentElement;
      if (!wrap || !wrap.classList.contains("v38-title-wrap")) {
        wrap = document.createElement("div");
        wrap.className = "v38-title-wrap";
        mainT.parentNode.insertBefore(wrap, mainT);
        wrap.appendChild(mainT);
      }
      if (!document.getElementById("v38CountdownBadge")) {
        const old37 = document.getElementById("v37CountdownBadge");
        if (old37) old37.remove();
        const b = document.createElement("div");
        b.id = "v38CountdownBadge";
        b.className = "v38-countdown-pill";
        b.innerHTML = `<span class="v38-cd-label" id="v38CdLabel">⏳ Kết thúc sau</span><span class="v38-cd-digits"><span class="v38-cd-box" id="v38CdH">00</span><span class="v38-cd-sep">:</span><span class="v38-cd-box" id="v38CdM">00</span><span class="v38-cd-sep">:</span><span class="v38-cd-box" id="v38CdS">00</span></span>`;
        wrap.appendChild(b);
      }
    };

    const v38TickCountdown = () => {
      v38EnsureCountdown();
      const now = Date.now();
      let targetMs = FINAL_DEADLINE_V38;
      if (now > targetMs) {
        const vn = new Date(now + 7 * 3600000);
        targetMs = Date.UTC(vn.getUTCFullYear(), vn.getUTCMonth(), vn.getUTCDate(), 23, 59, 59) - 7 * 3600000;
      }
      const diff = Math.max(0, Math.floor((targetMs - now) / 1000));
      const pad = (n) => String(n).padStart(2, "0");
      const hEl = document.getElementById("v38CdH"), mEl = document.getElementById("v38CdM"), sEl = document.getElementById("v38CdS"), lEl = document.getElementById("v38CdLabel");
      if (hEl) hEl.textContent = pad(Math.floor(diff / 3600));
      if (mEl) mEl.textContent = pad(Math.floor((diff % 3600) / 60));
      if (sEl) sEl.textContent = pad(diff % 60);
      if (lEl) lEl.textContent = currentLang === "EN" ? "⏳ Ends in" : "⏳ Kết thúc sau";
    };

    if (typeof RESET_TO_SAPO_SKUS_V21 !== "undefined" && RESET_TO_SAPO_SKUS_V21 && RESET_TO_SAPO_SKUS_V21.add) {
      RESET_TO_SAPO_SKUS_V21.add("CV538");
      RESET_TO_SAPO_SKUS_V21.add("SAT532");
    }
    try { ["havana_gh_tree_v13","havana_live_rel_v36","havana_live_rel_v37"].forEach((k) => sessionStorage.removeItem(k)); } catch (_) {}
    if (typeof MEDIA_CACHE_VERSION !== "undefined" && MEDIA_CACHE_VERSION < 38) MEDIA_CACHE_VERSION = 38;

    const v38ApplyBranding = () => {
      v38EnsureCountdown();
      v38EnsureMainTabsDOM();
      const en = currentLang === "EN";
      const mainT = document.getElementById("mainBrandTitle"), subT = document.getElementById("subBrandText");
      const badgeEl = document.querySelector("#billModal .bill-head-badge"), titleEl = document.getElementById("billModalTitle");
      const openBtn = document.getElementById("openBillBtn"), noteInp = document.getElementById("custNoteInput");
      if (mainT) mainT.textContent = en ? "FINAL DAY CLEARANCE" : "XẢ NGÀY CUỐI";
      if (subT) subT.textContent = en ? "(Online Exclusive Only)" : "(Chỉ áp dụng cho Online)";
      if (badgeEl) badgeEl.textContent = en ? "HAVANA ONLINE • ORDER SLIP" : "HAVANA ONLINE • CHỈ ÁP DỤNG CHO ONLINE";
      if (titleEl) titleEl.textContent = en ? "Provisional Order Slip" : "Phiếu Lên Đơn Tạm";
      if (openBtn) openBtn.textContent = en ? "Draft Order & Send" : "Xem Đơn Tạm & Gửi Zalo";
      if (noteInp) noteInp.placeholder = en ? "Shipping address & order note (Online only)..." : "Địa chỉ nhận ship & Ghi chú đơn hàng (Chỉ áp dụng cho Online)...";
      document.title = "HAVANA — Áo Thun ATH & Sale Black Friday (Online)";
      v38TickCountdown();
    };

    const _preV38BuildCard = buildCardInnerHtml;
    buildCardInnerHtml = function(p, idxInList) {
      let html = _preV38BuildCard(p, idxInList);
      const finalPrice = getProductSalePrice(p);
      const isSingle = Boolean(v38IsAthItem(p) || !(Number(p.origPrice) > finalPrice));
      if (isSingle) {
        html = html.replace(/<span class="discount-badge">[^<]*<\/span>/, `<span class="discount-badge" style="background:linear-gradient(135deg,#165954,#247c76);font-size:11.5px;box-shadow:0 4px 10px rgba(22,89,84,.25);">ÁO THUN ATH</span>`);
        html = html.replace(/<span class="price-orig">[^<]*<\/span>/, "");
        const tagLbl = currentLang === "EN" ? "Online Price" : "Giá ATH";
        html = html.replace(/<span class="save-tag">[^<]*<\/span>/, `<span class="save-tag" style="color:var(--havana-teal-dark);background:#fff;">${tagLbl}</span>`);
      }
      return html;
    };

    renderDynamicCategoryBar = function() {
      const wrap = document.getElementById("catFilters");
      if (!wrap) return;
      if (currentMainSection === "ATH") {
        wrap.style.display = "none";
        return;
      }
      wrap.style.display = "";
      const basePool = PRODUCTS.filter((p) => !v38IsAthItem(p) && (!onlyInStock || getProductTotal(p) >= MIN_STOCK_V38));
      const counts = {};
      basePool.forEach((p) => {
        const cid = classifyProduct(p);
        counts[cid] = (counts[cid] || 0) + 1;
      });
      if (currentCategory !== "ALL" && !counts[currentCategory]) currentCategory = "ALL";
      const activeDefs = CATEGORY_DEFS.filter((d) => (counts[d.id] || 0) > 0);
      const allLabel = currentLang === "EN" ? "All" : "Tất cả";
      let html = `<button type="button" class="pill-btn ${currentCategory === "ALL" ? "active" : ""}" onclick="setCategory('ALL')">${allLabel}<span class="cat-count-chip">${basePool.length}</span></button>`;
      html += activeDefs.map((d) => {
        const lbl = currentLang === "EN" ? d.en : d.vi;
        return `<button type="button" class="pill-btn ${currentCategory === d.id ? "active" : ""}" onclick="setCategory('${d.id}')">${lbl}<span class="cat-count-chip">${counts[d.id] || 0}</span></button>`;
      }).join("");
      wrap.innerHTML = html;
    };

    // Thiet ke lai khu vuc Moi muc gia: Loai bo hoan toan ben tab ATH, va gom 1 hop chon gon nhe ben tab BF!
    renderDynamicPriceTierBar = function() {
      const wrap = document.getElementById("tierFilters");
      if (!wrap) return;
      if (currentMainSection === "ATH") {
        currentPriceTier = "ALL";
        wrap.className = "filter-group v38-tier-hidden";
        wrap.innerHTML = "";
        return;
      }

      const pool = PRODUCTS.filter((p) => {
        if (v38IsAthItem(p)) return false;
        if (onlyInStock && getProductTotal(p) < MIN_STOCK_V38) return false;
        if (currentCategory !== "ALL" && classifyProduct(p) !== currentCategory) return false;
        return true;
      });

      const tierCounts = {};
      pool.forEach((p) => {
        const sp = getProductSalePrice(p);
        tierCounts[sp] = (tierCounts[sp] || 0) + 1;
      });
      const sortedPrices = Object.keys(tierCounts).map(Number).filter((n) => n > 0).sort((a, b) => a - b);

      if (sortedPrices.length <= 1) {
        currentPriceTier = "ALL";
        wrap.className = "filter-group v38-tier-hidden";
        wrap.innerHTML = "";
        return;
      }

      if (currentPriceTier !== "ALL" && String(currentPriceTier).startsWith("EXACT_")) {
        const targetP = Number(String(currentPriceTier).slice(6));
        if (!tierCounts[targetP]) currentPriceTier = "ALL";
      }

      wrap.className = "filter-group v38-tier-compact";
      const allTxt = currentLang === "EN" ? `💰 Price: All (${pool.length})` : `💰 Mọi mức giá (${pool.length})`;
      const isFiltered = currentPriceTier !== "ALL" ? "is-filtered" : "";
      let opts = `<option value="ALL" ${currentPriceTier === "ALL" ? "selected" : ""}>${allTxt}</option>`;
      sortedPrices.forEach((priceVal) => {
        const kLabel = Math.round(priceVal / 1000) + "K";
        const tierKey = "EXACT_" + priceVal;
        const sel = currentPriceTier === tierKey ? "selected" : "";
        const optTxt = currentLang === "EN"
          ? `💰 ${kLabel} (${tierCounts[priceVal]} items)`
          : `💰 Đồng giá ${kLabel} (${tierCounts[priceVal]} mẫu)`;
        opts += `<option value="${tierKey}" ${sel}>${optTxt}</option>`;
      });
      wrap.innerHTML = `<select class="v38-price-select ${isFiltered}" onchange="setPriceTier(this.value)" aria-label="Lọc theo mức giá">${opts}</select>`;
    };

    const _preV38CartBar = updateCartBar;
    updateCartBar = function() {
      _preV38CartBar();
      const sumTitle = document.getElementById("cartSummaryTitle");
      const savTxt = document.getElementById("cartSavingsText");
      const { totalSaved } = getCartTotals();
      if (sumTitle && currentLang === "VI" && sumTitle.textContent.startsWith("Phiếu Bill:")) {
        sumTitle.textContent = sumTitle.textContent.replace("Phiếu Bill:", "Đơn tạm:");
      }
      if (savTxt && totalSaved <= 0) {
        savTxt.textContent = currentLang === "EN" ? "Online Order Slip" : "Bấm xem chi tiết & gửi chốt Zalo";
      }
      v38ApplyBranding();
    };

    const _preV38RenderBill = renderBillModal;
    renderBillModal = function() {
      _preV38RenderBill();
      v38ApplyBranding();
      const itemRows = document.querySelectorAll("#billItemsContainer .bill-item-row");
      itemRows.forEach((row, idx) => {
        const cItem = cart[idx];
        if (cItem && !(Number(cItem.origPrice) > Number(cItem.finalPrice))) {
          const metaEl = row.querySelector(".bill-item-meta");
          if (metaEl) {
            metaEl.innerHTML = metaEl.innerHTML.replace("Đơn giá Sale:", "Đơn giá:");
            metaEl.querySelectorAll("span").forEach((sp) => {
              if ((sp.getAttribute("style") || "").includes("line-through")) sp.remove();
            });
          }
        }
      });
      const sumEl = document.getElementById("billSummaryBox");
      if (sumEl) {
        let nd = sumEl.querySelector(".bill-stock-disclaimer");
        if (!nd) {
          nd = document.createElement("div");
          nd.className = "bill-stock-disclaimer";
          nd.style.cssText = "margin-top:8px;padding:9px 12px;border-radius:11px;background:#fffbeb;border:1px dashed #f59e0b;color:#92400e;font-size:12.5px;line-height:1.45;font-weight:600;";
          sumEl.appendChild(nd);
        }
        nd.textContent = currentLang === "EN" ? BILL_NOTE_EN_V38 : BILL_NOTE_VI_V38;
      }
    };

    const _preV38BillTxt = buildFormattedBillText;
    buildFormattedBillText = function() {
      let txt = _preV38BillTxt().replace(/🧾 PHIẾU [^\n]+/, "🧾 PHIẾU LÊN ĐƠN TẠM — HAVANA (CHỈ ÁP DỤNG CHO ONLINE)");
      txt = txt.replace(/\n--------------------------\n📌 [^\n]+$/, "");
      return txt + "\n--------------------------\n" + (currentLang === "EN" ? BILL_NOTE_EN_V38 : BILL_NOTE_VI_V38);
    };

    const v38Prices = (rm) => {
      if (!rm || typeof rm !== "object") return;
      const fx = { AO1010: { origPrice: 194000, salePrice: 79000, discountPct: 59 }, AO726: { origPrice: 225000, salePrice: 39000, discountPct: 83 }, AO1016: { origPrice: 214000, salePrice: 79000, discountPct: 63 } };
      Object.keys(fx).forEach((k) => { if (rm[k] && rm[k].section !== "ATH") Object.assign(rm[k], fx[k]); });
    };

    const v38Vars = (arr) => {
      if (!Array.isArray(arr)) return [];
      const ord = [], mp = {};
      arr.forEach((v) => {
        if (!v) return;
        const sz = String(v.size || "Freesize").trim() || "Freesize";
        if (!mp[sz]) { ord.push(sz); mp[sz] = {}; }
        Object.entries(v.stocks || {}).forEach(([w, q]) => { mp[sz][w] = (mp[sz][w] || 0) + Math.max(0, Number(q) || 0); });
      });
      return ord.filter((sz) => Object.values(mp[sz]).reduce((a, b) => a + b, 0) >= MIN_STOCK_V38).map((sz) => ({ size: sz, stocks: mp[sz] }));
    };

    const v38Sanitize = (list) => {
      if (!Array.isArray(list)) return [];
      const out = [];
      list.forEach((p) => {
        if (!p) return;
        const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
        if (!code) return;
        const r = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
        const isAth = Boolean(p.section === "ATH" || p.singlePrice || (r && (r.section === "ATH" || r.singlePrice)));
        p.section = isAth ? "ATH" : "BF";
        p.singlePrice = isAth;
        if (isAth) {
          const athP = Number((r && (r.salePrice || r.origPrice)) || p.salePrice || p.origPrice || 0);
          if (athP > 0) { p.origPrice = athP; p.salePrice = athP; }
          p.discountPct = 0;
        } else if (r) {
          if (r.origPrice > 0) p.origPrice = Number(r.origPrice);
          if (r.salePrice > 0) p.salePrice = Number(r.salePrice);
          p.discountPct = getProductDiscountPct(p);
        }
        if (FORCE_SAPO_V38.has(code) && !(typeof sessionFreshUploadedSkus !== "undefined" && sessionFreshUploadedSkus && sessionFreshUploadedSkus.has(code))) {
          if (typeof repoLiveMediaBySku === "object" && repoLiveMediaBySku) delete repoLiveMediaBySku[code];
          const urls = [...(p.sapoImages || []), ...(p.images || []), p.image].map((u) => String(u || "").trim()).filter((s) => s && isSapoImageUrl(s) && !(code === "SAT532" && s.toLowerCase().includes("sat533")));
          const uniq = [...new Set(urls)];
          p.sapoImages = uniq; p.images = uniq; p.image = uniq[0] || ""; p.video = ""; p.sizeChart = ""; p.hasCustomMedia = false;
        }
        p.variants = v38Vars(p.variants);
        const tot = p.variants.reduce((s, v) => s + Object.values(v.stocks || {}).reduce((a, b) => a + b, 0), 0);
        if (p.variants.length > 0 && tot >= MIN_STOCK_V38) out.push(p);
      });
      return out;
    };

    ensureDefaultSelectedSizes = function() {
      if (!Array.isArray(PRODUCTS)) return;
      PRODUCTS.forEach((p) => {
        if (!p.variants || !p.variants.length) { delete selectedVariantBySku[p.sku]; return; }
        const cur = p.variants.find((v) => v.size === selectedVariantBySku[p.sku]);
        if (!cur || getVariantTotal(cur) < MIN_STOCK_V38) selectedVariantBySku[p.sku] = p.variants[0].size;
      });
    };

    const v38Enforce = () => {
      v38Prices(SALE_RULES_MASTER);
      if (Array.isArray(BASELINE_PRODUCTS)) BASELINE_PRODUCTS = v38Sanitize(BASELINE_PRODUCTS);
      if (Array.isArray(PRODUCTS)) PRODUCTS = v38Sanitize(PRODUCTS);
      ensureDefaultSelectedSizes();
      v38ApplyBranding();
      const stTxt = document.getElementById("onlyInStockText");
      if (stTxt) stTxt.textContent = currentLang === "VI" ? "Tồn ≥ 3" : "Stock ≥ 3";
      if (Array.isArray(cart) && cart.length) {
        let adj = false;
        cart = cart.filter((c) => {
          const mp = (PRODUCTS || []).find((x) => x.sku === c.sku);
          if (!mp) { adj = true; return false; }
          if (c.origPrice !== mp.origPrice || c.finalPrice !== getProductSalePrice(mp)) {
            c.origPrice = mp.origPrice;
            c.finalPrice = getProductSalePrice(mp);
            c.discountPct = v38IsAthItem(mp) ? 0 : getProductDiscountPct(mp);
            adj = true;
          }
          const mx = getMaxStockForSkuSize(c.sku, c.size);
          if (mx < MIN_STOCK_V38) { adj = true; return false; }
          if (c.qty > mx) { c.qty = mx; adj = true; }
          return true;
        });
        if (adj) { persistCart(); _preV38CartBar(); }
      }
    };

    const _preV38Merge = mergeAndApplySaleRules;
    mergeAndApplySaleRules = function(liveList, rulesObj) {
      v38Prices(SALE_RULES_MASTER); v38Prices(rulesObj);
      const cl = v38Sanitize(Array.isArray(liveList) ? liveList : []);
      const br = (rulesObj && Object.keys(rulesObj).length) ? rulesObj : SALE_RULES_MASTER;
      const sc = {};
      if (br) { const ks = new Set(cl.map((x) => normalizeBaseCode(String(x?.sku || "").split("-")[0]))); Object.keys(br).forEach((k) => { if (ks.has(k)) sc[k] = br[k]; }); }
      return v38Sanitize(_preV38Merge(cl, sc));
    };

    renderCatalog = function() {
      if (typeof purgeExcludedSkusEverywhere === "function") purgeExcludedSkusEverywhere();
      v38Enforce();
      syncMobileGridClass();
      renderDynamicCategoryBar();
      renderDynamicPriceTierBar();
      stripSapoImagesFromProducts(PRODUCTS);
      updateAdminMediaStats();

      const q = (document.getElementById("searchInput")?.value || "").trim().toLowerCase();
      const grid = document.getElementById("productGrid");
      const sentinel = document.getElementById("scrollSentinel");
      const wantAth = (currentMainSection === "ATH");

      filteredProductsCache = PRODUCTS.filter((p) => {
        if (v38IsAthItem(p) !== wantAth) return false;
        if (document.body.classList.contains("is-admin")) {
          if (adminMediaFilter === "MISSING" && hasCustomProductMedia(p)) return false;
          if (adminMediaFilter === "HAS_MEDIA" && !hasCustomProductMedia(p)) return false;
        }
        if (!wantAth && currentCategory !== "ALL" && classifyProduct(p) !== currentCategory) return false;
        const sPrice = getProductSalePrice(p);
        if (!wantAth && currentPriceTier !== "ALL" && String(currentPriceTier).startsWith("EXACT_")) {
          if (sPrice !== Number(String(currentPriceTier).slice(6))) return false;
        }
        if (q && !p.sku.toLowerCase().includes(q) && !p.name.toLowerCase().includes(q)) return false;
        if (onlyInStock && getProductTotal(p) < MIN_STOCK_V38) return false;
        return true;
      });

      const badgeCnt = document.getElementById("countResultBadge");
      if (badgeCnt) {
        badgeCnt.textContent = currentLang === "EN" ? `${filteredProductsCache.length} items` : `${filteredProductsCache.length} mẫu`;
      }

      if (!filteredProductsCache.length) {
        const otherTabMatches = q ? PRODUCTS.filter((p) => (v38IsAthItem(p) !== !wantAth ? false : true) && (p.sku.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)) && (!onlyInStock || getProductTotal(p) >= MIN_STOCK_V38)).length : 0;
        if (otherTabMatches > 0) {
          const targetSec = wantAth ? "BF" : "ATH";
          const targetName = wantAth ? "🔥 BLACK FRIDAY" : "✨ ÁO THUN ATH";
          grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:42px 16px;color:var(--text-main);"><div style="margin-bottom:12px;font-weight:700;">Tìm thấy <strong>${otherTabMatches} mẫu</strong> khớp từ khóa bên mục <strong>${targetName}</strong></div><button type="button" class="pill-btn active" onclick="setMainCatalogSection('${targetSec}')">Chuyển sang xem ${targetName} ngay</button></div>`;
        } else {
          grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);">Không tìm thấy mẫu nào khớp bộ lọc hiện tại.</div>`;
        }
        if (sentinel) sentinel.textContent = "";
        return;
      }

      grid.innerHTML = "";
      renderedCount = 0;
      appendNextBatch();
      ensureScrollObserver();
    };

    const _preV38Lang = setLang;
    setLang = function(l) { _preV38Lang(l); v38Enforce(); if (document.getElementById("billModal")?.classList.contains("open")) renderBillModal(); };

    refreshLiveSapoStock = async function(silent = true) {
      if (v38SyncBusy) return false;
      v38SyncBusy = true;
      const timeEl = document.getElementById("liveSyncTimeText"), badgeEl = document.getElementById("liveSyncBadge");
      const dotEl = badgeEl ? badgeEl.querySelector(".live-sync-dot") : null;
      try {
        let data = null;
        try {
          const sr = await fetch("./sale_rules.json?t=" + Date.now(), { cache: "no-store" });
          if (sr.ok) { const rr = await sr.json(); if (rr && Object.keys(rr).length >= 10) { v38Prices(rr); SALE_RULES_MASTER = rr; } }
        } catch (_) {}
        try {
          const r = await fetch("./sapo_5_kho.json?t=" + Date.now(), { cache: "no-store" });
          if (r.ok) data = await r.json();
        } catch (_) {}
        if (!data || !Array.isArray(data.products)) {
          try {
            const r = await fetch("./api/stock?t=" + Date.now(), { cache: "no-store" });
            if (r.ok) data = await r.json();
          } catch (_) {}
        }
        if (data && Array.isArray(data.products)) {
          if (Array.isArray(data.warehouses) && data.warehouses.length) WAREHOUSE_CONFIG = data.warehouses;
          const cleanLive = v38Sanitize(data.products);
          stripSapoImagesFromProducts(cleanLive);
          PRODUCTS = mergeAndApplySaleRules(cleanLive, SALE_RULES_MASTER);
          v38Enforce();
          const exp = Boolean(data.sapo_cookie_expired);
          if (timeEl) {
            timeEl.textContent = exp
              ? (currentLang === "VI" ? `⚠️ Cookie Sapo hết hạn (${data.synced_at || "Cũ"})` : `⚠️ Cookie Expired (${data.synced_at || "Stale"})`)
              : ((currentLang === "VI" ? "Cập nhật: " : "Synced: ") + (data.synced_at || "Live"));
          }
          if (badgeEl) { badgeEl.style.background = exp ? "var(--warn-bg)" : ""; badgeEl.style.color = exp ? "var(--warn-amber)" : ""; }
          if (dotEl) dotEl.style.background = exp ? "var(--warn-amber)" : "";
          lastSyncedStamp = data.synced_at || "";
          if (typeof discoverRepoMediaLive === "function") discoverRepoMediaLive(!silent);
          renderCatalog();
          updateCartBar();
          return true;
        }
      } catch (_) {} finally { v38SyncBusy = false; }
      return false;
    };

    window.addEventListener("resize", v38EnsureMainTabsDOM, { passive: true });
    v38Enforce();
    renderCatalog();
    updateCartBar();
    setTimeout(v38ApplyBranding, 10);
    setInterval(v38TickCountdown, 1000);
    setInterval(() => { if (document.visibilityState === "visible") refreshLiveSapoStock(true); }, 45000);
    window.addEventListener("focus", () => refreshLiveSapoStock(true), { passive: true });
    refreshLiveSapoStock(true);'''


def vn_now_str():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime("%H:%M %d/%m")


def norm_nfc(s):
    return unicodedata.normalize("NFC", str(s or "")).strip()


def strip_accents(s):
    s = str(s or "").replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").upper().strip()


def clean_base_code(val):
    raw = re.sub(r"\s+", "", str(val or "").strip().upper()).rstrip("-")
    m = SKU_REGEX.match(raw)
    return m.group(1).upper() if m else ""


def parse_money_col(val):
    if val is None: return 0
    if isinstance(val, (int, float)):
        v = float(val)
        return int(round(v * 1000)) if 0 < v < 1000 else int(round(v))
    s = str(val).strip().lower()
    if not s or "quà" in s or "#n/a" in s:
        return -1 if "quà" in s else 0
    d = re.sub(r"[^0-9]", "", s)
    if not d: return 0
    n = int(d)
    return n * 1000 if 0 < n < 1000 else n


def parse_strict_vnd_cell(val):
    if val is None: return 0
    if isinstance(val, (int, float)):
        n = int(round(float(val)))
        return n if 35000 <= n <= 950000 else 0
    s = str(val).strip().lower()
    if not s or "#n/a" in s: return 0
    if s.endswith("k"):
        d = re.sub(r"[^0-9]", "", s)
        return int(d) * 1000 if d and 35 <= int(d) <= 950 else 0
    d = re.sub(r"[^0-9]", "", s)
    if not d: return 0
    n = int(d)
    return n if 35000 <= n <= 950000 else 0


def parse_pct(val):
    if val is None: return 0.0
    try:
        v = float(str(val).strip().replace("%", "").replace(",", ".")) if not isinstance(val, (int, float)) else float(val)
        return v * 100.0 if 0 < v <= 1.0 else v
    except ValueError:
        return 0.0


def filter_variants_min_stock(variants_list, min_stock=MIN_VARIANT_STOCK):
    order, stocks_map = [], {}
    for v in (variants_list or []):
        if not isinstance(v, dict): continue
        sz = norm_nfc(v.get("size") or "Freesize") or "Freesize"
        if sz not in stocks_map: order.append(sz); stocks_map[sz] = {}
        for wid, qty in (v.get("stocks") or {}).items():
            try: stocks_map[sz][wid] = stocks_map[sz].get(wid, 0) + max(0, int(qty or 0))
            except Exception: pass
    return [{"size": sz, "stocks": stocks_map[sz]} for sz in order if sum(stocks_map[sz].values()) >= min_stock]


def is_sapo_cdn_url(url):
    u = str(url or "").strip()
    if not u or u.startswith(("./", "images/", "videos/", "sizes/", "data:", "blob:")): return False
    if "raw.githubusercontent.com/moonchuyensi-cmd/havana-sale" in u: return False
    return bool(SAPO_IMG_RX.search(u) or u.startswith(("http://", "https://")))


def split_custom_and_sapo_media(prod):
    custom_imgs, sapo_imgs = [], []
    for u in ([prod.get("image")] + list(prod.get("images") or []) + list(prod.get("sapoImages") or [])):
        s = str(u or "").strip()
        if not s or s.startswith("data:image/svg+xml"): continue
        if is_sapo_cdn_url(s):
            if s not in sapo_imgs: sapo_imgs.append(s)
        elif s not in custom_imgs: custom_imgs.append(s)
    vid = "" if is_sapo_cdn_url(prod.get("video")) else str(prod.get("video") or "").strip()
    szc = "" if is_sapo_cdn_url(prod.get("sizeChart")) else str(prod.get("sizeChart") or "").strip()
    return custom_imgs, sapo_imgs, vid, szc


def infer_category(code, pname, sub_type=""):
    up, nl, sl = code.upper(), norm_nfc(pname).lower(), norm_nfc(sub_type).lower()
    hint = f"{sl} {nl}".strip()
    if up.startswith("PK"):
        if re.search(r"(giày|dép|guốc|sandal|sục|boot|bốt)", hint): return "PK_GIAY_DEP"
        if re.search(r"(túi|\bví\b|balo|ba lô|clutch|tote)", hint): return "PK_TUI_XACH"
        return "PK_PHU_KIEN"
    if up.startswith("AD"): return "AO_DAI"
    if up.startswith("ATH"): return "AO_THUN"
    if up.startswith(("AO", "AE")): return "AO_SO_MI" if ("sơ mi" in nl or "sơmi" in nl) else "AO_KIEU"
    if up.startswith(("AK", "HD")): return "AO_KHOAC"
    if up.startswith("LEN"): return "AO_LEN"
    if up.startswith("CV"): return "CHAN_VAY"
    if up.startswith(("DA", "BE")): return "DAM"
    if up.startswith("SAT"): return "SET_BO"
    if up.startswith("Q"): return "QUAN"
    return "AO_KIEU"


def unpack_any_zip_archives():
    for d in ("images", "videos", "sizes"):
        os.makedirs(d, exist_ok=True)
    zips = []
    for pat in ("*.zip", "media/*.zip", "media/**/*.zip", "images/*.zip"):
        zips.extend(glob.glob(pat, recursive=True))
    for zp in sorted(set(zips)):
        low_z = os.path.basename(zp).lower()
        if "havana_sale" in low_z or "deploy" in low_z or "auto_cloud" in low_z:
            continue
        ext_dir = os.path.join("media", "_unzip_" + os.path.splitext(os.path.basename(zp))[0])
        os.makedirs(ext_dir, exist_ok=True)
        try:
            with zf_mod.ZipFile(zp, "r") as zf: zf.extractall(ext_dir)
            os.remove(zp)
        except Exception: pass
    if os.path.isdir("media"):
        for root, _, files in os.walk("media"):
            folder_hint = os.path.basename(root).replace("_unzip_", "").upper()
            sku_pfx = clean_base_code(folder_hint) if folder_hint not in ("MEDIA", "ATH", "BLACKFRIDAY", "") else ""
            for fn in sorted(files):
                if fn.startswith(".") or fn.lower() == "keep.txt": continue
                src = os.path.join(root, fn)
                low = fn.lower()
                tname = f"{sku_pfx}_{fn}" if (sku_pfx and sku_pfx not in re.sub(r"[\s_\-]+", "", fn.upper())) else fn
                dest_dir = "videos" if low.endswith(VID_EXTS) else ("sizes" if ("size" in low or "bangsize" in low) else ("images" if low.endswith(IMG_EXTS) else ""))
                if dest_dir:
                    dest_p = os.path.join(dest_dir, tname)
                    if os.path.exists(dest_p) and os.path.getsize(dest_p) != os.path.getsize(src):
                        b, ex = os.path.splitext(tname)
                        dest_p = os.path.join(dest_dir, f"{b}_{abs(hash(root)) % 1000}{ex}")
                    shutil.copy2(src, dest_p)
                    os.remove(src)
        for entry in os.listdir("media"):
            fe = os.path.join("media", entry)
            if os.path.isdir(fe): shutil.rmtree(fe, ignore_errors=True)


def detect_header_row_idx(rows):
    h_idx = -1
    best_score = 0
    for ri in range(min(15, len(rows))):
        r_vals = rows[ri] or []
        has_prod_sku = False
        for v in r_vals[:4]:
            bc = clean_base_code(str(v or "").split("-")[0])
            if bc and not bc.startswith("PVN") and re.search(r"\d{2,}", bc):
                has_prod_sku = True
                break
        if has_prod_sku:
            break
        norm_cells = [strip_accents(c) for c in r_vals if c is not None and str(c).strip() != ""]
        score = sum(1 for nc in norm_cells if any(k in nc for k in ("MA", "SKU", "TEN", "GIA", "DONG GIA", "TON", "SO LUONG", "SL", "ONLINE", "ONL", "MAU", "SIZE", "PHAN LOAI")))
        if score > best_score:
            best_score = score
            h_idx = ri
    return h_idx


def load_all_rules_and_excel_products():
    prev_json_rules = {}
    if os.path.exists("sale_rules.json"):
        try:
            with open("sale_rules.json", "r", encoding="utf-8") as f:
                prev_json_rules = json.load(f)
        except Exception:
            pass

    xlsx_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if fn.lower().endswith(".xlsx") and not fn.startswith("~$"):
                xlsx_files.append(os.path.join(root, fn))

    bf_files = sorted([x for x in xlsx_files if "ath" not in os.path.basename(x).lower()])
    ath_files = sorted([x for x in xlsx_files if "ath" in os.path.basename(x).lower()])

    bf_rules = {}
    ath_rules = {}
    ath_excel_products = {}

    if xlsx_files:
        try:
            import openpyxl
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
            import openpyxl

        for xf in bf_files:
            try:
                wb = openpyxl.load_workbook(xf, data_only=True)
            except Exception:
                continue
            for sname in wb.sheetnames:
                ws = wb[sname]
                if ws.sheet_state != "visible" or sname not in ("Tổng CH", "Phụ kiện"):
                    continue
                rows = list(ws.iter_rows(values_only=True))
                if not rows: continue
                h_idx = detect_header_row_idx(rows)
                header = [strip_accents(c) for c in (rows[h_idx] or [])] if h_idx >= 0 else []
                c_sku = c_orig = c_sale = c_pct = c_type = None
                for idx, h in enumerate(header):
                    if not h: continue
                    if c_sku is None and h in ("MA", "MA MOI", "SKU"): c_sku = idx
                    elif c_orig is None and "GIA BAN" in h: c_orig = idx
                    elif c_sale is None and ("DONG GIA" in h or "GIA SALE" in h or "GIA GIAM" in h): c_sale = idx
                    elif c_pct is None and ("%" in h or "GIAM GIA" in h): c_pct = idx
                    elif c_type is None and h == "LOAI": c_type = idx
                if c_sku is None: c_sku = 0
                for r in rows[h_idx + 1:]:
                    if not r or c_sku >= len(r): continue
                    code = clean_base_code(str(r[c_sku] or "").split("-")[0])
                    if not code or code in EXCLUDED_SKUS: continue
                    op = parse_money_col(r[c_orig]) if (c_orig is not None and c_orig < len(r)) else 0
                    sp = parse_money_col(r[c_sale]) if (c_sale is not None and c_sale < len(r)) else 0
                    if sp == -1: continue
                    pct_v = parse_pct(r[c_pct]) if (c_pct is not None and c_pct < len(r)) else 0.0
                    if sp <= 0 and op > 0 and pct_v > 0:
                        sp = int(round(op * (100.0 - pct_v) / 1000.0) * 1000)
                    if sp > 0:
                        sub_t = norm_nfc(r[c_type]) if (c_type is not None and c_type < len(r) and r[c_type]) else ""
                        bf_rules[code] = {
                            "origPrice": op if op > sp else sp,
                            "salePrice": sp,
                            "discountPct": max(1, round((1.0 - sp / op) * 100)) if op > sp else 30,
                            "subType": sub_t or PK_KNOWN_SUBTYPES.get(code, ""),
                            "section": "BF",
                            "singlePrice": False,
                        }

        if not bf_rules and prev_json_rules:
            for k, v in prev_json_rules.items():
                if k not in EXCLUDED_SKUS and v.get("section") != "ATH":
                    bf_rules[k] = dict(v, section="BF", singlePrice=False)

        for code, (op, sp, dp) in PRICE_PATCH_BF.items():
            if code in bf_rules:
                bf_rules[code].update({"origPrice": op, "salePrice": sp, "discountPct": dp})

        for xf in ath_files:
            try:
                wb = openpyxl.load_workbook(xf, data_only=True)
            except Exception as e:
                print(f"[!] Loi mo {xf}: {e}")
                continue
            vis_sheets = [sn for sn in wb.sheetnames if wb[sn].sheet_state == "visible" and "RUT" not in strip_accents(sn)]
            if not vis_sheets and wb.sheetnames:
                vis_sheets = [wb.sheetnames[0]]

            for sname in vis_sheets:
                ws = wb[sname]
                rows = list(ws.iter_rows(values_only=True))
                if not rows: continue
                h_idx = detect_header_row_idx(rows)
                header = [strip_accents(c) for c in (rows[h_idx] or [])] if h_idx >= 0 else []

                c_sku = c_name = c_opt = c_sale = c_orig = None
                indiv_stock_cols, total_stock_cols = {}, {}

                for idx, h in enumerate(header):
                    if not h or "THANH TIEN" in h or "GIA TRI" in h:
                        continue
                    if c_sku is None and (h in ("MA", "MA MOI", "SKU", "MA SKU", "MA HANG", "MA SP", "MA PHIEN BAN", "MA CON") or "MA SKU" in h):
                        c_sku = idx
                    elif c_name is None and ("TEN" in h):
                        c_name = idx
                    elif c_opt is None and any(k in h for k in ("MAU", "SIZE", "PHAN LOAI", "THUOC TINH")):
                        c_opt = idx
                    elif c_sale is None and (any(k in h for k in ("GIA ATH", "DONG GIA", "GIA SALE", "GIA GIAM", "GIA KM", "KHUYEN MAI", "GIA XA", "GIA MOI", "GIA SAU GIAM", "DE XUAT")) or h == "SALE"):
                        c_sale = idx
                    elif c_orig is None and (any(k in h for k in ("GIA BAN", "GIA GOC", "GIA LE", "DON GIA", "NIEM YET")) or h == "GIA"):
                        c_orig = idx
                    elif "DAU KY" not in h and "BAN RA" not in h and "STT" not in h and "NGAY" not in h:
                        if "PCT" in h or "PHAN CHAU" in h or "PHAN CHU" in h: indiv_stock_cols[idx] = "wh_pct"
                        elif "AU CO" in h or h in ("AC", "TON AC", "SL TON AC"): indiv_stock_cols[idx] = "wh_auco"
                        elif "LE DUAN" in h or h in ("LD", "TON LD", "SL TON LD"): indiv_stock_cols[idx] = "wh_leduan"
                        elif "HO PHI TICH" in h: indiv_stock_cols[idx] = "wh_havana"
                        elif any(k in h for k in ("ONLINE", "ONL", "CO THE BAN")): indiv_stock_cols[idx] = "wh_online"
                        elif any(k in h for k in ("TON", "SO LUONG", "SL", "CUOI KY", "SAN CO", "TONG")):
                            total_stock_cols[idx] = "wh_online"

                stock_cols = indiv_stock_cols if indiv_stock_cols else total_stock_cols
                last_code, last_ath_p, last_title = "", 0, ""

                for r in rows[h_idx + 1:]:
                    if not r or all(x is None or str(x).strip() == "" for x in r):
                        continue
                    raw_sku = str(r[c_sku] if (c_sku is not None and c_sku < len(r) and r[c_sku] is not None) else "").strip()
                    raw_name = norm_nfc(r[c_name]) if (c_name is not None and c_name < len(r) and r[c_name]) else ""

                    if not raw_name and c_name is None:
                        for cell_v in r[:5]:
                            sv = norm_nfc(cell_v)
                            if len(sv) >= 10 and " " in sv:
                                raw_name = sv
                                break

                    norm_name_up = re.sub(r"(?i)\bATH\s+(\d+)", r"ATH\1", raw_name.upper())
                    name_toks = [t for t in reversed(re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_name_up)) if not t.startswith("PVN")]
                    base = clean_base_code(raw_sku.split("-")[0])
                    if base.startswith("PVN"): base = ""
                    if not base and name_toks: base = name_toks[0]
                    if not base:
                        for cell_v in r[:5]:
                            cand_c = clean_base_code(str(cell_v or "").split("-")[0])
                            if cand_c and not cand_c.startswith("PVN") and re.search(r"\d{2,}", cand_c):
                                base = cand_c
                                if not raw_sku: raw_sku = str(cell_v or "").strip()
                                break
                    if not base or base in EXCLUDED_SKUS:
                        continue

                    p_sale = parse_money_col(r[c_sale]) if (c_sale is not None and c_sale < len(r)) else 0
                    p_orig = parse_money_col(r[c_orig]) if (c_orig is not None and c_orig < len(r)) else 0
                    ath_p = p_sale if p_sale > 0 else (p_orig if p_orig > 0 else 0)

                    if ath_p <= 0 and c_sale is None and c_orig is None:
                        for ci, cv in enumerate(r):
                            if ci in stock_cols or ci == c_sku or ci == c_name:
                                continue
                            svnd = parse_strict_vnd_cell(cv)
                            if svnd > 0:
                                ath_p = svnd
                                break

                    if base == last_code:
                        if ath_p <= 0: ath_p = last_ath_p
                        if not raw_name: raw_name = last_title

                    if ath_p <= 0 and base in bf_rules:
                        ath_p = bf_rules[base].get("origPrice") or 0
                    if ath_p <= 0 and base in ath_rules:
                        ath_p = ath_rules[base].get("salePrice") or 0

                    last_code, last_ath_p = base, ath_p
                    bf_rules.pop(base, None)

                    ath_rules[base] = {
                        "origPrice": ath_p,
                        "salePrice": ath_p,
                        "discountPct": 0,
                        "subType": "Áo thun",
                        "section": "ATH",
                        "singlePrice": True,
                    }

                    st_row = {}
                    for sc_i, wid in stock_cols.items():
                        if sc_i < len(r) and r[sc_i] is not None:
                            try:
                                qv = max(0, int(round(float(str(r[sc_i]).replace(",", "").strip()))))
                            except Exception:
                                qv = 0
                            st_row[wid] = st_row.get(wid, 0) + qv

                    clean_title = raw_name
                    sz_lbl = norm_nfc(r[c_opt]) if (c_opt is not None and c_opt < len(r) and r[c_opt]) else ""
                    if not sz_lbl and " - " in raw_name:
                        p_head, p_tail = raw_name.rsplit(" - ", 1)
                        if len(p_tail) <= 28:
                            sz_lbl = p_tail.strip()
                            clean_title = p_head.strip()
                    if not sz_lbl and "-" in raw_sku:
                        sfx = raw_sku.split("-", 1)[1].strip()
                        sz_lbl = SUFFIX_COLOR_MAP.get(sfx.upper(), sfx)
                    if not sz_lbl:
                        sz_lbl = "Freesize"

                    last_title = clean_title
                    default_name = f"Áo thun nữ Havana {base}"
                    ent = ath_excel_products.setdefault(base, {
                        "sku": base,
                        "name": clean_title or default_name,
                        "subType": "Áo thun",
                        "category": "AO_THUN",
                        "section": "ATH",
                        "singlePrice": True,
                        "origPrice": ath_p,
                        "salePrice": ath_p,
                        "discountPct": 0,
                        "image": "", "images": [], "sapoImages": [], "video": "", "sizeChart": "",
                        "excel_override": True,
                        "variants": [],
                    })
                    if clean_title and ent["name"].startswith("Áo thun nữ Havana"):
                        ent["name"] = clean_title
                    if ath_p > 0:
                        ent["origPrice"] = ath_p
                        ent["salePrice"] = ath_p
                    if st_row:
                        ent["variants"].append({"size": sz_lbl, "stocks": st_row})

            print(f"[+] Da nap Ao Thun ATH tu {xf}: {len(ath_rules)} ma!")

    sale_rules = {**bf_rules, **ath_rules}
    for ex in EXCLUDED_SKUS:
        sale_rules.pop(ex, None)
        ath_excel_products.pop(ex, None)

    if sale_rules:
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)

    return sale_rules, ath_excel_products, [os.path.basename(x) for x in xlsx_files]


def merge_ath_excel_into_grouped(grouped, ath_excel_prods, sale_rules):
    for c, xp in ath_excel_prods.items():
        if c not in sale_rules or c in EXCLUDED_SKUS:
            continue
        r = sale_rules[c]
        if c not in grouped:
            ath_p = r.get("salePrice") or xp.get("salePrice") or 189000
            xp["origPrice"] = ath_p
            xp["salePrice"] = ath_p
            xp["discountPct"] = 0
            xp["section"] = "ATH"
            xp["singlePrice"] = True
            grouped[c] = xp
            continue
        gp = grouped[c]
        gp["excel_override"] = True
        gp["section"] = "ATH"
        gp["singlePrice"] = True
        ath_p = r.get("salePrice") or gp.get("salePrice") or gp.get("origPrice") or 189000
        gp["origPrice"] = ath_p
        gp["salePrice"] = ath_p
        gp["discountPct"] = 0

        xp_vars = xp.get("variants") or []
        if not xp_vars:
            continue
        gp_valid = filter_variants_min_stock(gp.get("variants"), MIN_VARIANT_STOCK)
        xp_valid = filter_variants_min_stock(xp_vars, MIN_VARIANT_STOCK)
        if not gp_valid and xp_valid:
            gp["variants"] = xp_vars
        elif xp_valid:
            by_sz = {norm_nfc(v.get("size") or "Freesize").lower(): v for v in (gp.get("variants") or [])}
            for xv in xp_vars:
                xsz = norm_nfc(xv.get("size") or "Freesize")
                xst = xv.get("stocks") or {}
                if xsz.lower() in by_sz:
                    gst = by_sz[xsz.lower()].setdefault("stocks", {})
                    for wid, qv in xst.items():
                        gst[wid] = max(int(gst.get(wid) or 0), int(qv or 0))
                elif xsz.lower() == "freesize" and gp.get("variants"):
                    gst0 = gp["variants"][0].setdefault("stocks", {})
                    for wid, qv in xst.items():
                        gst0[wid] = max(int(gst0.get(wid) or 0), int(qv or 0))
                else:
                    gp.setdefault("variants", []).append(xv)


def scan_repo_media(codes):
    unpack_any_zip_archives()
    out_match = []
    for c in sorted(set(x.upper() for x in codes if x), key=len, reverse=True):
        m = re.match(r"^([A-Z]+)(\d+)$", c)
        rx = re.compile(rf"(^|[^A-Z0-9]){re.escape(m.group(1))}[\s_\-]*{re.escape(m.group(2))}([^0-9]|$)", re.I) if m else re.compile(rf"(^|[^A-Z0-9]){re.escape(c)}([^A-Z0-9]|$)", re.I)
        out_match.append((c, rx))

    media_map, all_files = {}, []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.startswith(".") and fn.lower() not in ("keep.txt", "readme.md"):
                all_files.append(os.path.join(root, fn).replace("\\", "/").lstrip("./"))

    all_files.sort(key=lambda p: (0 if p.startswith(("images/", "videos/", "sizes/")) else 1, p.lower()))
    for rel in all_files:
        low = rel.lower()
        if not low.endswith(IMG_EXTS + VID_EXTS): continue
        norm_rel = re.sub(r"(?i)(^|/)ATH[\s_\-/]+(\d+)", r"\1ATH\2", rel)
        fn, pd = os.path.basename(norm_rel), os.path.dirname(norm_rel)
        hit = next((c for c, rx in out_match if rx.search(fn)), "") or (next((c for c, rx in out_match if rx.search(pd)), "") if pd else "")
        if not hit or hit in FORCE_RESET_TO_SAPO_SKUS: continue
        enc = "./" + "/".join(urllib.parse.quote(x) for x in rel.split("/"))
        ent = media_map.setdefault(hit, {"images": [], "video": "", "sizeChart": ""})
        if low.endswith(VID_EXTS):
            if not ent["video"]: ent["video"] = enc
        elif rel.startswith("sizes/") or "size" in fn.lower() or "bangsize" in fn.lower():
            if not ent["sizeChart"]: ent["sizeChart"] = enc
        elif enc not in ent["images"]:
            ent["images"].append(enc)
    return media_map


def apply_media_to_products(prods_list, repo_media, preserved):
    for p in prods_list:
        c = clean_base_code(str(p.get("sku") or "").split("-")[0])
        rm = repo_media.get(c, {})
        pr = preserved.get(c, {})
        _, cur_sapo, cur_vid, cur_sz = split_custom_and_sapo_media(p)
        c_imgs = [] if c in FORCE_RESET_TO_SAPO_SKUS else (rm.get("images") or pr.get("custom") or [])
        s_imgs = cur_sapo if cur_sapo else (pr.get("sapo") or [])
        if c == "SAT532":
            s_imgs = [u for u in s_imgs if "sat533" not in u.lower()]
        fin = c_imgs if c_imgs else s_imgs
        p["sapoImages"] = s_imgs
        p["hasCustomMedia"] = bool(c_imgs or rm.get("video"))
        p["images"] = fin
        p["image"] = fin[0] if fin else ""
        p["video"] = "" if c in FORCE_RESET_TO_SAPO_SKUS else (rm.get("video") or pr.get("vid") or cur_vid or "")
        p["sizeChart"] = "" if c in FORCE_RESET_TO_SAPO_SKUS else (rm.get("sizeChart") or pr.get("sz") or cur_sz or "")


def upgrade_repo_files_v38():
    changed = False
    os.makedirs(os.path.join("functions", "api"), exist_ok=True)
    sjs_path = os.path.join("functions", "api", "stock.js")
    old_js = open(sjs_path, "r", encoding="utf-8").read() if os.path.exists(sjs_path) else ""
    if old_js.strip() != CF_STOCK_JS_V38.strip():
        with open(sjs_path, "w", encoding="utf-8") as f:
            f.write(CF_STOCK_JS_V38)
        changed = True

    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f: orig = f.read()
        html = orig
        html = re.sub(r'(<title>)[^<]*(</title>)', r'\1HAVANA — Áo Thun ATH &amp; Sale Black Friday (Online)\2', html, count=1)
        html = re.sub(r'(<div class="brand-title" id="mainBrandTitle">)[^<]*(</div>)', r'\1XẢ NGÀY CUỐI\2', html, count=1)
        html = re.sub(r'(<span id="subBrandText">)[^<]*(</span>)', r'\1(Chỉ áp dụng cho Online)\2', html, count=1)
        html = re.sub(r'(<span class="bill-head-badge">)[^<]*(</span>)', r'\1HAVANA ONLINE • CHỈ ÁP DỤNG CHO ONLINE\2', html, count=1)
        html = re.sub(r'(<h2 id="billModalTitle"[^>]*>)[^<]*(</h2>)', r'\1Phiếu Lên Đơn Tạm\2', html, count=1)
        html = re.sub(r'(<input type="text" class="bill-input" id="custNoteInput" placeholder=")[^"]*(")', r'\1Địa chỉ nhận ship &amp; Ghi chú đơn hàng (Chỉ áp dụng cho Online)...\2', html, count=1)
        html = html.replace('📋 Sao chép nội dung Bill', '📋 Sao chép Đơn tạm').replace('🖼️ Tải ảnh Bill gửi Zalo', '🖼️ Tải ảnh Đơn tạm gửi Zalo')
        end_m = html.rfind("/* [BLOCK:APP_LOGIC_END] */")
        if end_m != -1:
            cuts = [html.find(k, 0, end_m) for k in ("/* === v22:", "/* === v23:", "/* === v25:", "/* === v26:", "/* === v27:", "/* === v28:", "/* === v29:", "/* === v30:", "/* === v31:", "/* === v32:", "/* === v33:", "/* === v34:", "/* === v35:", "/* === v36:", "/* === v37:", "/* === v38:")]
            valid = [p for p in cuts if p != -1]
            cut_at = min(valid) if valid else end_m
            html = html[:cut_at].rstrip() + "\n\n    " + V38_INDEX_JS_ADDON.strip() + "\n    /* [BLOCK:APP_LOGIC_END] */" + html[end_m + len("/* [BLOCK:APP_LOGIC_END] */"):]
        html = re.sub(r'(<span id="onlyInStockText">)(?:Còn tồn|In Stock)(</span>)', r'\1Tồn ≥ 3\2', html, count=1)
        if html != orig:
            with open("index.html", "w", encoding="utf-8") as f: f.write(html)
            changed = True
    return changed


def extract_smart_secret(raw_val):
    raw = str(raw_val or "").strip()
    if not raw: return ""
    mb = re.search(r"(?:-b|--cookie)\s+(['\"])(.+?)\1", raw, flags=re.S)
    mh = re.search(r"(?:-H|--header)\s+(['\"])cookie\s*:\s*(.+?)\1", raw, flags=re.I | re.S)
    ml = re.search(r"(?:^|[\r\n])cookie\s*:\s*([^\r\n]+)", raw, flags=re.I)
    raw = mb.group(2) if mb else (mh.group(2) if mh else (ml.group(1) if ml else raw))
    if "^" in raw and ("sapo-admin" in raw or "AspNetCore" in raw): raw = re.sub(r"\^([\"%&|<>^])", r"\1", raw)
    s = re.sub(r"[\r\n]+", " ", raw.strip().strip('"').strip("'")).strip()
    for pfx in ("cookie:", "x-sapo-access-token:"):
        if s.lower().startswith(pfx): s = s[len(pfx):].strip()
    return "".join(ch for ch in s if 32 <= ord(ch) <= 126)


def fetch_json(path, headers):
    req = urllib.request.Request(f"https://{DOMAIN}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        if raw.lstrip().startswith("<"): raise RuntimeError("Sapo Cookie het han!")
        return json.loads(raw)


def map_branch(raw_name, loc_id, idx=9):
    n = strip_accents(raw_name)
    if n == "HAVANA" or "HO PHI TICH" in n or "93-95" in n: return ("wh_havana", "HAVANA (93-95 Hồ Phi Tích)", 1)
    if "ONLINE" in n: return ("wh_online", "HAVANA ONLINE", 2)
    if "261" in n or "PHAN CHU TRINH" in n or "PHAN CHAU TRINH" in n: return ("wh_pct", "261 - 263 Phan Châu Trinh", 3)
    if "101" in n or "AU CO" in n: return ("wh_auco", "101 Âu Cơ", 4)
    if "240" in n or "LE DUAN" in n: return ("wh_leduan", "240 Lê Duẩn", 5)
    return (f"wh_{loc_id}", norm_nfc(raw_name) or f"Chi nhánh {loc_id}", idx)


def publish_live_release(wh_list, prods, synced_at, epoch, expired):
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "moonchuyensi-cmd/havana-sale").strip()
    if not gh_token or not repo: return False
    wh_ids = [w.get("id") for w in wh_list]
    items = {}
    for p in prods:
        c = str(p.get("sku") or "").upper()
        if not c: continue
        vv = filter_variants_min_stock(p.get("variants"), MIN_VARIANT_STOCK)
        ent = {"v": [[v.get("size") or "Freesize", [int((v.get("stocks") or {}).get(w) or 0) for w in wh_ids]] for v in vv]}
        if vv and p.get("name"): ent["n"] = p["name"]
        if c in FORCE_RESET_TO_SAPO_SKUS and p.get("sapoImages"): ent["si"] = list(p["sapoImages"])[:5]
        items[c] = ent
    body = json.dumps({"v": 38, "full": 1, "cookie_expired": bool(expired), "synced_at": synced_at, "synced_epoch": int(epoch or 0), "warehouses": wh_list, "items": items}, ensure_ascii=False, separators=(",", ":"))
    if len(body) > 120000:
        tmp = json.loads(body)
        for v in tmp["items"].values(): v.pop("n", None)
        body = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    hdrs = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "havana-v38"}
    rel_id = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/live-stock", headers=hdrs), timeout=15) as r:
            rel_id = json.loads(r.read().decode("utf-8")).get("id")
    except Exception: pass
    data = json.dumps({"tag_name": "live-stock", "name": f"Live Sapo ({synced_at})", "body": body, "draft": False, "prerelease": True}).encode("utf-8")
    try:
        url = f"https://api.github.com/repos/{repo}/releases/{rel_id}" if rel_id else f"https://api.github.com/repos/{repo}/releases"
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdrs, method="PATCH" if rel_id else "POST"), timeout=20) as r: r.read()
        return True
    except Exception: return False


def write_outputs(wh_list, prods, synced_at, rules, epoch, expired):
    filtered = []
    for p in prods:
        cv = filter_variants_min_stock(p.get("variants"), MIN_VARIANT_STOCK)
        if cv and sum(sum(v["stocks"].values()) for v in cv) >= MIN_TOTAL_STOCK:
            filtered.append(dict(p, variants=cv))
    # Sap xep uu tien dua cac ma ATH len dau danh sach va ma moi nhat len truoc
    filtered.sort(key=lambda x: (0 if x.get("section") == "ATH" else 1, x.get("sku") or ""))
    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump({"min_stock_rule": MIN_TOTAL_STOCK, "min_variant_stock_rule": MIN_VARIANT_STOCK, "synced_at": synced_at, "synced_epoch": int(epoch or 0), "sapo_cookie_expired": bool(expired), "warehouses": wh_list, "products": filtered}, f, ensure_ascii=False, indent=2)
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f: h = f.read()
        s_i, e_i = h.find("/* [BLOCK:DATA_PAYLOAD_START] */"), h.find("/* [BLOCK:DATA_PAYLOAD_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_PAYLOAD_END] */")
            blk = f"/* [BLOCK:DATA_PAYLOAD_START] */\n    let WAREHOUSE_CONFIG = {json.dumps(wh_list, ensure_ascii=False)};\n    let SALE_RULES_MASTER = {json.dumps(rules or {}, ensure_ascii=False)};\n    let BASELINE_PRODUCTS = {json.dumps(filtered, ensure_ascii=False)};\n    let PRODUCTS = BASELINE_PRODUCTS.map((p) => Object.assign({{}}, p));\n    /* [BLOCK:DATA_PAYLOAD_END] */"
            with open("index.html", "w", encoding="utf-8") as f: f.write(h[:s_i] + blk + h[e_i:])
    return filtered


def main():
    upgrade_repo_files_v38()
    sale_rules, ath_excel_prods, found_xlsx = load_all_rules_and_excel_products()
    repo_media = scan_repo_media(list(sale_rules.keys()))

    old_wh, old_prods, old_stamp, old_epoch = [], [], "11:50 27/11", 0
    if os.path.exists("sapo_5_kho.json"):
        try:
            with open("sapo_5_kho.json", "r", encoding="utf-8") as f: od = json.load(f)
            old_wh, old_prods = od.get("warehouses") or [], od.get("products") or []
            old_stamp, old_epoch = str(od.get("synced_at") or old_stamp), int(od.get("synced_epoch") or 0)
        except Exception: pass

    preserved = {}
    for op in old_prods:
        ci, si, vd, sz = split_custom_and_sapo_media(op)
        c = clean_base_code(str(op.get("sku") or "").split("-")[0])
        if c: preserved[c] = {"custom": [] if c in FORCE_RESET_TO_SAPO_SKUS else ci, "sapo": si, "vid": "" if c in FORCE_RESET_TO_SAPO_SKUS else vd, "sz": "" if c in FORCE_RESET_TO_SAPO_SKUS else sz}

    base_h = {"accept": "application/json, text/plain, */*", "accept-language": "vi", "user-agent": "Mozilla/5.0 Chrome/131.0.0.0"}
    ck, tk = extract_smart_secret(os.environ.get("SAPO_COOKIE", "")), extract_smart_secret(os.environ.get("SAPO_ACCESS_TOKEN", ""))
    cands = []
    if ck: cands.append({**base_h, "cookie": ck, "referer": f"https://{DOMAIN}/admin/products", "x-sapo-client": "sapo-frontend-v3", "x-sapo-serviceid": "sapo-frontend-v3"})
    if tk: cands.append({**base_h, "cookie": tk, "referer": f"https://{DOMAIN}/admin/products", "x-sapo-client": "sapo-frontend-v3", "x-sapo-serviceid": "sapo-frontend-v3"} if ("sapo-admin=" in tk or ";" in tk) else {**base_h, "X-Sapo-Access-Token": tk})

    all_sapo, loc_meta = [], {}
    for hdrs in cands:
        try:
            p1 = fetch_json("/admin/products.json?page=1&limit=250", hdrs)
            b1 = p1.get("products") or []
            all_sapo.extend(b1)
            try:
                for idx, loc in enumerate((fetch_json("/admin/locations.json", hdrs).get("locations")) or []):
                    rid, rn = str(loc["id"]), loc.get("label") or loc.get("name") or ""
                    wid, wlbl, word = map_branch(rn, rid, 10 + idx)
                    loc_meta[rid] = {"id": wid, "sapo_name": rn, "name": wlbl, "order": word}
            except Exception: pass
            if len(b1) == 250:
                tot = int((p1.get("metadata") or {}).get("total") or 0)
                max_p = min(40, (tot + 249) // 250) if tot > 250 else 15
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    for br in pool.map(lambda pg: fetch_json(f"/admin/products.json?page={pg}&limit=250", hdrs), range(2, max_p + 1)):
                        all_sapo.extend(br.get("products") or [])
            break
        except Exception: pass

    if not all_sapo:
        merged_map = {}
        for ep in old_prods:
            c = clean_base_code(str(ep.get("sku") or "").split("-")[0])
            if not c or c in EXCLUDED_SKUS or c not in sale_rules: continue
            r = sale_rules[c]
            is_ath = (r.get("section") == "ATH")
            ep["section"] = "ATH" if is_ath else "BF"
            ep["singlePrice"] = is_ath
            if is_ath:
                ath_p = r.get("salePrice") or ep.get("origPrice") or ep.get("salePrice") or 189000
                ep["origPrice"] = ath_p
                ep["salePrice"] = ath_p
                ep["discountPct"] = 0
                sale_rules[c]["origPrice"] = ath_p
                sale_rules[c]["salePrice"] = ath_p
            else:
                ep["origPrice"] = r.get("origPrice") or ep.get("origPrice", 299000)
                ep["salePrice"] = r.get("salePrice") or ep.get("salePrice", 159000)
                ep["discountPct"] = max(1, round((1 - ep["salePrice"] / ep["origPrice"]) * 100)) if ep["origPrice"] > ep["salePrice"] else 30
            merged_map[c] = ep
        merge_ath_excel_into_grouped(merged_map, ath_excel_prods, sale_rules)
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)
        final_list = list(merged_map.values())
        apply_media_to_products(final_list, repo_media, preserved)
        wh_out = old_wh or [{"id": "wh_havana", "name": "HAVANA (93-95 Hồ Phi Tích)"}, {"id": "wh_online", "name": "HAVANA ONLINE"}, {"id": "wh_pct", "name": "261 - 263 Phan Châu Trinh"}, {"id": "wh_auco", "name": "101 Âu Cơ"}, {"id": "wh_leduan", "name": "240 Lê Duẩn"}]
        publish_live_release(wh_out, final_list, old_stamp, old_epoch, expired=True)
        if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
        kept = write_outputs(wh_out, final_list, old_stamp, sale_rules, old_epoch, expired=True)
        print(f"[+] Hoan tat v38 (Offline): {len(kept)} ma!")
        return

    grouped = {}
    for p in all_sapo:
        vraw = p.get("variants") or []
        if not vraw: continue
        ntoks = [t for t in reversed(re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_nfc(p.get("name") or "").upper())) if t in sale_rules]
        base = ntoks[0] if ntoks else next((clean_base_code(str(v.get("sku") or "").split("-")[0]) for v in vraw if clean_base_code(str(v.get("sku") or "").split("-")[0]) in sale_rules), "")
        if not base or base in EXCLUDED_SKUS: continue
        rule = sale_rules[base]
        is_ath = (rule.get("section") == "ATH")
        sapo_retail = parse_money_col(vraw[0].get("variant_retail_price") or vraw[0].get("price") or 0)

        if is_ath:
            ath_p = rule.get("salePrice") or sapo_retail or rule.get("origPrice") or 189000
            op, sp, dp = ath_p, ath_p, 0
            sale_rules[base]["origPrice"] = ath_p
            sale_rules[base]["salePrice"] = ath_p
        else:
            op = rule.get("origPrice") or sapo_retail or 299000
            sp = rule.get("salePrice") or 159000
            if op < sp: op = max(op, sapo_retail, sp)
            dp = max(1, round((1 - sp / op) * 100)) if op > sp > 0 else 30

        s_imgs = []
        im0 = p.get("image")
        for im in ([im0] if im0 else []) + list(p.get("images") or []):
            u = str(im.get("full_path") or im.get("src") or "" if isinstance(im, dict) else im).strip()
            if u.startswith("//"): u = "https:" + u
            if u.startswith("http") and u not in s_imgs: s_imgs.append(u)
        v_list = []
        for v in vraw:
            opts = [norm_nfc(v.get(k)) for k in ("opt1", "opt2", "opt3") if v.get(k)]
            sz = " - ".join(opts) if opts else norm_nfc(v.get("title") or "Freesize")
            if sz.lower() == "default title": sz = "Freesize"
            st = {}
            for inv in (v.get("inventories") or []):
                rid = str(inv.get("location_id"))
                if rid not in loc_meta:
                    wid, wlbl, word = map_branch(inv.get("location_label") or "", rid, 20 + len(loc_meta))
                    loc_meta[rid] = {"id": wid, "sapo_name": wlbl, "name": wlbl, "order": word}
                av = inv.get("available") if inv.get("available") is not None else inv.get("on_hand", 0)
                st[loc_meta[rid]["id"]] = st.get(loc_meta[rid]["id"], 0) + max(0, int(av or 0))
            v_list.append({"size": sz, "stocks": st})
        if base in grouped:
            grouped[base]["variants"].extend(v_list)
            for u in s_imgs:
                if u not in grouped[base]["sapoImages"]: grouped[base]["sapoImages"].append(u)
        else:
            pname = norm_nfc(p.get("name") or base)
            grouped[base] = {
                "sku": base, "name": pname, "subType": rule.get("subType", ""),
                "category": infer_category(base, pname, rule.get("subType", "")),
                "section": "ATH" if is_ath else "BF", "singlePrice": is_ath,
                "origPrice": op, "salePrice": sp, "discountPct": dp,
                "image": "", "images": [], "sapoImages": s_imgs, "video": "", "sizeChart": "", "variants": v_list,
            }

    merge_ath_excel_into_grouped(grouped, ath_excel_prods, sale_rules)
    with open("sale_rules.json", "w", encoding="utf-8") as f:
        json.dump(sale_rules, f, ensure_ascii=False, indent=2)

    prods_all = list(grouped.values())
    apply_media_to_products(prods_all, repo_media, preserved)
    wh_sorted = [{k: v for k, v in it.items() if k != "order"} for it in sorted(loc_meta.values(), key=lambda x: x["order"])]
    now_st, now_ep = vn_now_str(), int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    publish_live_release(wh_sorted, prods_all, now_st, now_ep, expired=False)
    if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
    kept = write_outputs(wh_sorted, prods_all, now_st, sale_rules, now_ep, expired=False)
    bf_cnt = sum(1 for x in kept if x.get("section") != "ATH")
    ath_cnt = sum(1 for x in kept if x.get("section") == "ATH")
    summary_md = f"### ✅ Đồng bộ v38 — Ưu tiên Áo Thun ATH lên đầu ({now_st})\n- **✨ ÁO THUN ATH (mở đầu tiên):** `{ath_cnt}` mẫu\n- **🔥 BLACK FRIDAY:** `{bf_cnt}` mẫu"
    print(summary_md)
    sp_sum = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if sp_sum:
        with open(sp_sum, "a", encoding="utf-8") as sf: sf.write(summary_md + "\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
# === HET FILE V38 ===
