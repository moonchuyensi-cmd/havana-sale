import os
import re
import sys
import glob
import json
import time
import shutil
import datetime
import hashlib
import concurrent.futures
import subprocess
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile as zf_mod

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v30 COMPACT - GHI CHU BILL + LOC TON >= 3 + SAPO LIVE)
# Ban rut gon chong cat dong khi copy-paste, giu du 100% tinh nang v28 + v29
# ==============================================================================

MIN_TOTAL_STOCK = 3
MIN_VARIANT_STOCK = 3
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
FORCE_RESET_TO_SAPO_SKUS = {"SAT532", "CV538"}
PURGE_STATE_FILE = ".purged_wrong_media_v27.json"
PRICE_PATCH_V30 = {
    "AO1010": (194000, 79000, 59, 0.5928),
    "AO726":  (225000, 39000, 83, 0.8267),
    "AO1016": (214000, 79000, 63, 0.6308),
}
SAPO_IMG_RX = re.compile(r"(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)", re.I)
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".jfif", ".avif", ".gif", ".bmp")
VID_EXTS = (".mp4", ".mov", ".webm", ".m4v")
SKIP_DIRS = {".git", ".github", "functions", "__pycache__", "node_modules"}
PROTECTED_ROOT_DIRS = {"images", "videos", "sizes", "media", "."}
PK_KNOWN_SUBTYPES = {
    "PK822": "Mũ", "PK887": "Túi xách", "PK893": "Mũ", "PK894": "Mũ",
    "PK897": "Phụ kiện", "PK898": "Túi xách", "PK976": "Túi xách", "PK995": "Túi xách",
    "PK997": "Túi xách", "PK999": "Túi xách", "PK1006": "Giày", "PK1027": "Túi xách", "PK1032": "Giày",
}

V30_INDEX_JS_ADDON = r'''/* === v30: BILL STOCK DISCLAIMER + EPOCH FIX + COOKIE WARNING + VARIANT >= 3 === */
    const MIN_STOCK_V30 = 3;
    const FORCE_SAPO_V30 = new Set(["SAT532", "CV538"]);
    const BILL_NOTE_VI_V30 = "📌 Lưu ý: Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";
    const BILL_NOTE_EN_V30 = "📌 Note: Sale items have limited stock and may slightly differ from actual store inventory. Thank you for your understanding!";
    let v30SyncBusy = false;
    let v30LastRelMs = 0;

    if (typeof RESET_TO_SAPO_SKUS_V21 !== "undefined" && RESET_TO_SAPO_SKUS_V21 && RESET_TO_SAPO_SKUS_V21.add) {
      RESET_TO_SAPO_SKUS_V21.add("CV538");
      RESET_TO_SAPO_SKUS_V21.add("SAT532");
    }
    try {
      ["havana_gh_tree_v13","havana_live_rel_v22","havana_live_rel_v25","havana_live_rel_v26","havana_live_rel_v27","havana_live_rel_v28","havana_live_rel_v29"].forEach((k) => sessionStorage.removeItem(k));
    } catch (_) {}
    if (typeof MEDIA_CACHE_VERSION !== "undefined" && MEDIA_CACHE_VERSION < 30) MEDIA_CACHE_VERSION = 30;

    const v30BillNoteText = () => (currentLang === "EN" ? BILL_NOTE_EN_V30 : BILL_NOTE_VI_V30);

    const _preV30RenderBillModal = renderBillModal;
    renderBillModal = function() {
      _preV30RenderBillModal();
      const sumEl = document.getElementById("billSummaryBox");
      if (sumEl && !sumEl.querySelector(".bill-stock-disclaimer")) {
        const noteDiv = document.createElement("div");
        noteDiv.className = "bill-stock-disclaimer";
        noteDiv.style.cssText = "margin-top:8px;padding:9px 12px;border-radius:11px;background:#fffbeb;border:1px dashed #f59e0b;color:#92400e;font-size:12.5px;line-height:1.45;font-weight:600;";
        noteDiv.textContent = v30BillNoteText();
        sumEl.appendChild(noteDiv);
      }
    };

    const _preV30BuildBillText = buildFormattedBillText;
    buildFormattedBillText = function() {
      const baseTxt = _preV30BuildBillText();
      if (baseTxt.includes("lệch so với tồn thực tế") || baseTxt.includes("differ from actual")) return baseTxt;
      return baseTxt + "\n--------------------------\n" + v30BillNoteText();
    };

    const v30RoundRect = (ctx, x, y, w, h, radii) => {
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, radii);
      else {
        const r = Array.isArray(radii) ? (radii[0] || 12) : (radii || 12);
        ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
      }
    };

    downloadBillImage = function() {
      if (!cart.length) return;
      saveCustomerDraft();
      const custName = (document.getElementById("custNameInput")?.value || "").trim() || (currentLang === "EN" ? "Havana Guest" : "Khách lẻ Havana");
      const custPhone = (document.getElementById("custPhoneInput")?.value || "").trim();
      const custNote = (document.getElementById("custNoteInput")?.value || "").trim();
      const nowStr = new Date().toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" });
      const { totalQty, totalOrig, totalSale, totalSaved } = getCartTotals();

      const W = 760, rowH = 62, fontStack = "'Be Vietnam Pro', -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      const rawNote = currentLang === "EN"
        ? "* Note: Sale items have limited stock and may slightly differ from actual store inventory. Thank you for your understanding!"
        : "* Lưu ý: Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";

      const tmpCtx = document.createElement("canvas").getContext("2d");
      const noteFont = `600 13.5px ${fontStack}`;
      tmpCtx.font = noteFont;
      const noteLines = [];
      let curLn = "";
      rawNote.split(/\s+/).forEach((w) => {
        const cand = curLn ? (curLn + " " + w) : w;
        if (tmpCtx.measureText(cand).width > (W - 132) && curLn) { noteLines.push(curLn); curLn = w; }
        else curLn = cand;
      });
      if (curLn) noteLines.push(curLn);

      const noteBoxH = 16 + noteLines.length * 20;
      const H = 314 + cart.length * rowH + (custNote ? 26 : 0) + noteBoxH;
      const canvas = document.createElement("canvas");
      canvas.width = W; canvas.height = H;
      const ctx = canvas.getContext("2d");

      ctx.fillStyle = "#f2f9f8"; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = "#ffffff"; ctx.strokeStyle = "#91d2ce"; ctx.lineWidth = 3;
      v30RoundRect(ctx, 24, 24, W - 48, H - 48, 22); ctx.fill(); ctx.stroke();

      ctx.fillStyle = "#165954";
      v30RoundRect(ctx, 24, 24, W - 48, 86, [22, 22, 0, 0]); ctx.fill();
      ctx.fillStyle = "#ffffff"; ctx.font = `bold 24px ${fontStack}`;
      ctx.fillText("HAVANA — PHIẾU ĐƠN BLACK FRIDAY", 52, 65);
      ctx.fillStyle = "#bbf7d0"; ctx.font = `600 14px ${fontStack}`;
      ctx.fillText("Thời gian xuất bill: " + nowStr + " • Giá gốc tại kho", 52, 92);

      let y = 142;
      ctx.fillStyle = "#11302e"; ctx.font = `bold 15px ${fontStack}`;
      ctx.fillText("Khách hàng: " + custName + (custPhone ? "   |   SĐT: " + custPhone : ""), 52, y);
      if (custNote) {
        y += 26; ctx.fillStyle = "#52706e"; ctx.font = `500 14px ${fontStack}`;
        ctx.fillText("Ghi chú / Nhận hàng: " + custNote.slice(0, 68), 52, y);
      }
      y += 22; ctx.strokeStyle = "#cdebe8"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(52, y); ctx.lineTo(W - 52, y); ctx.stroke();

      y += 30;
      cart.forEach((item, i) => {
        const q = Number(item.qty || 1), lineSum = Number(item.finalPrice || 0) * q;
        ctx.fillStyle = "#11302e"; ctx.font = `bold 15px ${fontStack}`;
        ctx.fillText((`${i + 1}. [${item.sku}] ` + item.name).slice(0, 48), 52, y);
        ctx.fillStyle = "#52706e"; ctx.font = `600 13.5px ${fontStack}`;
        ctx.fillText(`Size: ${item.size}   •   SL: ${q} x ${formatVND(item.finalPrice)}`, 52, y + 22);
        ctx.fillStyle = "#be123c"; ctx.font = `bold 16px ${fontStack}`;
        const pTxt = formatVND(lineSum);
        ctx.fillText(pTxt, W - 52 - ctx.measureText(pTxt).width, y + 10);
        y += rowH;
      });

      ctx.strokeStyle = "#91d2ce"; ctx.beginPath(); ctx.moveTo(52, y - 10); ctx.lineTo(W - 52, y - 10); ctx.stroke();
      y += 18; ctx.fillStyle = "#52706e"; ctx.font = `600 14.5px ${fontStack}`;
      ctx.fillText(`Tổng SL: ${totalQty} sp   |   Giá gốc: ${formatVND(totalOrig)}   |   Tiết kiệm: -${formatVND(totalSaved)}`, 52, y);

      y += 36; ctx.fillStyle = "#165954"; ctx.font = `bold 20px ${fontStack}`;
      ctx.fillText("TỔNG THANH TOÁN:", 52, y);
      ctx.fillStyle = "#be123c"; ctx.font = `bold 24px ${fontStack}`;
      const totStr = formatVND(totalSale);
      ctx.fillText(totStr, W - 52 - ctx.measureText(totStr).width, y);

      const noteY = y + 18;
      ctx.fillStyle = "#fffbeb"; ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 1.2;
      v30RoundRect(ctx, 52, noteY, W - 104, noteBoxH, 11); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#92400e"; ctx.font = noteFont;
      noteLines.forEach((ln, idx) => ctx.fillText(ln, 66, noteY + 22 + idx * 20));

      const link = document.createElement("a");
      link.download = "Bill_Havana_BlackFriday_" + Date.now().toString().slice(-5) + ".png";
      link.href = canvas.toDataURL("image/png");
      link.click();
      showToast("🖼️ Đã tải ảnh Phiếu Bill nét cao về máy!");
    };

    const v30ApplyPrices = (rm) => {
      if (!rm || typeof rm !== "object") return;
      const fx = { AO1010: { origPrice: 194000, salePrice: 79000, discountPct: 59 }, AO726: { origPrice: 225000, salePrice: 39000, discountPct: 83 }, AO1016: { origPrice: 214000, salePrice: 79000, discountPct: 63 } };
      Object.keys(fx).forEach((k) => { if (rm[k]) Object.assign(rm[k], fx[k]); });
    };

    const v30ForceSapoCheck = (code) => FORCE_SAPO_V30.has(code) && !(typeof sessionFreshUploadedSkus !== "undefined" && sessionFreshUploadedSkus && sessionFreshUploadedSkus.has(code));

    const v30CleanItemMedia = (p) => {
      if (!p) return;
      const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
      if (!v30ForceSapoCheck(code)) return;
      if (typeof repoLiveMediaBySku === "object" && repoLiveMediaBySku) delete repoLiveMediaBySku[code];
      const s533 = (code === "SAT532" && typeof getSat533ImageSet === "function") ? getSat533ImageSet() : new Set();
      const urls = [];
      [...(p.sapoImages || []), ...(p.images || []), p.image].forEach((u) => {
        const s = String(u || "").trim();
        if (s && isSapoImageUrl(s) && !(code === "SAT532" && (s533.has(s) || s.toLowerCase().includes("sat533"))) && !urls.includes(s)) urls.push(s);
      });
      p.sapoImages = urls; p.hasCustomMedia = false; p.video = ""; p.sizeChart = ""; p.images = [...urls]; p.image = urls[0] || "";
    };

    const _preV30Strip = stripSapoImagesFromProducts;
    stripSapoImagesFromProducts = function(list) { _preV30Strip(list); if (Array.isArray(list)) list.forEach(v30CleanItemMedia); };

    const _preV30Slides = getProductSlides;
    getProductSlides = function(p) {
      const code = normalizeBaseCode(String(p?.sku || "").split("-")[0]);
      if (v30ForceSapoCheck(code)) {
        v30CleanItemMedia(p);
        return (p.sapoImages && p.sapoImages.length) ? p.sapoImages.map((src) => ({ type: "image", src })) : [{ type: "image", src: svgPlaceholder(p.sku, p.name, "#d8f1ef", "#eef9f8") }];
      }
      return _preV30Slides(p);
    };

    const v30FilterVars = (arr) => {
      if (!Array.isArray(arr)) return [];
      const ord = [], mp = {};
      arr.forEach((v) => {
        if (!v) return;
        const sz = String(v.size || "Freesize").trim() || "Freesize";
        if (!mp[sz]) { ord.push(sz); mp[sz] = {}; }
        Object.entries(v.stocks || {}).forEach(([w, q]) => { mp[sz][w] = (mp[sz][w] || 0) + Math.max(0, Number(q) || 0); });
      });
      return ord.filter((sz) => Object.values(mp[sz]).reduce((a, b) => a + b, 0) >= MIN_STOCK_V30).map((sz) => ({ size: sz, stocks: mp[sz] }));
    };

    const v30CalcTot = (p) => (p && Array.isArray(p.variants)) ? p.variants.reduce((s, v) => s + Object.values(v?.stocks || {}).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0), 0) : 0;

    const v30Sanitize = (list) => {
      if (!Array.isArray(list)) return [];
      const out = [];
      list.forEach((p) => {
        if (!p) return;
        const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
        if (!code) return;
        const r = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
        if (r) { if (r.origPrice > 0) p.origPrice = Number(r.origPrice); if (r.salePrice > 0) p.salePrice = Number(r.salePrice); p.discountPct = getProductDiscountPct(p); }
        v30CleanItemMedia(p);
        p.variants = v30FilterVars(p.variants);
        if (p.variants.length > 0 && v30CalcTot(p) >= MIN_STOCK_V30) out.push(p);
      });
      return out;
    };

    ensureDefaultSelectedSizes = function() {
      if (!Array.isArray(PRODUCTS)) return;
      PRODUCTS.forEach((p) => {
        if (!p.variants || !p.variants.length) { delete selectedVariantBySku[p.sku]; return; }
        const cur = p.variants.find((v) => v.size === selectedVariantBySku[p.sku]);
        if (!cur || getVariantTotal(cur) < MIN_STOCK_V30) selectedVariantBySku[p.sku] = p.variants[0].size;
      });
    };

    const v30Enforce = () => {
      v30ApplyPrices(SALE_RULES_MASTER);
      if (Array.isArray(BASELINE_PRODUCTS)) BASELINE_PRODUCTS = v30Sanitize(BASELINE_PRODUCTS);
      if (Array.isArray(PRODUCTS)) PRODUCTS = v30Sanitize(PRODUCTS);
      ensureDefaultSelectedSizes();
      const stTxt = document.getElementById("onlyInStockText");
      if (stTxt) stTxt.textContent = currentLang === "VI" ? "Tồn ≥ 3" : "Stock ≥ 3";
      if (Array.isArray(cart) && cart.length) {
        let adj = false;
        cart = cart.filter((c) => {
          const mp = (PRODUCTS || []).find((x) => x.sku === c.sku);
          if (!mp) { adj = true; return false; }
          c.origPrice = mp.origPrice; c.finalPrice = getProductSalePrice(mp); c.discountPct = getProductDiscountPct(mp);
          const mx = getMaxStockForSkuSize(c.sku, c.size);
          if (mx < MIN_STOCK_V30) { adj = true; return false; }
          if (c.qty > mx) { c.qty = mx; adj = true; }
          return true;
        });
        if (adj) { persistCart(); updateCartBar(); }
      }
    };

    const _preV30Merge = mergeAndApplySaleRules;
    mergeAndApplySaleRules = function(liveList, rulesObj) {
      v30ApplyPrices(SALE_RULES_MASTER); v30ApplyPrices(rulesObj);
      const cleaned = v30Sanitize(Array.isArray(liveList) ? liveList : []);
      const baseR = (rulesObj && Object.keys(rulesObj).length) ? rulesObj : SALE_RULES_MASTER;
      const scoped = {};
      if (baseR) {
        const setK = new Set(cleaned.map((x) => normalizeBaseCode(String(x?.sku || "").split("-")[0])));
        Object.keys(baseR).forEach((k) => { if (setK.has(k)) scoped[k] = baseR[k]; });
      }
      return v30Sanitize(_preV30Merge(cleaned, scoped));
    };

    const _preV30Render = renderCatalog;
    renderCatalog = function() { v30Enforce(); _preV30Render(); };

    const _preV30Lang = setLang;
    setLang = function(l) {
      _preV30Lang(l);
      v30Enforce();
      if (document.getElementById("billModal")?.classList.contains("open")) renderBillModal();
    };

    const v30StampMins = (s) => {
      const m = String(s || "").trim().match(/^(\d{1,2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})/);
      return m ? ((((Number(m[4]) * 32 + Number(m[3])) * 24 + Number(m[1])) * 60) + Number(m[2])) : 0;
    };

    refreshLiveSapoStock = async function(silent = true) {
      if (v30SyncBusy) return false;
      v30SyncBusy = true;
      const timeEl = document.getElementById("liveSyncTimeText");
      const badgeEl = document.getElementById("liveSyncBadge");
      const dotEl = badgeEl ? badgeEl.querySelector(".live-sync-dot") : null;
      if (!silent && timeEl) timeEl.textContent = currentLang === "VI" ? "Đang kiểm tra Sapo..." : "Checking Sapo...";
      try {
        let data = null;
        if (location.protocol.startsWith("http")) {
          try {
            const r = await fetch("./api/stock?" + (!silent ? "force=1&" : "") + "t=" + Date.now(), { cache: "no-store" });
            if (r.ok && (r.headers.get("content-type") || "").includes("application/json")) data = await r.json();
          } catch (_) {}
        }
        if (!data || !Array.isArray(data.products)) {
          try {
            const r = await fetch("./sapo_5_kho.json?t=" + Date.now(), { cache: "no-store" });
            if (r.ok) data = await r.json();
          } catch (_) {}
        }
        if (data && data.sync_source !== "cloudflare_edge_sapo_live" && ( !silent || Date.now() - v30LastRelMs > 45000 )) {
          v30LastRelMs = Date.now();
          try {
            const repoSlug = typeof GITHUB_REPO_SLUG !== "undefined" ? GITHUB_REPO_SLUG : "moonchuyensi-cmd/havana-sale";
            const gr = await fetch(`https://api.github.com/repos/${repoSlug}/releases/tags/live-stock?t=${Date.now()}`, { headers: { "Accept": "application/vnd.github+json" }, cache: "no-store" });
            if (gr.ok) {
              const rel = await gr.json();
              const feed = rel && rel.body ? JSON.parse(rel.body) : null;
              if (feed && feed.items && Array.isArray(feed.warehouses)) {
                const fEp = Number(feed.synced_epoch || 0), bEp = Number(data.synced_epoch || 0);
                const isNewer = (fEp > 0 && bEp > 0) ? (fEp >= bEp) : (v30StampMins(feed.synced_at) >= v30StampMins(data.synced_at));
                if (isNewer) {
                  const whIds = feed.warehouses.map((w) => w.id);
                  data.warehouses = feed.warehouses;
                  const seen = new Set();
                  (data.products || []).forEach((it) => {
                    const c = normalizeBaseCode(String(it.sku || "").split("-")[0]);
                    seen.add(c);
                    const fe = feed.items[c];
                    if (fe && Array.isArray(fe.v)) {
                      it.variants = fe.v.map(([sz, qa]) => { const st = {}; whIds.forEach((w, i) => { st[w] = Number((qa && qa[i]) || 0); }); return { size: sz || "Freesize", stocks: st }; });
                      if (fe.n) it.name = fe.n;
                      if (Array.isArray(fe.si) && fe.si.length) it.sapoImages = fe.si;
                    } else if (feed.full) it.variants = [];
                  });
                  data.synced_at = feed.synced_at || data.synced_at;
                  data.synced_epoch = fEp || bEp || 0;
                  if (typeof feed.cookie_expired === "boolean") data.sapo_cookie_expired = feed.cookie_expired;
                }
              }
            }
          } catch (_) {}
        }
        if (data && Array.isArray(data.products)) {
          if (Array.isArray(data.warehouses) && data.warehouses.length) WAREHOUSE_CONFIG = data.warehouses;
          try {
            const sr = await fetch("./sale_rules.json?t=" + Date.now(), { cache: "no-store" });
            if (sr.ok) { const rr = await sr.json(); if (rr && Object.keys(rr).length >= 10) { v30ApplyPrices(rr); SALE_RULES_MASTER = rr; } }
          } catch (_) {}
          const cleanLive = v30Sanitize(data.products);
          stripSapoImagesFromProducts(cleanLive);
          PRODUCTS = mergeAndApplySaleRules(cleanLive, SALE_RULES_MASTER);
          v30Enforce();
          const exp = Boolean(data.sapo_cookie_expired);
          if (timeEl) {
            timeEl.textContent = exp
              ? (currentLang === "VI" ? `⚠️ Cookie Sapo hết hạn (${data.synced_at || "Cũ"})` : `⚠️ Cookie Expired (${data.synced_at || "Stale"})`)
              : ((currentLang === "VI" ? "Cập nhật: " : "Synced: ") + (data.synced_at || "Live"));
          }
          if (badgeEl) { badgeEl.style.background = exp ? "var(--warn-bg)" : ""; badgeEl.style.color = exp ? "var(--warn-amber)" : ""; }
          if (dotEl) dotEl.style.background = exp ? "var(--warn-amber)" : "";
          if (!silent) showToast(exp ? `⚠️ SAPO_COOKIE hết hạn (dừng ở ${data.synced_at || "cũ"})` : `⚡ Đã đồng bộ (${data.synced_at}) • ${PRODUCTS.length} mẫu tồn ≥ 3`);
          lastSyncedStamp = data.synced_at || "";
          renderCatalog();
          updateCartBar();
          if (document.getElementById("billModal")?.classList.contains("open")) { if (!cart.length) closeBillModal(); else renderBillModal(); }
          return true;
        }
      } catch (_) {} finally { v30SyncBusy = false; }
      return false;
    };

    v30Enforce();
    renderCatalog();
    updateCartBar();
    setInterval(() => { if (document.visibilityState === "visible") refreshLiveSapoStock(true); }, 45000);
    window.addEventListener("focus", () => refreshLiveSapoStock(true), { passive: true });
    refreshLiveSapoStock(true);'''


def vn_now_str() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime("%H:%M %d/%m")


def norm_nfc(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip()


def strip_accents(s: str) -> str:
    s = str(s or "").replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").upper().strip()


def clean_base_code(val) -> str:
    m = SKU_REGEX.match(str(val or "").strip().upper().rstrip("-"))
    return m.group(1).upper() if m else ""


def filter_variants_min_stock(variants_list: list, min_stock: int = MIN_VARIANT_STOCK) -> list:
    order, stocks_map = [], {}
    for v in (variants_list or []):
        if not isinstance(v, dict):
            continue
        sz = norm_nfc(v.get("size") or "Freesize") or "Freesize"
        if sz not in stocks_map:
            order.append(sz)
            stocks_map[sz] = {}
        for wid, qty in (v.get("stocks") or {}).items():
            try:
                stocks_map[sz][wid] = stocks_map[sz].get(wid, 0) + max(0, int(qty or 0))
            except (ValueError, TypeError):
                pass
    return [{"size": sz, "stocks": stocks_map[sz]} for sz in order if sum(stocks_map[sz].values()) >= min_stock]


def calc_product_total_stock(prod: dict) -> int:
    return sum(max(0, int(q or 0)) for v in (prod.get("variants") or []) for q in (v.get("stocks") or {}).values())


def is_sapo_cdn_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u or u.startswith(("./", "images/", "videos/", "sizes/", "data:", "blob:")):
        return False
    if "raw.githubusercontent.com/moonchuyensi-cmd/havana-sale" in u:
        return False
    return bool(SAPO_IMG_RX.search(u) or u.startswith(("http://", "https://")))


def extract_sapo_product_images(sapo_prod: dict) -> list:
    urls = []
    def add_u(val):
        u = str(val or "").strip()
        if u.startswith("//"): u = "https:" + u
        elif u.startswith("/"): u = f"https://{DOMAIN}{u}"
        if u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
    im0 = sapo_prod.get("image")
    add_u(im0.get("full_path") or im0.get("src") or im0.get("path") if isinstance(im0, dict) else im0)
    for im in (sapo_prod.get("images") or []):
        add_u(im.get("full_path") or im.get("src") or im.get("path") if isinstance(im, dict) else im)
    for v in (sapo_prod.get("variants") or []):
        for vim in (v.get("images") or []):
            add_u(vim.get("full_path") or vim.get("src") or vim.get("path") if isinstance(vim, dict) else vim)
        vs = v.get("image")
        add_u(vs.get("full_path") or vs.get("src") or vs.get("path") if isinstance(vs, dict) else vs)
    return urls


def split_custom_and_sapo_media(prod: dict) -> tuple:
    custom_imgs, sapo_imgs = [], []
    raw_list = [prod.get("image")] + list(prod.get("images") or []) + list(prod.get("sapoImages") or [])
    for u in raw_list:
        s = str(u or "").strip()
        if not s or s.startswith("data:image/svg+xml"):
            continue
        if is_sapo_cdn_url(s):
            if s not in sapo_imgs: sapo_imgs.append(s)
        elif s not in custom_imgs:
            custom_imgs.append(s)
    vid = "" if is_sapo_cdn_url(prod.get("video")) else str(prod.get("video") or "").strip()
    szc = "" if is_sapo_cdn_url(prod.get("sizeChart")) else str(prod.get("sizeChart") or "").strip()
    return custom_imgs, sapo_imgs, vid, szc


def parse_money(val) -> int:
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


def parse_pct(val) -> float:
    if val is None: return 0.0
    try:
        v = float(str(val).strip().replace("%", "").replace(",", ".")) if not isinstance(val, (int, float)) else float(val)
        return v * 100.0 if 0 < v <= 1.0 else v
    except ValueError:
        return 0.0


def build_sku_matcher(codes: list):
    out = []
    for c in sorted(set(x.upper() for x in codes if x), key=len, reverse=True):
        m = re.match(r"^([A-Z]+)(\d+)$", c)
        rx = re.compile(rf"(^|[^A-Z0-9]){re.escape(m.group(1))}[\s_\-]*{re.escape(m.group(2))}([^0-9]|$)", re.I) if m else re.compile(rf"(^|[^A-Z0-9]){re.escape(c)}([^A-Z0-9]|$)", re.I)
        out.append((c, rx))
    return out


def purge_wrong_custom_media_once():
    already = set()
    if os.path.exists(PURGE_STATE_FILE):
        try:
            with open(PURGE_STATE_FILE, "r", encoding="utf-8") as f:
                already = set(json.load(f) or [])
        except Exception:
            pass
    pending = FORCE_RESET_TO_SAPO_SKUS - already
    if not pending:
        return set(), []
    compiled = build_sku_matcher(list(pending))
    removed = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel_r = root.replace("\\", "/").lstrip("./")
        for fn in files:
            if fn.lower() != "keep.txt" and fn.lower().endswith(IMG_EXTS + VID_EXTS):
                if any(rx.search(fn) or (rel_r and rx.search(rel_r)) for _, rx in compiled):
                    fp = os.path.join(root, fn)
                    try:
                        os.remove(fp)
                        removed.append(fp)
                    except Exception:
                        pass
    try:
        with open(PURGE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(already | pending), f)
    except Exception:
        pass
    return pending, removed


def upgrade_repo_files_v30() -> bool:
    changed = False
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            orig = f.read()
        html = orig
        end_m = html.rfind("/* [BLOCK:APP_LOGIC_END] */")
        if end_m != -1:
            cuts = [html.find(k, 0, end_m) for k in ("/* === v22:", "/* === v23:", "/* === v25:", "/* === v26:", "/* === v27:", "/* === v28:", "/* === v29:", "/* === v30:")]
            valid = [p for p in cuts if p != -1]
            cut_at = min(valid) if valid else end_m
            html = html[:cut_at].rstrip() + "\n\n    " + V30_INDEX_JS_ADDON.strip() + "\n    /* [BLOCK:APP_LOGIC_END] */" + html[end_m + len("/* [BLOCK:APP_LOGIC_END] */"):]
        html = re.sub(r'(<span id="onlyInStockText">)(?:Còn tồn|In Stock)(</span>)', r'\1Tồn ≥ 3\2', html, count=1)
        if html != orig:
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html)
            changed = True
    return changed


def extract_smart_secret(raw_val: str) -> str:
    raw = str(raw_val or "").strip()
    if not raw: return ""
    mb = re.search(r"(?:-b|--cookie)\s+(['\"])(.+?)\1", raw, flags=re.S)
    mh = re.search(r"(?:-H|--header)\s+(['\"])cookie\s*:\s*(.+?)\1", raw, flags=re.I | re.S)
    ml = re.search(r"(?:^|[\r\n])cookie\s*:\s*([^\r\n]+)", raw, flags=re.I)
    if mb: raw = mb.group(2)
    elif mh: raw = mh.group(2)
    elif ml: raw = ml.group(1)
    if "^" in raw and ("sapo-admin" in raw or "AspNetCore" in raw):
        raw = re.sub(r"\^([\"%&|<>^])", r"\1", raw)
    s = re.sub(r"[\r\n]+", " ", raw.strip().strip('"').strip("'")).strip()
    for pfx in ("cookie:", "x-sapo-access-token:"):
        if s.lower().startswith(pfx): s = s[len(pfx):].strip()
    return "".join(ch for ch in s if 32 <= ord(ch) <= 126)


def build_header_candidates() -> list:
    cands = []
    base = {"accept": "application/json, text/plain, */*", "accept-language": "vi", "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
    ck = extract_smart_secret(os.environ.get("SAPO_COOKIE", ""))
    tk = extract_smart_secret(os.environ.get("SAPO_ACCESS_TOKEN", ""))
    if ck:
        cands.append(("SAPO_COOKIE", {**base, "cookie": ck, "referer": f"https://{DOMAIN}/admin/products", "x-sapo-client": "sapo-frontend-v3", "x-sapo-serviceid": "sapo-frontend-v3"}))
    if tk:
        if "sapo-admin=" in tk or ";" in tk:
            cands.append(("SAPO_ACCESS_TOKEN(cookie)", {**base, "cookie": tk, "referer": f"https://{DOMAIN}/admin/products", "x-sapo-client": "sapo-frontend-v3", "x-sapo-serviceid": "sapo-frontend-v3"}))
        else:
            cands.append(("SAPO_ACCESS_TOKEN", {**base, "X-Sapo-Access-Token": tk}))
    return cands


def load_black_friday_rules() -> dict:
    sale_rules = {}
    if os.path.exists("sale_rules.json"):
        try:
            with open("sale_rules.json", "r", encoding="utf-8") as f:
                sale_rules = json.load(f)
        except Exception:
            pass
    if not sale_rules and os.path.exists("Black Friday.xlsx"):
        try:
            import openpyxl
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
            import openpyxl
        wb = openpyxl.load_workbook("Black Friday.xlsx", data_only=True)
        for sname in ("Tổng CH", "Phụ kiện"):
            if sname not in wb.sheetnames: continue
            rows = list(wb[sname].iter_rows(values_only=True))
            for r in rows[1:]:
                if not r or len(r) < 3: continue
                c = clean_base_code(r[0])
                if not c or c in EXCLUDED_SKUS: continue
                op, sp = parse_money(r[1]), parse_money(r[2])
                if sp > 0:
                    sale_rules[c] = {"origPrice": op, "salePrice": sp, "discountPct": round((1 - sp / op) * 100) if op > sp else 30, "subType": norm_nfc(r[4]) if len(r) > 4 and r[4] else PK_KNOWN_SUBTYPES.get(c, "")}
    for ex in EXCLUDED_SKUS:
        sale_rules.pop(ex, None)
    for code, (op, sp, dp, _) in PRICE_PATCH_V30.items():
        if code in sale_rules:
            sale_rules[code].update({"origPrice": op, "salePrice": sp, "discountPct": dp})
    if sale_rules:
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)
    return sale_rules


def scan_repo_media_folders(codes: list) -> dict:
    compiled = build_sku_matcher(codes)
    media_map = {}
    all_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.startswith(".") and fn.lower() not in ("keep.txt", "readme.md"):
                all_files.append(os.path.join(root, fn).replace("\\", "/").lstrip("./"))
    all_files.sort(key=lambda p: (0 if p.startswith(("images/", "videos/", "sizes/")) else 1, p.lower()))
    for rel in all_files:
        low = rel.lower()
        if not low.endswith(IMG_EXTS + VID_EXTS): continue
        fn, pd = os.path.basename(rel), os.path.dirname(rel)
        hit = next((c for c, rx in compiled if rx.search(fn)), "") or (next((c for c, rx in compiled if rx.search(pd)), "") if pd else "")
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


def map_branch(raw_name: str, loc_id: str, idx: int = 9):
    n = strip_accents(raw_name)
    if n == "HAVANA" or "HO PHI TICH" in n or "93-95" in n or "93 - 95" in n: return ("wh_havana", "HAVANA (93-95 Hồ Phi Tích)", 1)
    if "ONLINE" in n: return ("wh_online", "HAVANA ONLINE", 2)
    if "261" in n or "PHAN CHU TRINH" in n or "PHAN CHAU TRINH" in n: return ("wh_pct", "261 - 263 Phan Châu Trinh", 3)
    if "101" in n or "AU CO" in n: return ("wh_auco", "101 Âu Cơ", 4)
    if "240" in n or "LE DUAN" in n: return ("wh_leduan", "240 Lê Duẩn", 5)
    return (f"wh_{loc_id}", norm_nfc(raw_name) or f"Chi nhánh {loc_id}", idx)


def infer_category(code: str, pname: str, sub_type: str = "") -> str:
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


def fetch_json(path: str, headers: dict) -> dict:
    req = urllib.request.Request(f"https://{DOMAIN}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        if raw.lstrip().startswith("<"):
            raise RuntimeError("Sapo Cookie het han!")
        return json.loads(raw)


def write_outputs(wh_list: list, prods: list, synced_at: str, rules: dict, epoch: int, expired: bool):
    filtered = []
    for p in prods:
        cv = filter_variants_min_stock(p.get("variants"), MIN_VARIANT_STOCK)
        if cv:
            pc = dict(p, variants=cv)
            if calc_product_total_stock(pc) >= MIN_TOTAL_STOCK:
                filtered.append(pc)
    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump({"min_stock_rule": MIN_TOTAL_STOCK, "min_variant_stock_rule": MIN_VARIANT_STOCK, "synced_at": synced_at, "synced_epoch": int(epoch or 0), "sapo_cookie_expired": bool(expired), "warehouses": wh_list, "products": filtered}, f, ensure_ascii=False, indent=2)
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            h = f.read()
        s_i, e_i = h.find("/* [BLOCK:DATA_PAYLOAD_START] */"), h.find("/* [BLOCK:DATA_PAYLOAD_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_PAYLOAD_END] */")
            blk = f"/* [BLOCK:DATA_PAYLOAD_START] */\n    let WAREHOUSE_CONFIG = {json.dumps(wh_list, ensure_ascii=False)};\n    let SALE_RULES_MASTER = {json.dumps(rules or {}, ensure_ascii=False)};\n    let BASELINE_PRODUCTS = {json.dumps(filtered, ensure_ascii=False)};\n    let PRODUCTS = BASELINE_PRODUCTS.map((p) => Object.assign({{}}, p));\n    /* [BLOCK:DATA_PAYLOAD_END] */"
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(h[:s_i] + blk + h[e_i:])


def publish_live_release(wh_list: list, prods: list, synced_at: str, epoch: int, expired: bool):
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
        if c in FORCE_RESET_TO_SAPO_SKUS and p.get("sapoImages"):
            ent["si"] = list(p["sapoImages"])[:5]
        items[c] = ent
    body = json.dumps({"v": 30, "full": 1, "cookie_expired": bool(expired), "synced_at": synced_at, "synced_epoch": int(epoch or 0), "warehouses": wh_list, "items": items}, ensure_ascii=False, separators=(",", ":"))
    hdrs = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "havana-v30"}
    rel_id = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/live-stock", headers=hdrs), timeout=15) as r:
            rel_id = json.loads(r.read().decode("utf-8")).get("id")
    except Exception:
        pass
    data = json.dumps({"tag_name": "live-stock", "name": f"Live Sapo ({synced_at})", "body": body, "draft": False, "prerelease": True}).encode("utf-8")
    try:
        url = f"https://api.github.com/repos/{repo}/releases/{rel_id}" if rel_id else f"https://api.github.com/repos/{repo}/releases"
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdrs, method="PATCH" if rel_id else "POST"), timeout=20) as r:
            r.read()
        return True
    except Exception:
        return False


def main():
    purged_now, removed_files = purge_wrong_custom_media_once()
    upgraded_html = upgrade_repo_files_v30()
    sale_rules = load_black_friday_rules()

    old_wh, old_prods, old_stamp, old_epoch = [], [], "21:18 21/11", 0
    if os.path.exists("sapo_5_kho.json"):
        try:
            with open("sapo_5_kho.json", "r", encoding="utf-8") as f:
                od = json.load(f)
            old_wh = od.get("warehouses") or []
            old_prods = od.get("products") or []
            old_stamp = str(od.get("synced_at") or old_stamp)
            old_epoch = int(od.get("synced_epoch") or 0)
        except Exception:
            pass

    preserved = {}
    for op in old_prods:
        ci, si, vd, sz = split_custom_and_sapo_media(op)
        c = clean_base_code(str(op.get("sku") or "").split("-")[0])
        if c:
            preserved[c] = {"custom": [] if c in FORCE_RESET_TO_SAPO_SKUS else ci, "sapo": si, "vid": "" if c in FORCE_RESET_TO_SAPO_SKUS else vd, "sz": "" if c in FORCE_RESET_TO_SAPO_SKUS else sz}

    all_sapo, loc_meta, active_hdrs = [], {}, None
    for lbl, hdrs in build_header_candidates():
        try:
            p1 = fetch_json("/admin/products.json?page=1&limit=250", hdrs)
            b1 = p1.get("products") or []
            all_sapo.extend(b1)
            active_hdrs = hdrs
            try:
                ld = fetch_json("/admin/locations.json", hdrs)
                for idx, loc in enumerate(ld.get("locations") or []):
                    rid = str(loc["id"])
                    rname = loc.get("label") or loc.get("name") or ""
                    wid, wlbl, word = map_branch(rname, rid, 10 + idx)
                    loc_meta[rid] = {"id": wid, "sapo_name": rname, "name": wlbl, "order": word}
            except Exception:
                pass
            if len(b1) == 250:
                tot = int((p1.get("metadata") or {}).get("total") or 0)
                max_p = min(40, (tot + 249) // 250) if tot > 250 else 15
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    for b_res in pool.map(lambda pg: fetch_json(f"/admin/products.json?page={pg}&limit=250", hdrs), range(2, max_p + 1)):
                        all_sapo.extend(b_res.get("products") or [])
            print(f"[+] Ket noi Sapo OK ({lbl}): {len(all_sapo)} sp")
            break
        except Exception as e:
            print(f"[!] {lbl} loi: {e}")

    repo_media = scan_repo_media_folders(list(sale_rules.keys()))

    if not all_sapo:
        for ep in old_prods:
            c = clean_base_code(str(ep.get("sku") or "").split("-")[0])
            if c in sale_rules:
                ep["origPrice"] = sale_rules[c]["origPrice"] or ep.get("origPrice", 299000)
                ep["salePrice"] = sale_rules[c]["salePrice"] or ep.get("salePrice", 159000)
                ep["discountPct"] = max(1, round((1 - ep["salePrice"] / ep["origPrice"]) * 100))
            rm = repo_media.get(c, {})
            pr = preserved.get(c, {})
            c_imgs = [] if c in FORCE_RESET_TO_SAPO_SKUS else (rm.get("images") or pr.get("custom") or [])
            s_imgs = pr.get("sapo") or []
            fin = c_imgs if c_imgs else s_imgs
            ep.update({"sapoImages": s_imgs, "images": fin, "image": fin[0] if fin else "", "video": rm.get("video") or pr.get("vid") or "", "sizeChart": rm.get("sizeChart") or pr.get("sz") or ""})
        wh_out = old_wh or [{"id": "wh_havana", "name": "HAVANA (93-95 Hồ Phi Tích)"}, {"id": "wh_online", "name": "HAVANA ONLINE"}, {"id": "wh_pct", "name": "261 - 263 Phan Châu Trinh"}, {"id": "wh_auco", "name": "101 Âu Cơ"}, {"id": "wh_leduan", "name": "240 Lê Duẩn"}]
        publish_live_release(wh_out, old_prods, old_stamp, old_epoch, expired=True)
        if upgraded_html or purged_now or removed_files or os.environ.get("GITHUB_EVENT_NAME") != "schedule":
            if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
            write_outputs(wh_out, old_prods, old_stamp, sale_rules, old_epoch, expired=True)
        else:
            with open(".skip_git_commit", "w", encoding="utf-8") as sf: sf.write(old_stamp)
        print("[!] Cookie Sapo dang het han — da cap nhat giao dien Bill v30 thanh cong!")
        return

    grouped = {}
    for p in all_sapo:
        vraw = p.get("variants") or []
        if not vraw: continue
        ntoks = [t for t in reversed(re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_nfc(p.get("name") or "").upper())) if t in sale_rules]
        base = ntoks[0] if ntoks else next((clean_base_code(str(v.get("sku") or "").split("-")[0]) for v in vraw if clean_base_code(str(v.get("sku") or "").split("-")[0]) in sale_rules), "")
        if not base or base in EXCLUDED_SKUS: continue
        rule = sale_rules[base]
        op = rule.get("origPrice") or parse_money(vraw[0].get("variant_retail_price") or 299000)
        sp = rule.get("salePrice") or 159000
        dp = max(1, round((1 - sp / op) * 100)) if op > sp > 0 else 30
        s_imgs = extract_sapo_product_images(p)
        if base in FORCE_RESET_TO_SAPO_SKUS and not s_imgs and active_hdrs and p.get("id"):
            try:
                s_imgs = extract_sapo_product_images(fetch_json(f"/admin/products/{p['id']}.json", active_hdrs).get("product") or {})
            except Exception:
                pass
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
            rm, pr = repo_media.get(base, {}), preserved.get(base, {})
            c_imgs = [] if base in FORCE_RESET_TO_SAPO_SKUS else (rm.get("images") or pr.get("custom") or [])
            all_si = s_imgs if s_imgs else (pr.get("sapo") or [])
            fin = c_imgs if c_imgs else all_si
            grouped[base] = {"sku": base, "name": pname, "subType": rule.get("subType", ""), "category": infer_category(base, pname, rule.get("subType", "")), "origPrice": op, "salePrice": sp, "discountPct": dp, "image": fin[0] if fin else "", "images": fin, "sapoImages": all_si, "video": rm.get("video") or pr.get("vid") or "", "sizeChart": rm.get("sizeChart") or pr.get("sz") or "", "variants": v_list}

    wh_sorted = [{k: v for k, v in it.items() if k != "order"} for it in sorted(loc_meta.values(), key=lambda x: x["order"])]
    now_st, now_ep = vn_now_str(), int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    prods_all = list(grouped.values())
    publish_live_release(wh_sorted, prods_all, now_st, now_ep, expired=False)
    if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
    write_outputs(wh_sorted, prods_all, now_st, sale_rules, now_ep, expired=False)
    print(f"[+] Hoan tat Auto-Sync v30 LIVE ({now_st})!")


if __name__ == "__main__":
    main()
# === HET FILE V30 - DA COPY DU 100% ===
