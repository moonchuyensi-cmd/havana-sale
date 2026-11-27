import os, re, sys, glob, json, shutil, datetime, concurrent.futures, subprocess, unicodedata
import urllib.error, urllib.parse, urllib.request
import zipfile as zf_mod

# ==============================================================================
# AUTO_SYNC_SAPO.PY (v35 - TU DONG DOC "ATH ONL.xlsx" + QUET ANH ATH/BLACKFRIDAY)
# Giu nguyen Xa Ngay Cuoi + Dong ho dem nguoc + Loc bien the & ma ton >= 3
# ==============================================================================

MIN_TOTAL_STOCK = 3
MIN_VARIANT_STOCK = 3
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
FORCE_RESET_TO_SAPO_SKUS = {"SAT532", "CV538"}
PRICE_PATCH_V35 = {
    "AO1010": (194000, 79000, 59),
    "AO726":  (225000, 39000, 83),
    "AO1016": (214000, 79000, 63),
}
SUFFIX_COLOR_MAP = {
    "D": "Đen", "DEN": "Đen", "T": "Trắng", "TR": "Trắng", "XDU": "Xanh Dương",
    "XDE": "Xanh Đen", "XLA": "Xanh Lá", "XA": "Xám", "XM": "Xám", "H": "Hồng",
    "V": "Vàng", "N": "Nâu", "K": "Kem", "G": "Ghi", "DO": "Đỏ", "TI": "Tím",
    "R": "Rêu", "REU": "Rêu", "B": "Be", "BE": "Be", "C": "Cam", " Kem": "Kem",
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

V35_INDEX_JS_ADDON = r'''/* === v35: "XA NGAY CUOI" + LIVE COUNTDOWN + ATH ONL & MEDIA AUTO-DISCOVERY === */
    const MIN_STOCK_V35 = 3;
    const FORCE_SAPO_V35 = new Set(["SAT532", "CV538"]);
    const FINAL_DEADLINE_V35 = new Date("2026-11-27T23:59:59+07:00").getTime();
    const BILL_NOTE_VI_V35 = "📌 Lưu ý: Chương trình xả ngày cuối chỉ áp dụng cho Online. Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";
    const BILL_NOTE_EN_V35 = "📌 Note: Final day clearance is valid for Online orders only. Sale items have limited stock and may slightly differ from actual inventory.";
    let v35SyncBusy = false, v35LastRelMs = 0;

    if (!document.getElementById("v35CountdownStyle")) {
      const st = document.createElement("style");
      st.id = "v35CountdownStyle";
      st.textContent = `
        .v35-title-wrap{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:3px}
        #mainBrandTitle,#subBrandText{color:#be123c!important;font-weight:800!important}
        .v35-countdown-pill{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#e11d48 0%,#9f1239 100%);color:#fff;padding:4px 10px;border-radius:999px;box-shadow:0 4px 14px rgba(225,29,72,.38);border:1px solid rgba(255,255,255,.35);font-variant-numeric:tabular-nums;animation:v35Pulse 1.9s ease-in-out infinite;white-space:nowrap}
        .v35-cd-label{font-size:11px;font-weight:800;letter-spacing:.02em;color:#ffe4e6;text-transform:uppercase}
        .v35-cd-digits{display:inline-flex;align-items:center;gap:3px}
        .v35-cd-box{background:rgba(15,23,42,.44);color:#fff;font-weight:800;font-size:12.5px;min-width:24px;height:21px;padding:0 4px;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,.24);font-variant-numeric:tabular-nums}
        .v35-cd-sep{font-weight:800;font-size:12px;color:#fecdd3}
        @keyframes v35Pulse{0%,100%{transform:scale(1);box-shadow:0 4px 14px rgba(225,29,72,.34)}50%{transform:scale(1.025);box-shadow:0 6px 20px rgba(225,29,72,.58)}}
        @media(max-width:768px){
          .brand>div>div:last-child{flex-wrap:wrap!important;row-gap:3px!important}
          .brand-title{font-size:14.5px!important}
          .v35-title-wrap{gap:6px}
          .v35-countdown-pill{padding:3px 8px;gap:4px}
          .v35-cd-label{font-size:10px}
          .v35-cd-box{font-size:11px;min-width:20px;height:18px;padding:0 3px;border-radius:5px}
        }
      `;
      document.head.appendChild(st);
    }

    const v35EnsureCountdown = () => {
      const mainT = document.getElementById("mainBrandTitle");
      if (!mainT) return;
      let wrap = mainT.parentElement;
      if (!wrap || !wrap.classList.contains("v35-title-wrap")) {
        wrap = document.createElement("div");
        wrap.className = "v35-title-wrap";
        mainT.parentNode.insertBefore(wrap, mainT);
        wrap.appendChild(mainT);
      }
      if (!document.getElementById("v35CountdownBadge")) {
        const b = document.createElement("div");
        b.id = "v35CountdownBadge";
        b.className = "v35-countdown-pill";
        b.innerHTML = `<span class="v35-cd-label" id="v35CdLabel">⏳ Kết thúc sau</span><span class="v35-cd-digits"><span class="v35-cd-box" id="v35CdH">00</span><span class="v35-cd-sep">:</span><span class="v35-cd-box" id="v35CdM">00</span><span class="v35-cd-sep">:</span><span class="v35-cd-box" id="v35CdS">00</span></span>`;
        wrap.appendChild(b);
      }
    };

    const v35TickCountdown = () => {
      v35EnsureCountdown();
      const now = Date.now();
      let targetMs = FINAL_DEADLINE_V35;
      if (now > targetMs) {
        const vn = new Date(now + 7 * 3600000);
        targetMs = Date.UTC(vn.getUTCFullYear(), vn.getUTCMonth(), vn.getUTCDate(), 23, 59, 59) - 7 * 3600000;
      }
      const diff = Math.max(0, Math.floor((targetMs - now) / 1000));
      const pad = (n) => String(n).padStart(2, "0");
      const hEl = document.getElementById("v35CdH"), mEl = document.getElementById("v35CdM"), sEl = document.getElementById("v35CdS"), lEl = document.getElementById("v35CdLabel");
      if (hEl) hEl.textContent = pad(Math.floor(diff / 3600));
      if (mEl) mEl.textContent = pad(Math.floor((diff % 3600) / 60));
      if (sEl) sEl.textContent = pad(diff % 60);
      if (lEl) lEl.textContent = currentLang === "EN" ? "⏳ Ends in" : "⏳ Kết thúc sau";
    };

    if (typeof RESET_TO_SAPO_SKUS_V21 !== "undefined" && RESET_TO_SAPO_SKUS_V21 && RESET_TO_SAPO_SKUS_V21.add) {
      RESET_TO_SAPO_SKUS_V21.add("CV538");
      RESET_TO_SAPO_SKUS_V21.add("SAT532");
    }
    try { ["havana_gh_tree_v13","havana_live_rel_v33","havana_live_rel_v34"].forEach((k) => sessionStorage.removeItem(k)); } catch (_) {}
    if (typeof MEDIA_CACHE_VERSION !== "undefined" && MEDIA_CACHE_VERSION < 35) MEDIA_CACHE_VERSION = 35;

    const v35ApplyBranding = () => {
      v35EnsureCountdown();
      const en = currentLang === "EN";
      const mainT = document.getElementById("mainBrandTitle"), subT = document.getElementById("subBrandText");
      const badgeEl = document.querySelector("#billModal .bill-head-badge"), titleEl = document.getElementById("billModalTitle");
      const openBtn = document.getElementById("openBillBtn"), noteInp = document.getElementById("custNoteInput");
      if (mainT) mainT.textContent = en ? "FINAL DAY CLEARANCE" : "XẢ NGÀY CUỐI";
      if (subT) subT.textContent = en ? "(Online Exclusive Only)" : "(Chỉ áp dụng cho Online)";
      if (badgeEl) badgeEl.textContent = en ? "FINAL DAY SALE • ONLINE ONLY" : "XẢ NGÀY CUỐI • CHỈ ÁP DỤNG CHO ONLINE";
      if (titleEl) titleEl.textContent = en ? "Provisional Order Slip" : "Phiếu Lên Đơn Tạm";
      if (openBtn) openBtn.textContent = en ? "Draft Order & Send" : "Xem Đơn Tạm & Gửi Zalo";
      if (noteInp) noteInp.placeholder = en ? "Shipping address & order note (Online only)..." : "Địa chỉ nhận ship & Ghi chú đơn hàng (Chỉ áp dụng cho Online)...";
      document.title = "HAVANA — Xả Ngày Cuối (Chỉ Áp Dụng Cho Online)";
      v35TickCountdown();
    };

    const _preV35CartBar = updateCartBar;
    updateCartBar = function() {
      _preV35CartBar();
      const sumTitle = document.getElementById("cartSummaryTitle");
      if (sumTitle && currentLang === "VI" && sumTitle.textContent.startsWith("Phiếu Bill:")) {
        sumTitle.textContent = sumTitle.textContent.replace("Phiếu Bill:", "Đơn tạm:");
      }
      v35ApplyBranding();
    };

    const _preV35RenderBill = renderBillModal;
    renderBillModal = function() {
      _preV35RenderBill();
      v35ApplyBranding();
      const sumEl = document.getElementById("billSummaryBox");
      if (sumEl) {
        let nd = sumEl.querySelector(".bill-stock-disclaimer");
        if (!nd) {
          nd = document.createElement("div");
          nd.className = "bill-stock-disclaimer";
          nd.style.cssText = "margin-top:8px;padding:9px 12px;border-radius:11px;background:#fffbeb;border:1px dashed #f59e0b;color:#92400e;font-size:12.5px;line-height:1.45;font-weight:600;";
          sumEl.appendChild(nd);
        }
        nd.textContent = currentLang === "EN" ? BILL_NOTE_EN_V35 : BILL_NOTE_VI_V35;
      }
    };

    const _preV35BillTxt = buildFormattedBillText;
    buildFormattedBillText = function() {
      let txt = _preV35BillTxt().replace(/🧾 PHIẾU [^\n]+/, "🧾 PHIẾU LÊN ĐƠN TẠM — XẢ NGÀY CUỐI (CHỈ ÁP DỤNG CHO ONLINE)");
      txt = txt.replace(/\n--------------------------\n📌 [^\n]+$/, "");
      return txt + "\n--------------------------\n" + (currentLang === "EN" ? BILL_NOTE_EN_V35 : BILL_NOTE_VI_V35);
    };

    const v35RoundRect = (ctx, x, y, w, h, rad) => {
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, rad);
      else { const r = Array.isArray(rad) ? rad[0] : rad; ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r); ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath(); }
    };

    downloadBillImage = function() {
      if (!cart.length) return;
      saveCustomerDraft();
      const custName = (document.getElementById("custNameInput")?.value || "").trim() || (currentLang === "EN" ? "Havana Guest" : "Khách lẻ Havana");
      const custPhone = (document.getElementById("custPhoneInput")?.value || "").trim();
      const custNote = (document.getElementById("custNoteInput")?.value || "").trim();
      const nowStr = new Date().toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" });
      const { totalQty, totalOrig, totalSale, totalSaved } = getCartTotals();
      const W = 760, rowH = 62, fnt = "'Be Vietnam Pro', -apple-system, sans-serif";
      const rawNote = currentLang === "EN"
        ? "* Note: Final day clearance is valid for Online orders only. Limited stock may slightly differ from actual inventory."
        : "* Lưu ý: Chương trình xả ngày cuối chỉ áp dụng cho Online. Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách thông cảm ạ!";
      const tCtx = document.createElement("canvas").getContext("2d");
      tCtx.font = `600 13.5px ${fnt}`;
      const lines = []; let cur = "";
      rawNote.split(/\s+/).forEach((w) => { const c = cur ? cur + " " + w : w; if (tCtx.measureText(c).width > W - 132 && cur) { lines.push(cur); cur = w; } else cur = c; });
      if (cur) lines.push(cur);
      const nBoxH = 16 + lines.length * 20, H = 314 + cart.length * rowH + (custNote ? 26 : 0) + nBoxH;
      const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
      const ctx = cv.getContext("2d");
      ctx.fillStyle = "#f2f9f8"; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = "#fff"; ctx.strokeStyle = "#91d2ce"; ctx.lineWidth = 3; v35RoundRect(ctx, 24, 24, W - 48, H - 48, 22); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#165954"; v35RoundRect(ctx, 24, 24, W - 48, 86, [22, 22, 0, 0]); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.font = `bold 24px ${fnt}`; ctx.fillText("HAVANA — PHIẾU LÊN ĐƠN TẠM", 52, 65);
      ctx.fillStyle = "#bbf7d0"; ctx.font = `600 14px ${fnt}`; ctx.fillText("Thời gian: " + nowStr + " • Xả ngày cuối (Chỉ áp dụng cho Online)", 52, 92);
      let y = 142;
      ctx.fillStyle = "#11302e"; ctx.font = `bold 15px ${fnt}`; ctx.fillText("Khách hàng: " + custName + (custPhone ? "   |   SĐT: " + custPhone : ""), 52, y);
      if (custNote) { y += 26; ctx.fillStyle = "#52706e"; ctx.font = `500 14px ${fnt}`; ctx.fillText("Địa chỉ / Ghi chú: " + custNote.slice(0, 68), 52, y); }
      y += 22; ctx.strokeStyle = "#cdebe8"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(52, y); ctx.lineTo(W - 52, y); ctx.stroke();
      y += 30;
      cart.forEach((it, i) => {
        const q = Number(it.qty || 1), ls = Number(it.finalPrice || 0) * q;
        ctx.fillStyle = "#11302e"; ctx.font = `bold 15px ${fnt}`; ctx.fillText((`${i + 1}. [${it.sku}] ` + it.name).slice(0, 48), 52, y);
        ctx.fillStyle = "#52706e"; ctx.font = `600 13.5px ${fnt}`; ctx.fillText(`Size: ${it.size}   •   SL: ${q} x ${formatVND(it.finalPrice)}`, 52, y + 22);
        ctx.fillStyle = "#be123c"; ctx.font = `bold 16px ${fnt}`; const pt = formatVND(ls); ctx.fillText(pt, W - 52 - ctx.measureText(pt).width, y + 10);
        y += rowH;
      });
      ctx.strokeStyle = "#91d2ce"; ctx.beginPath(); ctx.moveTo(52, y - 10); ctx.lineTo(W - 52, y - 10); ctx.stroke();
      y += 18; ctx.fillStyle = "#52706e"; ctx.font = `600 14.5px ${fnt}`; ctx.fillText(`Tổng SL: ${totalQty} sp   |   Giá gốc: ${formatVND(totalOrig)}   |   Tiết kiệm: -${formatVND(totalSaved)}`, 52, y);
      y += 36; ctx.fillStyle = "#165954"; ctx.font = `bold 20px ${fnt}`; ctx.fillText("TỔNG THANH TOÁN:", 52, y);
      ctx.fillStyle = "#be123c"; ctx.font = `bold 24px ${fnt}`; const ts = formatVND(totalSale); ctx.fillText(ts, W - 52 - ctx.measureText(ts).width, y);
      const ny = y + 18;
      ctx.fillStyle = "#fffbeb"; ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 1.2; v35RoundRect(ctx, 52, ny, W - 104, nBoxH, 11); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#92400e"; ctx.font = `600 13.5px ${fnt}`; lines.forEach((l, idx) => ctx.fillText(l, 66, ny + 22 + idx * 20));
      const a = document.createElement("a"); a.download = "Phieu_Len_Don_Tam_Havana_" + Date.now().toString().slice(-5) + ".png"; a.href = cv.toDataURL("image/png"); a.click();
      showToast("🖼️ Đã tải ảnh Phiếu Lên Đơn Tạm nét cao về máy!");
    };

    const v35Prices = (rm) => {
      if (!rm || typeof rm !== "object") return;
      const fx = { AO1010: { origPrice: 194000, salePrice: 79000, discountPct: 59 }, AO726: { origPrice: 225000, salePrice: 39000, discountPct: 83 }, AO1016: { origPrice: 214000, salePrice: 79000, discountPct: 63 } };
      Object.keys(fx).forEach((k) => { if (rm[k]) Object.assign(rm[k], fx[k]); });
    };

    const v35Vars = (arr) => {
      if (!Array.isArray(arr)) return [];
      const ord = [], mp = {};
      arr.forEach((v) => {
        if (!v) return;
        const sz = String(v.size || "Freesize").trim() || "Freesize";
        if (!mp[sz]) { ord.push(sz); mp[sz] = {}; }
        Object.entries(v.stocks || {}).forEach(([w, q]) => { mp[sz][w] = (mp[sz][w] || 0) + Math.max(0, Number(q) || 0); });
      });
      return ord.filter((sz) => Object.values(mp[sz]).reduce((a, b) => a + b, 0) >= MIN_STOCK_V35).map((sz) => ({ size: sz, stocks: mp[sz] }));
    };

    const v35Sanitize = (list) => {
      if (!Array.isArray(list)) return [];
      const out = [];
      list.forEach((p) => {
        if (!p) return;
        const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
        if (!code) return;
        const r = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
        if (r) { if (r.origPrice > 0) p.origPrice = Number(r.origPrice); if (r.salePrice > 0) p.salePrice = Number(r.salePrice); p.discountPct = getProductDiscountPct(p); }
        if (FORCE_SAPO_V35.has(code) && !(typeof sessionFreshUploadedSkus !== "undefined" && sessionFreshUploadedSkus && sessionFreshUploadedSkus.has(code))) {
          if (typeof repoLiveMediaBySku === "object" && repoLiveMediaBySku) delete repoLiveMediaBySku[code];
          const urls = [...(p.sapoImages || []), ...(p.images || []), p.image].map((u) => String(u || "").trim()).filter((s) => s && isSapoImageUrl(s) && !(code === "SAT532" && s.toLowerCase().includes("sat533")));
          const uniq = [...new Set(urls)];
          p.sapoImages = uniq; p.images = uniq; p.image = uniq[0] || ""; p.video = ""; p.sizeChart = ""; p.hasCustomMedia = false;
        }
        p.variants = v35Vars(p.variants);
        const tot = p.variants.reduce((s, v) => s + Object.values(v.stocks || {}).reduce((a, b) => a + b, 0), 0);
        if (p.variants.length > 0 && tot >= MIN_STOCK_V35) out.push(p);
      });
      return out;
    };

    ensureDefaultSelectedSizes = function() {
      if (!Array.isArray(PRODUCTS)) return;
      PRODUCTS.forEach((p) => {
        if (!p.variants || !p.variants.length) { delete selectedVariantBySku[p.sku]; return; }
        const cur = p.variants.find((v) => v.size === selectedVariantBySku[p.sku]);
        if (!cur || getVariantTotal(cur) < MIN_STOCK_V35) selectedVariantBySku[p.sku] = p.variants[0].size;
      });
    };

    const v35Enforce = () => {
      v35Prices(SALE_RULES_MASTER);
      if (Array.isArray(BASELINE_PRODUCTS)) BASELINE_PRODUCTS = v35Sanitize(BASELINE_PRODUCTS);
      if (Array.isArray(PRODUCTS)) PRODUCTS = v35Sanitize(PRODUCTS);
      ensureDefaultSelectedSizes();
      v35ApplyBranding();
      const stTxt = document.getElementById("onlyInStockText");
      if (stTxt) stTxt.textContent = currentLang === "VI" ? "Tồn ≥ 3" : "Stock ≥ 3";
      if (Array.isArray(cart) && cart.length) {
        let adj = false;
        cart = cart.filter((c) => {
          const mp = (PRODUCTS || []).find((x) => x.sku === c.sku);
          if (!mp) { adj = true; return false; }
          c.origPrice = mp.origPrice; c.finalPrice = getProductSalePrice(mp); c.discountPct = getProductDiscountPct(mp);
          const mx = getMaxStockForSkuSize(c.sku, c.size);
          if (mx < MIN_STOCK_V35) { adj = true; return false; }
          if (c.qty > mx) { c.qty = mx; adj = true; }
          return true;
        });
        if (adj) { persistCart(); updateCartBar(); }
      }
    };

    const _preV35Merge = mergeAndApplySaleRules;
    mergeAndApplySaleRules = function(liveList, rulesObj) {
      v35Prices(SALE_RULES_MASTER); v35Prices(rulesObj);
      const cl = v35Sanitize(Array.isArray(liveList) ? liveList : []);
      const br = (rulesObj && Object.keys(rulesObj).length) ? rulesObj : SALE_RULES_MASTER;
      const sc = {};
      if (br) { const ks = new Set(cl.map((x) => normalizeBaseCode(String(x?.sku || "").split("-")[0]))); Object.keys(br).forEach((k) => { if (ks.has(k)) sc[k] = br[k]; }); }
      return v35Sanitize(_preV35Merge(cl, sc));
    };

    const _preV35Render = renderCatalog;
    renderCatalog = function() { v35Enforce(); _preV35Render(); };

    const _preV35Lang = setLang;
    setLang = function(l) { _preV35Lang(l); v35Enforce(); if (document.getElementById("billModal")?.classList.contains("open")) renderBillModal(); };

    const v35StampMins = (s) => {
      const m = String(s || "").trim().match(/^(\d{1,2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})/);
      return m ? ((((Number(m[4]) * 32 + Number(m[3])) * 24 + Number(m[1])) * 60) + Number(m[2])) : 0;
    };

    refreshLiveSapoStock = async function(silent = true) {
      if (v35SyncBusy) return false;
      v35SyncBusy = true;
      const timeEl = document.getElementById("liveSyncTimeText"), badgeEl = document.getElementById("liveSyncBadge");
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
          try { const r = await fetch("./sapo_5_kho.json?t=" + Date.now(), { cache: "no-store" }); if (r.ok) data = await r.json(); } catch (_) {}
        }
        if (data && data.sync_source !== "cloudflare_edge_sapo_live" && (!silent || Date.now() - v35LastRelMs > 45000)) {
          v35LastRelMs = Date.now();
          try {
            const slug = typeof GITHUB_REPO_SLUG !== "undefined" ? GITHUB_REPO_SLUG : "moonchuyensi-cmd/havana-sale";
            const gr = await fetch(`https://api.github.com/repos/${slug}/releases/tags/live-stock?t=${Date.now()}`, { headers: { "Accept": "application/vnd.github+json" }, cache: "no-store" });
            if (gr.ok) {
              const rel = await gr.json();
              const feed = rel && rel.body ? JSON.parse(rel.body) : null;
              if (feed && feed.items && Array.isArray(feed.warehouses)) {
                const fEp = Number(feed.synced_epoch || 0), bEp = Number(data.synced_epoch || 0);
                if ((fEp > 0 && bEp > 0) ? (fEp >= bEp) : (v35StampMins(feed.synced_at) >= v35StampMins(data.synced_at))) {
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
                  if (SALE_RULES_MASTER) {
                    Object.keys(feed.items).forEach((c) => {
                      if (seen.has(c) || !SALE_RULES_MASTER[c]) return;
                      const fe = feed.items[c];
                      if (!fe || !Array.isArray(fe.v) || !fe.v.length) return;
                      const vars = fe.v.map(([sz, qa]) => { const st = {}; whIds.forEach((w, i) => { st[w] = Number((qa && qa[i]) || 0); }); return { size: sz || "Freesize", stocks: st }; });
                      data.products.push({ sku: c, name: fe.n || ("Áo thun Havana " + c), subType: SALE_RULES_MASTER[c].subType || "", origPrice: SALE_RULES_MASTER[c].origPrice || 199000, salePrice: SALE_RULES_MASTER[c].salePrice || 79000, discountPct: SALE_RULES_MASTER[c].discountPct || 50, image: "", images: [], sapoImages: fe.si || [], video: "", sizeChart: "", variants: vars });
                    });
                  }
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
            if (sr.ok) { const rr = await sr.json(); if (rr && Object.keys(rr).length >= 10) { v35Prices(rr); SALE_RULES_MASTER = rr; } }
          } catch (_) {}
          const cleanLive = v35Sanitize(data.products);
          stripSapoImagesFromProducts(cleanLive);
          PRODUCTS = mergeAndApplySaleRules(cleanLive, SALE_RULES_MASTER);
          v35Enforce();
          const exp = Boolean(data.sapo_cookie_expired);
          if (timeEl) {
            timeEl.textContent = exp
              ? (currentLang === "VI" ? `⚠️ Cookie Sapo hết hạn (${data.synced_at || "Cũ"})` : `⚠️ Cookie Expired (${data.synced_at || "Stale"})`)
              : ((currentLang === "VI" ? "Cập nhật: " : "Synced: ") + (data.synced_at || "Live"));
          }
          if (badgeEl) { badgeEl.style.background = exp ? "var(--warn-bg)" : ""; badgeEl.style.color = exp ? "var(--warn-amber)" : ""; }
          if (dotEl) dotEl.style.background = exp ? "var(--warn-amber)" : "";
          lastSyncedStamp = data.synced_at || "";
          if (typeof discoverRepoMediaLive === "function") discoverRepoMediaLive(false);
          renderCatalog();
          updateCartBar();
          if (document.getElementById("billModal")?.classList.contains("open")) { if (!cart.length) closeBillModal(); else renderBillModal(); }
          return true;
        }
      } catch (_) {} finally { v35SyncBusy = false; }
      return false;
    };

    v35Enforce();
    renderCatalog();
    updateCartBar();
    setTimeout(v35ApplyBranding, 10);
    setInterval(v35TickCountdown, 1000);
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
    m = SKU_REGEX.match(str(val or "").strip().upper().rstrip("-"))
    return m.group(1).upper() if m else ""


def parse_money(val):
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
            with zf_mod.ZipFile(zp, "r") as zf:
                zf.extractall(ext_dir)
            os.remove(zp)
            print(f"[+] Da giai nen {zp} -> {ext_dir}")
        except Exception as e:
            print(f"[!] Loi giai nen {zp}: {e}")
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


def load_all_rules_and_excel_products():
    sale_rules = {}
    if os.path.exists("sale_rules.json"):
        try:
            with open("sale_rules.json", "r", encoding="utf-8") as f:
                sale_rules = json.load(f)
        except Exception:
            pass

    xlsx_files = [f for f in glob.glob("*.xlsx") if not os.path.basename(f).startswith("~$")]
    xlsx_files.sort(key=lambda x: (0 if "black" in x.lower() else 1, x.lower()))

    excel_products = {}
    unpriced_extra = set()

    if xlsx_files:
        try:
            import openpyxl
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
            import openpyxl

        for xf in xlsx_files:
            is_bf_master = ("black" in os.path.basename(xf).lower() and "ath" not in os.path.basename(xf).lower())
            try:
                wb = openpyxl.load_workbook(xf, data_only=True)
            except Exception as e:
                print(f"[!] Khong doc duoc {xf}: {e}")
                continue

            for sname in wb.sheetnames:
                ws = wb[sname]
                if ws.sheet_state != "visible" or "RÚT" in sname.upper() or "RUT" in strip_accents(sname):
                    continue
                if is_bf_master and sname not in ("Tổng CH", "Phụ kiện"):
                    continue

                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue

                # Tu dong tim dong tieu de trong 10 dong dau
                h_idx = 0
                for ri in range(min(10, len(rows))):
                    cand_h = [strip_accents(c) for c in (rows[ri] or []) if c is not None]
                    joined = " | ".join(cand_h)
                    if len(cand_h) >= 2 and any(k in joined for k in ("MA", "SKU", "GIA", "DONG GIA", "SALE", "TON", "SO LUONG")):
                        h_idx = ri
                        break

                header = [strip_accents(c) for c in (rows[h_idx] or [])]
                c_sku = c_name = c_opt = c_orig = c_sale = c_pct = c_type = None
                stock_cols = {}

                for idx, h in enumerate(header):
                    if not h: continue
                    if c_sku is None and (h in ("MA", "MA MOI", "SKU", "MA SKU", "MA HANG", "MA SP", "MA PHIEN BAN") or "MA SKU" in h):
                        c_sku = idx
                    elif c_name is None and ("TEN" in h):
                        c_name = idx
                    elif c_opt is None and any(k in h for k in ("MAU", "SIZE", "PHAN LOAI", "THUOC TINH")):
                        c_opt = idx
                    elif c_sale is None and (any(k in h for k in ("DONG GIA", "GIA SALE", "GIA GIAM", "GIA KM", "KHUYEN MAI", "GIA XA", "DE XUAT", "39 - 79")) or h == "SALE"):
                        c_sale = idx
                    elif c_orig is None and any(k in h for k in ("GIA BAN", "GIA GOC", "GIA LE", "NIEM YET")):
                        c_orig = idx
                    elif c_pct is None and ("%" in h or "GIAM GIA" in h):
                        c_pct = idx
                    elif c_type is None and h == "LOAI":
                        c_type = idx
                    elif "DAU KY" not in h and "BAN RA" not in h and "TONG TON CU" not in h:
                        if "PCT" in h or "PHAN CHAU" in h or "PHAN CHU" in h: stock_cols[idx] = "wh_pct"
                        elif "AU CO" in h or h in ("AC", "TON AC", "SL TON AC"): stock_cols[idx] = "wh_auco"
                        elif "LE DUAN" in h or h in ("LD", "TON LD", "SL TON LD"): stock_cols[idx] = "wh_leduan"
                        elif "HO PHI TICH" in h: stock_cols[idx] = "wh_havana"
                        elif any(k in h for k in ("TON", "SO LUONG", "SL", "CO THE BAN", "ONLINE", "ONL", "CUOI KY")):
                            if "wh_online" not in stock_cols.values():
                                stock_cols[idx] = "wh_online"

                last_code, last_orig, last_sale, last_title = "", 0, 0, ""

                for r in rows[h_idx + 1:]:
                    if not r or all(x is None or str(x).strip() == "" for x in r):
                        continue
                    raw_sku = str(r[c_sku] if (c_sku is not None and c_sku < len(r) and r[c_sku] is not None) else "").strip()
                    raw_name = norm_nfc(r[c_name]) if (c_name is not None and c_name < len(r) and r[c_name]) else ""

                    # Tim ma SKU goc (uu tien ma trong ten neu cot SKU la barcode PVN, hoac quet 3 cot dau)
                    name_toks = [t for t in reversed(re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", raw_name.upper())) if not t.startswith("PVN")]
                    base = clean_base_code(raw_sku.split("-")[0])
                    if base.startswith("PVN"):
                        base = ""
                    if not base and name_toks:
                        base = name_toks[0]
                    if not base:
                        for cell_v in r[:3]:
                            cand_c = clean_base_code(str(cell_v or "").split("-")[0])
                            if cand_c and not cand_c.startswith("PVN"):
                                base = cand_c
                                if not raw_sku: raw_sku = str(cell_v or "").strip()
                                break
                    if not base or base in EXCLUDED_SKUS:
                        continue

                    orig_p = parse_money(r[c_orig]) if (c_orig is not None and c_orig < len(r)) else 0
                    sale_p = parse_money(r[c_sale]) if (c_sale is not None and c_sale < len(r)) else 0
                    if sale_p == -1:
                        continue
                    pct_v = parse_pct(r[c_pct]) if (c_pct is not None and c_pct < len(r)) else 0.0

                    # Ke thua gia va ten cua dong cha neu Excel gop o (merged cells)
                    if base == last_code:
                        if orig_p <= 0: orig_p = last_orig
                        if sale_p <= 0: sale_p = last_sale
                        if not raw_name: raw_name = last_title

                    if orig_p > 0 and sale_p > 0 and orig_p < sale_p:
                        orig_p, sale_p = sale_p, orig_p
                    if sale_p <= 0 and orig_p > 0 and pct_v > 0:
                        sale_p = int(round(orig_p * (100.0 - pct_v) / 1000.0) * 1000)

                    sub_t = norm_nfc(r[c_type]) if (c_type is not None and c_type < len(r) and r[c_type]) else ""
                    prev_rule = sale_rules.get(base, {})

                    if sale_p > 0:
                        fin_orig = orig_p if orig_p > sale_p else (prev_rule.get("origPrice") or orig_p or sale_p * 2)
                        fin_dp = max(1, round((1.0 - sale_p / fin_orig) * 100)) if fin_orig > sale_p else 30
                        sale_rules[base] = {
                            "origPrice": fin_orig,
                            "salePrice": sale_p,
                            "discountPct": fin_dp,
                            "subType": sub_t or prev_rule.get("subType", "") or PK_KNOWN_SUBTYPES.get(base, ""),
                        }
                    elif base in sale_rules:
                        sale_p = sale_rules[base]["salePrice"]
                        if orig_p > sale_p and not sale_rules[base].get("origPrice"):
                            sale_rules[base]["origPrice"] = orig_p
                    elif not is_bf_master:
                        unpriced_extra.add(base)

                    last_code, last_orig, last_sale = base, orig_p, sale_p

                    # Trich xuat bien the & ton kho tu cac file bo sung (VD: ATH ONL.xlsx)
                    if not is_bf_master and stock_cols and base in sale_rules:
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
                        default_name = f"Áo thun nữ Havana {base}" if base.startswith("ATH") else f"Sản phẩm Havana {base}"
                        ent = excel_products.setdefault(base, {
                            "sku": base,
                            "name": clean_title or default_name,
                            "subType": sale_rules[base].get("subType", ""),
                            "category": infer_category(base, clean_title or default_name, sale_rules[base].get("subType", "")),
                            "origPrice": sale_rules[base]["origPrice"],
                            "salePrice": sale_rules[base]["salePrice"],
                            "discountPct": sale_rules[base]["discountPct"],
                            "image": "", "images": [], "sapoImages": [], "video": "", "sizeChart": "",
                            "variants": [],
                        })
                        if clean_title and ent["name"].startswith(("Áo thun nữ Havana", "Sản phẩm Havana")):
                            ent["name"] = clean_title
                        ent["origPrice"] = sale_rules[base]["origPrice"]
                        ent["salePrice"] = sale_rules[base]["salePrice"]
                        ent["discountPct"] = sale_rules[base]["discountPct"]
                        ent["variants"].append({"size": sz_lbl, "stocks": st_row})

            print(f"[+] Da quet xong file Excel: {xf} (Tong ma Sale hien tai: {len(sale_rules)})")

    for ex in EXCLUDED_SKUS:
        sale_rules.pop(ex, None)
        excel_products.pop(ex, None)
    for code, (op, sp, dp) in PRICE_PATCH_V35.items():
        if code in sale_rules:
            sale_rules[code].update({"origPrice": op, "salePrice": sp, "discountPct": dp})

    if sale_rules:
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)

    if unpriced_extra:
        msg = f"### ⚠️ Phát hiện {len(unpriced_extra)} mã mới trong Excel chưa có cột Giá Sale: `{', '.join(sorted(unpriced_extra))}`\nHãy thêm cột `Giá sale` (hoặc `Đồng giá`) vào `ATH ONL.xlsx` để các mã này lên kệ tự động."
        print(msg)
        sp_summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if sp_summary:
            with open(sp_summary, "a", encoding="utf-8") as sf: sf.write(msg + "\n")

    return sale_rules, excel_products


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
        fn, pd = os.path.basename(rel), os.path.dirname(rel)
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


def upgrade_repo_files_v35():
    if not os.path.exists("index.html"): return False
    with open("index.html", "r", encoding="utf-8") as f: orig = f.read()
    html = orig
    html = re.sub(r'(<title>)[^<]*(</title>)', r'\1HAVANA — Xả Ngày Cuối (Chỉ Áp Dụng Cho Online)\2', html, count=1)
    html = re.sub(r'(<div class="brand-title" id="mainBrandTitle">)[^<]*(</div>)', r'\1XẢ NGÀY CUỐI\2', html, count=1)
    html = re.sub(r'(<span id="subBrandText">)[^<]*(</span>)', r'\1(Chỉ áp dụng cho Online)\2', html, count=1)
    html = re.sub(r'(<span class="bill-head-badge">)[^<]*(</span>)', r'\1XẢ NGÀY CUỐI • CHỈ ÁP DỤNG CHO ONLINE\2', html, count=1)
    html = re.sub(r'(<h2 id="billModalTitle"[^>]*>)[^<]*(</h2>)', r'\1Phiếu Lên Đơn Tạm\2', html, count=1)
    html = re.sub(r'(<input type="text" class="bill-input" id="custNoteInput" placeholder=")[^"]*(")', r'\1Địa chỉ nhận ship &amp; Ghi chú đơn hàng (Chỉ áp dụng cho Online)...\2', html, count=1)
    html = html.replace('📋 Sao chép nội dung Bill', '📋 Sao chép Đơn tạm').replace('🖼️ Tải ảnh Bill gửi Zalo', '🖼️ Tải ảnh Đơn tạm gửi Zalo')
    end_m = html.rfind("/* [BLOCK:APP_LOGIC_END] */")
    if end_m != -1:
        cuts = [html.find(k, 0, end_m) for k in ("/* === v22:", "/* === v23:", "/* === v25:", "/* === v26:", "/* === v27:", "/* === v28:", "/* === v29:", "/* === v30:", "/* === v31:", "/* === v32:", "/* === v33:", "/* === v34:", "/* === v35:")]
        valid = [p for p in cuts if p != -1]
        cut_at = min(valid) if valid else end_m
        html = html[:cut_at].rstrip() + "\n\n    " + V35_INDEX_JS_ADDON.strip() + "\n    /* [BLOCK:APP_LOGIC_END] */" + html[end_m + len("/* [BLOCK:APP_LOGIC_END] */"):]
    html = re.sub(r'(<span id="onlyInStockText">)(?:Còn tồn|In Stock)(</span>)', r'\1Tồn ≥ 3\2', html, count=1)
    if html != orig:
        with open("index.html", "w", encoding="utf-8") as f: f.write(html)
        return True
    return False


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
    body = json.dumps({"v": 35, "full": 1, "cookie_expired": bool(expired), "synced_at": synced_at, "synced_epoch": int(epoch or 0), "warehouses": wh_list, "items": items}, ensure_ascii=False, separators=(",", ":"))
    if len(body) > 120000:
        tmp = json.loads(body)
        for v in tmp["items"].values(): v.pop("n", None)
        body = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    hdrs = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "havana-v35"}
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
    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump({"min_stock_rule": MIN_TOTAL_STOCK, "min_variant_stock_rule": MIN_VARIANT_STOCK, "synced_at": synced_at, "synced_epoch": int(epoch or 0), "sapo_cookie_expired": bool(expired), "warehouses": wh_list, "products": filtered}, f, ensure_ascii=False, indent=2)
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f: h = f.read()
        s_i, e_i = h.find("/* [BLOCK:DATA_PAYLOAD_START] */"), h.find("/* [BLOCK:DATA_PAYLOAD_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_PAYLOAD_END] */")
            blk = f"/* [BLOCK:DATA_PAYLOAD_START] */\n    let WAREHOUSE_CONFIG = {json.dumps(wh_list, ensure_ascii=False)};\n    let SALE_RULES_MASTER = {json.dumps(rules or {}, ensure_ascii=False)};\n    let BASELINE_PRODUCTS = {json.dumps(filtered, ensure_ascii=False)};\n    let PRODUCTS = BASELINE_PRODUCTS.map((p) => Object.assign({{}}, p));\n    /* [BLOCK:DATA_PAYLOAD_END] */"
            with open("index.html", "w", encoding="utf-8") as f: f.write(h[:s_i] + blk + h[e_i:])
    return len(filtered)


def main():
    upgraded = upgrade_repo_files_v35()
    sale_rules, excel_prods = load_all_rules_and_excel_products()
    repo_media = scan_repo_media(list(sale_rules.keys()))

    old_wh, old_prods, old_stamp, old_epoch = [], [], "18:04 26/11", 0
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
            ep["origPrice"] = sale_rules[c]["origPrice"] or ep.get("origPrice", 299000)
            ep["salePrice"] = sale_rules[c]["salePrice"] or ep.get("salePrice", 159000)
            ep["discountPct"] = max(1, round((1 - ep["salePrice"] / ep["origPrice"]) * 100))
            merged_map[c] = ep
        # Gop va cap nhat ngay cac ma tu ATH ONL.xlsx ke ca khi Cookie Sapo dang het han
        for c, xp in excel_prods.items():
            if c in merged_map:
                merged_map[c]["origPrice"] = xp["origPrice"]
                merged_map[c]["salePrice"] = xp["salePrice"]
                merged_map[c]["discountPct"] = xp["discountPct"]
                if xp.get("variants"): merged_map[c]["variants"] = xp["variants"]
                if xp.get("name") and not xp["name"].startswith(("Áo thun nữ Havana", "Sản phẩm Havana")):
                    merged_map[c]["name"] = xp["name"]
            else:
                merged_map[c] = xp
        final_list = list(merged_map.values())
        apply_media_to_products(final_list, repo_media, preserved)
        wh_out = old_wh or [{"id": "wh_havana", "name": "HAVANA (93-95 Hồ Phi Tích)"}, {"id": "wh_online", "name": "HAVANA ONLINE"}, {"id": "wh_pct", "name": "261 - 263 Phan Châu Trinh"}, {"id": "wh_auco", "name": "101 Âu Cơ"}, {"id": "wh_leduan", "name": "240 Lê Duẩn"}]
        publish_live_release(wh_out, final_list, old_stamp, old_epoch, expired=True)
        if upgraded or excel_prods or repo_media or os.environ.get("GITHUB_EVENT_NAME") != "schedule":
            if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
            kept = write_outputs(wh_out, final_list, old_stamp, sale_rules, old_epoch, expired=True)
            print(f"[+] Da gop ATH ONL.xlsx & anh moi -> hien {kept} ma tren Web!")
        else:
            with open(".skip_git_commit", "w", encoding="utf-8") as sf: sf.write(old_stamp)
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
            grouped[base] = {"sku": base, "name": pname, "subType": rule.get("subType", ""), "category": infer_category(base, pname, rule.get("subType", "")), "origPrice": op, "salePrice": sp, "discountPct": dp, "image": "", "images": [], "sapoImages": s_imgs, "video": "", "sizeChart": "", "variants": v_list}

    for c, xp in excel_prods.items():
        if c not in grouped:
            grouped[c] = xp

    prods_all = list(grouped.values())
    apply_media_to_products(prods_all, repo_media, preserved)
    wh_sorted = [{k: v for k, v in it.items() if k != "order"} for it in sorted(loc_meta.values(), key=lambda x: x["order"])]
    now_st, now_ep = vn_now_str(), int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    publish_live_release(wh_sorted, prods_all, now_st, now_ep, expired=False)
    if os.path.exists(".skip_git_commit"): os.remove(".skip_git_commit")
    kept = write_outputs(wh_sorted, prods_all, now_st, sale_rules, now_ep, expired=False)
    print(f"[+] Hoan tat Auto-Sync v35 LIVE ({now_st}) -> {kept} ma!")


if __name__ == "__main__":
    main()
# === HET FILE V35 ===
