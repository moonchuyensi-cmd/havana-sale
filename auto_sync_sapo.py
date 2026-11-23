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
# AUTO_SYNC_SAPO.PY (v25.1 - FIX SYNTAX + TU DONG LOC TONG TON >= 3 MOI LAN QUET)
# Khong them ma QR - Tu dong dong bo index.html, functions/api/stock.js & sapo_5_kho.json
# ==============================================================================

MIN_TOTAL_STOCK = 3
DEFAULT_COOKIE = ""
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
PRICE_PATCH_V25 = {
    "AO1010": (194000, 79000, 59, 0.5928),
    "AO726":  (225000, 39000, 83, 0.8267),
    "AO1016": (214000, 79000, 63, 0.6308),
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

FORCE_RESET_TO_SAPO_SKUS = {"SAT532"}
PURGE_STATE_FILE = ".purged_wrong_media_v21.json"

CF_STOCK_JS_V25 = r'''// Cloudflare Pages Function: /api/stock (v25 Strict MIN_TOTAL_STOCK >= 3 + 45s Edge Sync)
const MIN_TOTAL_STOCK = 3;
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
const GITHUB_REPO_SLUG = "moonchuyensi-cmd/havana-sale";
const SAPO_CDN_RX = /(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)/i;
const PRICE_PATCH_V25 = {
  AO1010: { origPrice: 194000, salePrice: 79000, discountPct: 59 },
  AO726:  { origPrice: 225000, salePrice: 39000, discountPct: 83 },
  AO1016: { origPrice: 214000, salePrice: 79000, discountPct: 63 }
};

function isExternalSapoUrl(u) {
  const s = String(u || "").trim();
  if (!s || s.startsWith("./") || s.startsWith("images/") || s.startsWith("videos/") || s.startsWith("sizes/") || s.startsWith("data:") || s.startsWith("blob:")) return false;
  if (s.includes("raw.githubusercontent.com/" + GITHUB_REPO_SLUG)) return false;
  return SAPO_CDN_RX.test(s) || /^https?:\/\//i.test(s);
}

function calcTotalStock(p) {
  if (!p || !Array.isArray(p.variants)) return 0;
  let total = 0;
  for (const v of p.variants) {
    const st = (v && v.stocks) || {};
    for (const q of Object.values(st)) total += Math.max(0, Number(q) || 0);
  }
  return total;
}

function extractSmartSecret(rawVal) {
  let raw = String(rawVal || "").trim();
  if (!raw) return "";
  const mb = raw.match(/(?:-b|--cookie)\s+(['"])([\s\S]+?)\1/);
  if (mb) raw = mb[2];
  else {
    const mh = raw.match(/(?:-H|--header)\s+(['"])cookie\s*:\s*([\s\S]+?)\1/i);
    if (mh) raw = mh[2];
    else {
      const ml = raw.match(/(?:^|[\r\n])cookie\s*:\s*([^\r\n]+)/i);
      if (ml) raw = ml[1];
    }
  }
  if (raw.includes("^") && (raw.includes("sapo-admin") || raw.includes("AspNetCore"))) {
    raw = raw.replace(/\^(["%&|<>^])/g, "$1");
  }
  let s = raw.trim().replace(/^["']+|["']+$/g, "").replace(/[\r\n]+/g, " ").trim();
  for (const pfx of ["cookie:", "x-sapo-access-token:"]) {
    if (s.toLowerCase().startsWith(pfx)) s = s.slice(pfx.length).trim();
  }
  return s.replace(/[^\x20-\x7E]/g, "");
}

function buildAuthCandidates(env, domain) {
  const candidates = [];
  const cookie = extractSmartSecret(env.SAPO_COOKIE);
  const token = extractSmartSecret(env.SAPO_ACCESS_TOKEN);
  const baseHdr = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "vi",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
  };
  const makeCookieHdr = (c) => ({
    ...baseHdr,
    "cookie": c,
    "referer": `https://${domain}/admin/products`,
    "x-sapo-client": "sapo-frontend-v3",
    "x-sapo-serviceid": "sapo-frontend-v3"
  });
  if (cookie) candidates.push(makeCookieHdr(cookie));
  if (token) {
    if (token.includes("sapo-admin=") || token.includes(".AspNetCore") || token.includes(";")) {
      candidates.push(makeCookieHdr(token));
    } else {
      candidates.push({ ...baseHdr, "X-Sapo-Access-Token": token });
      candidates.push(makeCookieHdr(token));
    }
  }
  return candidates;
}

function stripAccents(str) {
  return String(str || "").replace(/đ/g, "d").replace(/Đ/g, "D").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();
}

function mapWarehouseId(rawName, locId) {
  const norm = stripAccents(rawName);
  if (norm === "HAVANA" || norm.includes("HO PHI TICH") || norm.includes("93-95") || norm.includes("93 - 95")) return "wh_havana";
  if (norm.includes("ONLINE")) return "wh_online";
  if (norm.includes("261") || norm.includes("PHAN CHU TRINH") || norm.includes("PHAN CHAU TRINH")) return "wh_pct";
  if (norm.includes("101") || norm.includes("AU CO")) return "wh_auco";
  if (norm.includes("240") || norm.includes("LE DUAN")) return "wh_leduan";
  return "wh_" + locId;
}

function normalizeSapoImg(rawUrl, domain) {
  let u = String(rawUrl || "").trim();
  if (!u || u.startsWith("data:")) return "";
  if (u.startsWith("//")) u = "https:" + u;
  else if (u.startsWith("/")) u = `https://${domain}${u}`;
  return /^https?:\/\//i.test(u) ? u : "";
}

function extractSapoImagesFromProd(prod, domain) {
  const urls = [];
  const addU = (val) => {
    const nu = normalizeSapoImg(val, domain);
    if (nu && !urls.includes(nu)) urls.push(nu);
  };
  if (prod.image) addU(typeof prod.image === "object" ? (prod.image.full_path || prod.image.src || prod.image.path) : prod.image);
  for (const im of (prod.images || [])) addU(typeof im === "object" ? (im.full_path || im.src || im.path) : im);
  for (const v of (prod.variants || [])) {
    for (const vim of (v.images || [])) addU(typeof vim === "object" ? (vim.full_path || vim.src || vim.path) : vim);
    if (v.image) addU(typeof v.image === "object" ? (v.image.full_path || v.image.src || v.image.path) : v.image);
  }
  return urls;
}

function parseStampToMinutes(stampStr) {
  const m = String(stampStr || "").trim().match(/^(\d{1,2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})/);
  if (!m) return 0;
  const [, hh, mm, dd, mo] = m.map(Number);
  return (((mo * 32 + dd) * 24 + hh) * 60) + mm;
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const reqUrl = new URL(request.url);
  const forceBypass = reqUrl.searchParams.get("force") === "1";
  const domain = (env.SAPO_DOMAIN || "havanago.mysapogo.com").trim();
  const authCandidates = buildAuthCandidates(env, domain);

  const cache = caches.default;
  const cacheKey = new Request(new URL("/api/stock-edge-v25", request.url).toString());
  if (!forceBypass) {
    const cached = await cache.match(cacheKey);
    if (cached) return cached;
  }

  const baseAsset = await env.ASSETS.fetch(new URL("/sapo_5_kho.json", request.url));
  if (!baseAsset.ok) {
    return new Response(JSON.stringify({ error: "Missing sapo_5_kho.json" }), {
      status: 404,
      headers: { "content-type": "application/json; charset=utf-8" }
    });
  }

  const payload = await baseAsset.json();
  let saleRules = null;
  try {
    const rulesAsset = await env.ASSETS.fetch(new URL("/sale_rules.json", request.url));
    if (rulesAsset.ok) {
      saleRules = await rulesAsset.json();
      for (const ex of EXCLUDED_SKUS) {
        if (saleRules) delete saleRules[ex];
      }
      if (saleRules) {
        for (const [k, patch] of Object.entries(PRICE_PATCH_V25)) {
          if (saleRules[k]) Object.assign(saleRules[k], patch);
        }
      }
    }
  } catch (_) {}

  let syncedLive = false;
  let syncSource = "static_json";
  const sapoImgsByBase = {};

  for (const headers of authCandidates) {
    try {
      const p1Res = await fetch(`https://${domain}/admin/products.json?page=1&limit=250`, { headers });
      if (!p1Res.ok) continue;
      const p1Text = await p1Res.text();
      if (p1Text.trimStart().startsWith("<")) continue;
      const p1Data = JSON.parse(p1Text);
      const firstBatch = p1Data.products || [];
      if (!firstBatch.length) continue;

      const allProds = [...firstBatch];
      const locMap = {};
      const locPromise = fetch(`https://${domain}/admin/locations.json`, { headers })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

      if (firstBatch.length === 250) {
        const total = Number((p1Data.metadata && p1Data.metadata.total) || 0);
        const maxPage = total > 250 ? Math.min(25, Math.ceil(total / 250)) : 12;
        for (let startP = 2; startP <= maxPage; startP += 5) {
          const batchPages = [];
          for (let pg = startP; pg < startP + 5 && pg <= maxPage; pg++) {
            batchPages.push(
              fetch(`https://${domain}/admin/products.json?page=${pg}&limit=250`, { headers })
                .then((r) => (r.ok ? r.json() : { products: [] }))
                .catch(() => ({ products: [] }))
            );
          }
          const results = await Promise.all(batchPages);
          let gotEmpty = false;
          for (const rData of results) {
            const b = rData.products || [];
            if (!b.length) gotEmpty = true;
            else allProds.push(...b);
          }
          if (gotEmpty) break;
        }
      }

      const locData = await locPromise;
      if (locData && Array.isArray(locData.locations)) {
        for (const l of locData.locations) {
          locMap[String(l.id)] = mapWarehouseId(l.label || l.name || l.location_name, l.id);
        }
      }

      const liveByBase = {};
      const nameByBase = {};
      for (const prod of allProds) {
        const variants = prod.variants || [];
        if (!variants.length) continue;
        let baseCode = "";
        const nameTokens = String(prod.name || "").toUpperCase().match(/\b([A-Z]{1,5}\d{1,5})\b/g) || [];
        for (let ti = nameTokens.length - 1; ti >= 0; ti--) {
          const tok = nameTokens[ti];
          if (saleRules && saleRules[tok]) { baseCode = tok; break; }
        }
        if (!baseCode) {
          for (const v of variants) {
            const rawSku = String(v.sku || "").trim().toUpperCase().split("-")[0];
            const m = rawSku.match(/^([A-Z]{1,5}\d{1,5})/);
            if (m) {
              if (saleRules && saleRules[m[1]]) { baseCode = m[1]; break; }
              if (!m[1].startsWith("PVN") && !baseCode) baseCode = m[1];
            }
          }
        }
        if (!baseCode || EXCLUDED_SKUS.has(baseCode)) continue;
        if (!nameByBase[baseCode] && prod.name) nameByBase[baseCode] = String(prod.name).trim();
        liveByBase[baseCode] = liveByBase[baseCode] || [];

        const pImgs = extractSapoImagesFromProd(prod, domain);
        if (pImgs.length) {
          sapoImgsByBase[baseCode] = sapoImgsByBase[baseCode] || [];
          for (const u of pImgs) {
            if (!sapoImgsByBase[baseCode].includes(u)) sapoImgsByBase[baseCode].push(u);
          }
        }

        for (const v of variants) {
          const opts = [v.opt1, v.opt2, v.opt3].filter(Boolean).map((s) => String(s).trim());
          let sz = opts.length ? opts.join(" - ") : String(v.title || "Freesize").trim();
          if (sz.toLowerCase() === "default title") sz = "Freesize";
          const stocks = {};
          for (const inv of (v.inventories || [])) {
            const wid = locMap[String(inv.location_id)] || mapWarehouseId(inv.location_label || inv.location_name, inv.location_id);
            const avail = inv.available !== undefined && inv.available !== null ? inv.available : (inv.on_hand || 0);
            stocks[wid] = (stocks[wid] || 0) + Math.max(0, Number(avail) || 0);
          }
          liveByBase[baseCode].push({ size: sz, stocks });
        }
      }

      if (Object.keys(liveByBase).length > 0 && Array.isArray(payload.products)) {
        const seen = new Set();
        for (const item of payload.products) {
          const code = String(item.sku || "").trim().toUpperCase().split("-")[0];
          seen.add(code);
          item.variants = liveByBase[code] || [];
          if (nameByBase[code]) item.name = nameByBase[code];
        }
        if (saleRules) {
          for (const [code, rule] of Object.entries(saleRules)) {
            if (!seen.has(code) && !EXCLUDED_SKUS.has(code) && liveByBase[code]) {
              payload.products.push({
                sku: code,
                name: nameByBase[code] || ("Sản phẩm Havana " + code),
                subType: rule.subType || "",
                origPrice: rule.origPrice || 299000,
                salePrice: rule.salePrice || 159000,
                discountPct: rule.discountPct || 30,
                image: "",
                images: [],
                sapoImages: sapoImgsByBase[code] || [],
                video: "",
                sizeChart: "",
                variants: liveByBase[code]
              });
            }
          }
        }
        const nowVN = new Date(Date.now() + 7 * 3600 * 1000);
        const pad = (n) => String(n).padStart(2, "0");
        payload.synced_at = `${pad(nowVN.getUTCHours())}:${pad(nowVN.getUTCMinutes())} ${pad(nowVN.getUTCDate())}/${pad(nowVN.getUTCMonth() + 1)}`;
        payload.synced_epoch = Math.floor(Date.now() / 1000);
        syncedLive = true;
        syncSource = "cloudflare_edge_sapo_live";
        break;
      }
    } catch (_) {}
  }

  if (!syncedLive && Array.isArray(payload.products)) {
    try {
      const ghRes = await fetch(`https://api.github.com/repos/${GITHUB_REPO_SLUG}/releases/tags/live-stock`, {
        headers: { "Accept": "application/vnd.github+json", "User-Agent": "havana-sale-cf-edge-v25" }
      });
      if (ghRes.ok) {
        const relJson = await ghRes.json();
        if (relJson && relJson.body) {
          const feed = JSON.parse(relJson.body);
          if (feed && feed.items && Array.isArray(feed.warehouses)) {
            const feedMins = parseStampToMinutes(feed.synced_at);
            const baseMins = parseStampToMinutes(payload.synced_at);
            if (feedMins >= baseMins) {
              const whIds = feed.warehouses.map((w) => w.id);
              payload.warehouses = feed.warehouses;
              const seenInBase = new Set();
              for (const item of payload.products) {
                const code = String(item.sku || "").trim().toUpperCase().split("-")[0];
                seenInBase.add(code);
                const fEntry = feed.items[code];
                if (fEntry && Array.isArray(fEntry.v)) {
                  item.variants = fEntry.v.map(([sz, qArr]) => {
                    const st = {};
                    whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
                    return { size: sz || "Freesize", stocks: st };
                  });
                  if (fEntry.n) item.name = fEntry.n;
                } else if (feed.full) {
                  item.variants = [];
                }
              }
              if (saleRules) {
                for (const [code, fEntry] of Object.entries(feed.items)) {
                  if (!seenInBase.has(code) && saleRules[code] && !EXCLUDED_SKUS.has(code) && Array.isArray(fEntry.v)) {
                    const rule = saleRules[code];
                    const builtVars = fEntry.v.map(([sz, qArr]) => {
                      const st = {};
                      whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
                      return { size: sz || "Freesize", stocks: st };
                    });
                    payload.products.push({
                      sku: code,
                      name: fEntry.n || ("Sản phẩm Havana " + code),
                      subType: rule.subType || "",
                      origPrice: rule.origPrice || 299000,
                      salePrice: rule.salePrice || 159000,
                      discountPct: rule.discountPct || 30,
                      image: "",
                      images: [],
                      sapoImages: fEntry.si || [],
                      video: "",
                      sizeChart: "",
                      variants: builtVars
                    });
                  }
                }
              }
              payload.synced_at = feed.synced_at;
              payload.synced_epoch = feed.synced_epoch || Math.floor(Date.now() / 1000);
              syncedLive = true;
              syncSource = "github_release_live_feed";
            }
          }
        }
      }
    } catch (_) {}
  }

  if (Array.isArray(payload.products)) {
    payload.products = payload.products.filter((p) => {
      const baseSku = String(p.sku || "").trim().toUpperCase().split("-")[0];
      if (EXCLUDED_SKUS.has(baseSku)) return false;
      if (saleRules && Object.keys(saleRules).length > 0) {
        const rule = saleRules[baseSku];
        if (!rule) return false;
        if (rule.origPrice > 0) p.origPrice = rule.origPrice;
        if (rule.salePrice > 0) p.salePrice = rule.salePrice;
        if (p.origPrice > p.salePrice && p.salePrice > 0) {
          p.discountPct = Math.max(1, Math.round((1 - p.salePrice / p.origPrice) * 100));
        }
        if (rule.subType && !p.subType) p.subType = rule.subType;
      }
      return calcTotalStock(p) >= MIN_TOTAL_STOCK;
    });

    const sat533SapoSet = new Set(sapoImgsByBase["SAT533"] || []);
    for (const p of payload.products) {
      if (String(p.sku || "").trim().toUpperCase().split("-")[0] === "SAT533") {
        for (const u of [p.image, ...(p.images || []), ...(p.sapoImages || [])]) {
          if (isExternalSapoUrl(u)) sat533SapoSet.add(String(u).trim());
        }
      }
    }

    for (const p of payload.products) {
      const code = String(p.sku || "").trim().toUpperCase().split("-")[0];
      const rawImgs = Array.isArray(p.images) ? p.images : [];
      let customImgs = [];
      let sapoImgs = [];
      const seedSapo = (code === "SAT532" && (sapoImgsByBase["SAT532"] || []).length > 0)
        ? [...sapoImgsByBase["SAT532"]]
        : [...(sapoImgsByBase[code] || []), ...(Array.isArray(p.sapoImages) ? p.sapoImages : [])];

      for (const u of [p.image, ...rawImgs, ...seedSapo]) {
        const s = String(u || "").trim();
        if (!s || s.startsWith("data:image/svg+xml")) continue;
        if (isExternalSapoUrl(s)) {
          if (code === "SAT532" && (sat533SapoSet.has(s) || s.toLowerCase().includes("sat533"))) continue;
          if (!sapoImgs.includes(s)) sapoImgs.push(s);
        } else {
          if (code === "SAT532") continue;
          if (!customImgs.includes(s)) customImgs.push(s);
        }
      }
      if (code === "SAT532") { p.video = ""; p.sizeChart = ""; }
      const finalImgs = customImgs.length > 0 ? customImgs : sapoImgs;
      p.sapoImages = sapoImgs;
      p.hasCustomMedia = customImgs.length > 0 || Boolean(p.video && !isExternalSapoUrl(String(p.video)));
      p.images = finalImgs;
      p.image = finalImgs[0] || "";
    }
  }

  payload.min_stock_rule = MIN_TOTAL_STOCK;
  payload.sync_source = syncSource;
  const outResp = new Response(JSON.stringify(payload), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": syncedLive ? "public, s-maxage=45, stale-while-revalidate=45" : "no-store, max-age=0"
    }
  });
  if (syncedLive && !forceBypass) context.waitUntil(cache.put(cacheKey, outResp.clone()));
  return outResp;
}
'''

V25_INDEX_JS_ADDON = r'''/* === v25: AUTO FILTER TOTAL STOCK >= 3 & PURGE LOW STOCK (< 3) ON EVERY SYNC === */
    const MIN_TOTAL_STOCK_V25 = 3;
    let v25SyncInFlight = false;
    let v25LastReleaseCheckMs = 0;
    if (typeof MEDIA_CACHE_VERSION !== "undefined" && MEDIA_CACHE_VERSION < 25) {
      MEDIA_CACHE_VERSION = 25;
    }

    const v25ApplyPriceFixes = (rulesMap) => {
      if (!rulesMap || typeof rulesMap !== "object") return;
      const fixed = {
        AO1010: { origPrice: 194000, salePrice: 79000, discountPct: 59 },
        AO726:  { origPrice: 225000, salePrice: 39000, discountPct: 83 },
        AO1016: { origPrice: 214000, salePrice: 79000, discountPct: 63 }
      };
      Object.keys(fixed).forEach((k) => {
        if (rulesMap[k]) Object.assign(rulesMap[k], fixed[k]);
      });
    };

    const v25CalcTotalStock = (p) => {
      if (!p || !Array.isArray(p.variants)) return 0;
      return p.variants.reduce((sum, v) => {
        const st = (v && v.stocks && typeof v.stocks === "object") ? v.stocks : {};
        return sum + Object.values(st).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0);
      }, 0);
    };

    const v25SyncStockBadgeLabel = () => {
      const stTxt = document.getElementById("onlyInStockText");
      if (stTxt) {
        stTxt.textContent = currentLang === "VI" ? "Tồn ≥ 3" : "Stock ≥ 3";
      }
    };

    const v25EnforceMinStock = (notifyCartChange = false) => {
      v25ApplyPriceFixes(SALE_RULES_MASTER);
      if (Array.isArray(BASELINE_PRODUCTS)) {
        BASELINE_PRODUCTS = BASELINE_PRODUCTS.filter((p) => v25CalcTotalStock(p) >= MIN_TOTAL_STOCK_V25);
      }
      if (Array.isArray(PRODUCTS)) {
        PRODUCTS.forEach((p) => {
          const code = normalizeBaseCode(String(p?.sku || "").split("-")[0]);
          const rule = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
          if (rule) {
            if (Number(rule.origPrice) > 0) p.origPrice = Number(rule.origPrice);
            if (Number(rule.salePrice) > 0) p.salePrice = Number(rule.salePrice);
            p.discountPct = getProductDiscountPct(p);
          }
        });
        PRODUCTS = PRODUCTS.filter((p) => v25CalcTotalStock(p) >= MIN_TOTAL_STOCK_V25);
      }
      if (Array.isArray(cart) && cart.length) {
        let adjusted = false;
        cart = cart.filter((c) => {
          const matchedP = (PRODUCTS || []).find((x) => x.sku === c.sku);
          if (!matchedP || v25CalcTotalStock(matchedP) < MIN_TOTAL_STOCK_V25) {
            adjusted = true;
            return false;
          }
          c.origPrice = matchedP.origPrice;
          c.finalPrice = getProductSalePrice(matchedP);
          c.discountPct = getProductDiscountPct(matchedP);
          const maxAvail = getMaxStockForSkuSize(c.sku, c.size);
          if (maxAvail <= 0) {
            adjusted = true;
            return false;
          }
          if (c.qty > maxAvail) {
            c.qty = maxAvail;
            adjusted = true;
          }
          return true;
        });
        if (adjusted) {
          persistCart();
          updateCartBar();
          if (notifyCartChange) {
            showToast("Tồn kho Sapo vừa thay đổi — các mã tổng tồn dưới 3 đã tự động rút khỏi danh sách & Phiếu Bill!");
          }
        }
      }
      v25SyncStockBadgeLabel();
    };

    const _preV25MergeRules = mergeAndApplySaleRules;
    mergeAndApplySaleRules = function(liveList, rulesObj) {
      v25ApplyPriceFixes(SALE_RULES_MASTER);
      v25ApplyPriceFixes(rulesObj);
      const baseRules = (rulesObj && Object.keys(rulesObj).length > 0) ? rulesObj : SALE_RULES_MASTER;
      let scopedRules = baseRules;
      if (Array.isArray(liveList) && liveList.length > 0 && baseRules) {
        const liveSet = new Set(
          liveList.map((it) => normalizeBaseCode(String(it?.sku || "").split("-")[0])).filter(Boolean)
        );
        scopedRules = {};
        Object.keys(baseRules).forEach((k) => {
          if (liveSet.has(k)) scopedRules[k] = baseRules[k];
        });
      }
      const merged = _preV25MergeRules(liveList, scopedRules);
      return merged.filter((p) => v25CalcTotalStock(p) >= MIN_TOTAL_STOCK_V25);
    };

    const _preV25RenderCatalog = renderCatalog;
    renderCatalog = function() {
      v25EnforceMinStock(false);
      _preV25RenderCatalog();
    };

    const _preV25SetLang = setLang;
    setLang = function(lang) {
      _preV25SetLang(lang);
      v25SyncStockBadgeLabel();
    };

    if (typeof exportCleanIndexHtml === "function") {
      const _preV25ExportCleanHtml = exportCleanIndexHtml;
      exportCleanIndexHtml = function() {
        v25EnforceMinStock(false);
        _preV25ExportCleanHtml();
      };
    }

    const v25ParseStampMins = (stampStr) => {
      const m = String(stampStr || "").trim().match(/^(\d{1,2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})/);
      if (!m) return 0;
      return (((Number(m[4]) * 32 + Number(m[3])) * 24 + Number(m[1])) * 60) + Number(m[2]);
    };

    const v25FetchLiveRelease = async (forceNow = false) => {
      const cacheKey = "havana_live_rel_v25";
      const nowMs = Date.now();
      if (!forceNow && (nowMs - v25LastReleaseCheckMs < 45000)) {
        try {
          const cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null");
          if (cached && (nowMs - cached.ts < 45000)) return cached.feed;
        } catch (_) {}
      }
      v25LastReleaseCheckMs = nowMs;
      try {
        const repoSlug = typeof GITHUB_REPO_SLUG !== "undefined" ? GITHUB_REPO_SLUG : "moonchuyensi-cmd/havana-sale";
        const res = await fetch(`https://api.github.com/repos/${repoSlug}/releases/tags/live-stock`, {
          headers: { "Accept": "application/vnd.github+json" },
          cache: "no-store"
        });
        if (!res.ok) return null;
        const rel = await res.json();
        if (!rel || !rel.body) return null;
        const feed = JSON.parse(rel.body);
        if (feed && feed.items && Array.isArray(feed.warehouses)) {
          try { sessionStorage.setItem(cacheKey, JSON.stringify({ ts: nowMs, feed })); } catch (_) {}
          return feed;
        }
      } catch (_) {}
      return null;
    };

    const v25ApplyReleaseFeed = (basePayload, feed) => {
      if (!feed || !feed.items || !Array.isArray(feed.warehouses) || !basePayload || !Array.isArray(basePayload.products)) {
        return basePayload;
      }
      const feedMins = v25ParseStampMins(feed.synced_at);
      const baseMins = v25ParseStampMins(basePayload.synced_at);
      const feedEpoch = Number(feed.synced_epoch || 0);
      const baseEpoch = Number(basePayload.synced_epoch || 0);
      if ((feedEpoch && baseEpoch && feedEpoch < baseEpoch) || (!feedEpoch && feedMins < baseMins)) {
        return basePayload;
      }

      const whIds = feed.warehouses.map((w) => w.id);
      basePayload.warehouses = feed.warehouses;
      const seen = new Set();
      basePayload.products.forEach((item) => {
        const code = normalizeBaseCode(String(item.sku || "").split("-")[0]);
        seen.add(code);
        const fEntry = feed.items[code];
        if (fEntry && Array.isArray(fEntry.v)) {
          item.variants = fEntry.v.map(([sz, qArr]) => {
            const st = {};
            whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
            return { size: sz || "Freesize", stocks: st };
          });
          if (fEntry.n) item.name = fEntry.n;
        } else if (feed.full) {
          item.variants = [];
        }
      });

      if (SALE_RULES_MASTER) {
        Object.keys(feed.items).forEach((code) => {
          if (seen.has(code) || !SALE_RULES_MASTER[code]) return;
          const fEntry = feed.items[code];
          if (!fEntry || !Array.isArray(fEntry.v)) return;
          const builtVars = fEntry.v.map(([sz, qArr]) => {
            const st = {};
            whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
            return { size: sz || "Freesize", stocks: st };
          });
          const tempProd = {
            sku: code,
            name: fEntry.n || ("Sản phẩm Havana " + code),
            subType: SALE_RULES_MASTER[code].subType || "",
            origPrice: SALE_RULES_MASTER[code].origPrice || 299000,
            salePrice: SALE_RULES_MASTER[code].salePrice || 159000,
            discountPct: SALE_RULES_MASTER[code].discountPct || 30,
            image: "",
            images: [],
            sapoImages: fEntry.si || [],
            video: "",
            sizeChart: "",
            variants: builtVars
          };
          if (v25CalcTotalStock(tempProd) >= MIN_TOTAL_STOCK_V25) {
            basePayload.products.push(tempProd);
          }
        });
      }

      basePayload.products = basePayload.products.filter((it) => v25CalcTotalStock(it) >= MIN_TOTAL_STOCK_V25);
      basePayload.synced_at = feed.synced_at || basePayload.synced_at;
      basePayload.synced_epoch = feedEpoch || basePayload.synced_epoch;
      basePayload.sync_source = "github_release_live_feed";
      return basePayload;
    };

    const v25SilentUpdateDOM = () => {
      if (typeof purgeExcludedSkusEverywhere === "function") purgeExcludedSkusEverywhere();
      v25EnforceMinStock(false);
      syncMobileGridClass();
      ensureDefaultSelectedSizes();
      renderDynamicCategoryBar();
      renderDynamicPriceTierBar();
      stripSapoImagesFromProducts(PRODUCTS);
      updateAdminMediaStats();

      const q = (document.getElementById("searchInput")?.value || "").trim().toLowerCase();
      const grid = document.getElementById("productGrid");
      const sentinel = document.getElementById("scrollSentinel");
      if (!grid) return;

      const prevSkusRendered = Array.from(grid.querySelectorAll(".card")).map((el) => el.id.replace(/^card-/, ""));
      const keepCount = Math.max(RENDER_BATCH_SIZE, renderedCount || RENDER_BATCH_SIZE);

      filteredProductsCache = PRODUCTS.filter((p) => {
        if (v25CalcTotalStock(p) < MIN_TOTAL_STOCK_V25) return false;
        if (document.body.classList.contains("is-admin")) {
          if (adminMediaFilter === "MISSING" && hasCustomProductMedia(p)) return false;
          if (adminMediaFilter === "HAS_MEDIA" && !hasCustomProductMedia(p)) return false;
        }
        const cat = classifyProduct(p);
        if (currentCategory !== "ALL" && cat !== currentCategory) return false;
        const sPrice = getProductSalePrice(p);
        if (currentPriceTier !== "ALL") {
          if (String(currentPriceTier).startsWith("EXACT_")) {
            if (sPrice !== Number(String(currentPriceTier).slice(6))) return false;
          } else if (currentPriceTier === "UNDER_99" && sPrice > 99000) return false;
          else if (currentPriceTier === "159" && (sPrice < 149000 || sPrice > 169000)) return false;
          else if (currentPriceTier === "199_UP" && sPrice < 190000) return false;
        }
        if (q && !p.sku.toLowerCase().includes(q) && !p.name.toLowerCase().includes(q)) return false;
        if (onlyInStock && getProductTotal(p) < MIN_TOTAL_STOCK_V25) return false;
        return true;
      });

      const badgeCnt = document.getElementById("countResultBadge");
      if (badgeCnt) {
        badgeCnt.textContent = currentLang === "VI" ? `${filteredProductsCache.length} mẫu` : `${filteredProductsCache.length} items`;
      }

      if (!filteredProductsCache.length) {
        _preV25RenderCatalog();
        return;
      }

      const targetSlice = filteredProductsCache.slice(0, Math.min(keepCount, filteredProductsCache.length));
      const nextSkus = targetSlice.map((p) => p.sku);
      const sameOrder = prevSkusRendered.length === nextSkus.length && prevSkusRendered.every((s, idx) => s === nextSkus[idx]);

      if (sameOrder && prevSkusRendered.length > 0) {
        targetSlice.forEach((p, idx) => {
          const cardEl = document.getElementById("card-" + p.sku);
          if (!cardEl) return;
          if (cardEl.querySelector("video")) {
            const tmp = document.createElement("div");
            tmp.innerHTML = buildCardInnerHtml(p, idx);
            const newBody = tmp.querySelector(".card-body");
            const curBody = cardEl.querySelector(".card-body");
            if (newBody && curBody && curBody.innerHTML !== newBody.innerHTML) curBody.innerHTML = newBody.innerHTML;
          } else {
            const nextHtml = buildCardInnerHtml(p, idx);
            if (cardEl.innerHTML !== nextHtml) cardEl.innerHTML = nextHtml;
          }
        });
      } else {
        grid.innerHTML = targetSlice.map((p, idx) => `<article class="card" id="card-${p.sku}">${buildCardInnerHtml(p, idx)}</article>`).join("");
        renderedCount = targetSlice.length;
        if (sentinel) {
          sentinel.textContent = renderedCount < filteredProductsCache.length
            ? `Đang hiển thị ${renderedCount}/${filteredProductsCache.length} mẫu — Cuộn xuống để xem tiếp...`
            : "";
        }
        ensureScrollObserver();
      }
    };
    window.silentUpdateCatalogInPlace = v25SilentUpdateDOM;

    refreshLiveSapoStock = async function(silent = true) {
      if (v25SyncInFlight) return false;
      v25SyncInFlight = true;
      const timeEl = document.getElementById("liveSyncTimeText");
      if (!silent && timeEl) {
        timeEl.textContent = currentLang === "VI" ? "Đang cập nhật tồn..." : "Syncing stock...";
      }
      try {
        let data = null;
        if (location.protocol.startsWith("http")) {
          try {
            const qs = (!silent ? "force=1&" : "") + "t=" + Date.now();
            const apiResp = await fetch("./api/stock?" + qs, { cache: "no-store" });
            if (apiResp.ok && (apiResp.headers.get("content-type") || "").includes("application/json")) {
              data = await apiResp.json();
            }
          } catch (_) {}
        }

        if (!data || !Array.isArray(data.products)) {
          try {
            const jsonResp = await fetch("./sapo_5_kho.json?t=" + Date.now(), { cache: "no-store" });
            if (jsonResp.ok) data = await jsonResp.json();
          } catch (_) {}
        }

        if (!data || !Array.isArray(data.products)) {
          data = {
            synced_at: lastSyncedStamp || "",
            warehouses: WAREHOUSE_CONFIG,
            products: JSON.parse(JSON.stringify(PRODUCTS))
          };
        }

        if (data.sync_source !== "cloudflare_edge_sapo_live") {
          const liveFeed = await v25FetchLiveRelease(!silent);
          if (liveFeed) data = v25ApplyReleaseFeed(data, liveFeed);
        }

        if (data && Array.isArray(data.products)) {
          if (Array.isArray(data.warehouses) && data.warehouses.length) {
            WAREHOUSE_CONFIG = data.warehouses;
          }
          try {
            const srResp = await fetch("./sale_rules.json?t=" + Date.now(), { cache: "no-store" });
            if (srResp.ok) {
              const remoteRules = await srResp.json();
              if (remoteRules && Object.keys(remoteRules).length >= 10) {
                v25ApplyPriceFixes(remoteRules);
                SALE_RULES_MASTER = remoteRules;
              }
            }
          } catch (_) {}

          const cleanLiveProds = data.products.filter((p) => v25CalcTotalStock(p) >= MIN_TOTAL_STOCK_V25);
          stripSapoImagesFromProducts(cleanLiveProds);
          PRODUCTS = mergeAndApplySaleRules(cleanLiveProds, SALE_RULES_MASTER);
          v25EnforceMinStock(true);

          if (timeEl && data.synced_at) {
            timeEl.textContent = (currentLang === "VI" ? "Cập nhật: " : "Synced: ") + data.synced_at;
          }
          if (!silent && data.synced_at) {
            showToast(`⚡ Đã đồng bộ tồn 5 kho (${data.synced_at}) • Hiện ${PRODUCTS.length} mã tồn ≥ 3`);
          }
          lastSyncedStamp = data.synced_at || "";

          v25SilentUpdateDOM();
          updateCartBar();
          const billModalEl = document.getElementById("billModal");
          if (billModalEl && billModalEl.classList.contains("open")) {
            if (!cart.length) closeBillModal();
            else renderBillModal();
          }
          return true;
        }
      } catch (_) {
      } finally {
        v25SyncInFlight = false;
      }
      return false;
    };

    v25EnforceMinStock(false);
    v25SilentUpdateDOM();
    updateCartBar();
    setInterval(() => {
      if (document.visibilityState === "visible") refreshLiveSapoStock(true);
    }, 45 * 1000);
    window.addEventListener("focus", () => refreshLiveSapoStock(true), { passive: true });
    refreshLiveSapoStock(true);'''


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


def calc_product_total_stock(prod: dict) -> int:
    total = 0
    for v in (prod.get("variants") or []):
        stocks = v.get("stocks") or {}
        if isinstance(stocks, dict):
            for qty in stocks.values():
                try:
                    total += max(0, int(qty or 0))
                except (ValueError, TypeError):
                    pass
    return total


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
    custom_imgs, sapo_imgs = [], []

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
    return num * 1000 if 0 < num < 1000 else num


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


def upgrade_repo_files_v25() -> bool:
    changed = False

    os.makedirs(os.path.join("functions", "api"), exist_ok=True)
    stock_js_path = os.path.join("functions", "api", "stock.js")
    old_js = ""
    if os.path.exists(stock_js_path):
        with open(stock_js_path, "r", encoding="utf-8") as f:
            old_js = f.read()
    if old_js.strip() != CF_STOCK_JS_V25.strip():
        with open(stock_js_path, "w", encoding="utf-8") as f:
            f.write(CF_STOCK_JS_V25)
        print("[+] Da cap nhat functions/api/stock.js -> v25 (MIN_TOTAL_STOCK = 3)")
        changed = True

    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            html_orig = f.read()
        html = html_orig

        try:
            s_i = html.find("/* [BLOCK:DATA_PAYLOAD_START] */")
            e_i = html.find("/* [BLOCK:DATA_PAYLOAD_END] */")
            if s_i != -1 and e_i > s_i:
                seg = html[s_i:e_i]
                m_wh = re.search(r"let WAREHOUSE_CONFIG = (\[[^\n]*\]);", seg) or re.search(r"let WAREHOUSE_CONFIG = (\[.*?\]);", seg, re.S)
                m_sr = re.search(r"let SALE_RULES_MASTER = (\{[^\n]*\});", seg) or re.search(r"let SALE_RULES_MASTER = (\{.*?\});", seg, re.S)
                m_bp = re.search(r"let BASELINE_PRODUCTS = (\[[^\n]*\]);", seg) or re.search(r"let BASELINE_PRODUCTS = (\[.*?\]);", seg, re.S)
                if m_wh and m_sr and m_bp:
                    wh_data = json.loads(m_wh.group(1))
                    sr_data = json.loads(m_sr.group(1))
                    bp_data = json.loads(m_bp.group(1))
                    for ex in EXCLUDED_SKUS:
                        sr_data.pop(ex, None)
                    for code, (orig_p, sale_p, disc_p, _) in PRICE_PATCH_V25.items():
                        if code in sr_data:
                            sr_data[code]["origPrice"] = orig_p
                            sr_data[code]["salePrice"] = sale_p
                            sr_data[code]["discountPct"] = disc_p
                    filtered_bp = []
                    for item in bp_data:
                        c = clean_base_code(str(item.get("sku") or "").split("-")[0])
                        if not c or c in EXCLUDED_SKUS:
                            continue
                        if c in PRICE_PATCH_V25:
                            item["origPrice"] = PRICE_PATCH_V25[c][0]
                            item["salePrice"] = PRICE_PATCH_V25[c][1]
                            item["discountPct"] = PRICE_PATCH_V25[c][2]
                        if calc_product_total_stock(item) >= MIN_TOTAL_STOCK:
                            filtered_bp.append(item)
                    new_seg = (
                        "/* [BLOCK:DATA_PAYLOAD_START] */\n"
                        f"    let WAREHOUSE_CONFIG = {json.dumps(wh_data, ensure_ascii=False)};\n"
                        f"    let SALE_RULES_MASTER = {json.dumps(sr_data, ensure_ascii=False)};\n"
                        f"    let BASELINE_PRODUCTS = {json.dumps(filtered_bp, ensure_ascii=False)};\n"
                        "    let PRODUCTS = BASELINE_PRODUCTS.map((p) => Object.assign({}, p));\n"
                        "    "
                    )
                    html = html[:s_i] + new_seg + html[e_i:]
        except Exception as e:
            print("[*] Bo qua buoc loc khoi tao DATA_PAYLOAD:", e)

        end_logic = html.rfind("/* [BLOCK:APP_LOGIC_END] */")
        if end_logic != -1:
            cuts = [
                html.find(m, 0, end_logic)
                for m in ("/* === v22:", "/* === v23:", "/* === v25:")
            ]
            valid_cuts = [pos for pos in cuts if pos != -1]
            cut_at = min(valid_cuts) if valid_cuts else end_logic
            html = (
                html[:cut_at].rstrip()
                + "\n\n    "
                + V25_INDEX_JS_ADDON.strip()
                + "\n    /* [BLOCK:APP_LOGIC_END] */"
                + html[end_logic + len("/* [BLOCK:APP_LOGIC_END] */"):]
            )

        html = re.sub(
            r'(<span id="onlyInStockText">)(?:Còn tồn|In Stock)(</span>)',
            r'\1Tồn ≥ 3\2',
            html,
            count=1
        )

        if html != html_orig:
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("[+] Da nang cap index.html -> v25 (Tu dong loc ma tong ton >= 3, khong them QR)")
            changed = True

    return changed


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


def purge_wrong_custom_media_once():
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
    for folder in ("images", "videos", "sizes"):
        if not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for fname in files:
                if fname.lower() == "keep.txt":
                    continue
                fpath = os.path.join(root, fname)
                for _, rx in compiled:
                    if rx.search(fname) or rx.search(root):
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
                        break

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
    target_xlsx = "Black Friday.xlsx" if os.path.exists("Black Friday.xlsx") else None
    if not target_xlsx:
        excel_files = [f for f in glob.glob("*.xlsx") if not os.path.basename(f).startswith("~$")]
        if excel_files:
            excel_files.sort(key=lambda x: (0 if "black" in x.lower() else 1, len(x), x))
            target_xlsx = excel_files[0]

    sale_rules = {}
    if target_xlsx:
        try:
            import openpyxl
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
            import openpyxl

        try:
            wb_edit = openpyxl.load_workbook(target_xlsx)
            xlsx_patched = False
            if "Tổng CH" in wb_edit.sheetnames:
                ws_e = wb_edit["Tổng CH"]
                for r_idx in range(2, ws_e.max_row + 1):
                    c_val = str(ws_e.cell(r_idx, 1).value or "").strip().upper()
                    if c_val in PRICE_PATCH_V25:
                        _, target_sale, _, target_pct_dec = PRICE_PATCH_V25[c_val]
                        cur_sale = parse_money(ws_e.cell(r_idx, 3).value)
                        if cur_sale in (99000, 159000) and cur_sale != target_sale:
                            ws_e.cell(r_idx, 3).value = target_sale
                            ws_e.cell(r_idx, 4).value = target_pct_dec
                            xlsx_patched = True
            if xlsx_patched:
                wb_edit.save(target_xlsx)
                print(f"[+] Da cap nhat gia AO1010/AO726/AO1016 trong {target_xlsx}")
        except Exception as e:
            print("[*] Bo qua ghi truc tiep xlsx:", e)

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
                        "discountPct": round((1 - sale_p / orig_p) * 100) if orig_p > sale_p > 0 else round(pct_v, 1),
                        "subType": sub_type or prev_sub,
                    }
    elif os.path.exists("sale_rules.json"):
        with open("sale_rules.json", "r", encoding="utf-8") as f:
            sale_rules = json.load(f)
    elif os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            m = re.search(r"let SALE_RULES_MASTER = (\{[^\n]*\});", f.read())
            if m:
                sale_rules = json.loads(m.group(1))

    for ex_code in EXCLUDED_SKUS:
        sale_rules.pop(ex_code, None)
    for code, (orig_p, sale_p, disc_p, _) in PRICE_PATCH_V25.items():
        if code in sale_rules and sale_rules[code].get("salePrice") in (99000, 159000, sale_p):
            sale_rules[code]["origPrice"] = orig_p
            sale_rules[code]["salePrice"] = sale_p
            sale_rules[code]["discountPct"] = disc_p

    if sale_rules:
        with open("sale_rules.json", "w", encoding="utf-8") as f:
            json.dump(sale_rules, f, ensure_ascii=False, indent=2)
    return sale_rules


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
                    dest_dir = "sizes" if ("size" in low or "bang_size" in low or "bangsize" in low) else "images"
                    shutil.copy2(src_path, os.path.join(dest_dir, target_name))
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

    all_repo_files = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if fname.startswith(".") or fname.lower() in ("keep.txt", "readme.md"):
                continue
            rel = os.path.join(root, fname).replace("\\", "/").lstrip("./")
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
        entry = media_map.setdefault(matched_code, {"images": [], "video": "", "sizeChart": ""})
        if is_vid:
            if not entry["video"]:
                entry["video"] = encoded_url
        elif is_img:
            if rel_path.startswith("sizes/") or "size" in fname_only.lower() or "bangsize" in fname_only.lower():
                if not entry["sizeChart"]:
                    entry["sizeChart"] = encoded_url
            elif encoded_url not in entry["images"]:
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
    if up.startswith("AD"): return "AO_DAI"
    if up.startswith("ATH"): return "AO_THUN"
    if up.startswith(("AO", "AE")): return "AO_SO_MI" if ("sơ mi" in name_l or "sơmi" in name_l) else "AO_KIEU"
    if up.startswith(("AK", "HD")): return "AO_KHOAC"
    if up.startswith("LEN"): return "AO_LEN"
    if up.startswith("CV"): return "CHAN_VAY"
    if up.startswith(("DA", "BE")): return "DAM"
    if up.startswith("SAT"): return "SET_BO"
    if up.startswith("Q"): return "QUAN"
    return "AO_KIEU"


def fetch_json(path: str, headers: dict) -> dict:
    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(f"https://{DOMAIN}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if raw.lstrip().startswith("<"):
                    raise RuntimeError("Sapo Cookie/Token da het han!")
                return json.loads(raw)
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(1)
    raise last_err


def extract_base_code_from_sapo_product(p: dict, sale_rules: dict) -> str:
    name_tokens = re.findall(r"\b([A-Z]{1,5}\d{1,5})\b", norm_nfc(p.get("name") or "").upper())
    name_sale_codes = [tok for tok in reversed(name_tokens) if sale_rules and tok in sale_rules]
    var_sale_codes, candidates = [], []
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
        return name_sale_codes[0]
    if var_sale_codes:
        return var_sale_codes[0]
    for tok in reversed(name_tokens):
        if not tok.startswith("PVN"):
            candidates.append(tok)
    return candidates[0] if candidates else ""


def write_outputs(warehouses_sorted: list, products_out: list, synced_at_str: str, sale_rules: dict = None):
    filtered_out = [p for p in products_out if calc_product_total_stock(p) >= MIN_TOTAL_STOCK]
    payload = {
        "min_stock_rule": MIN_TOTAL_STOCK,
        "synced_at": synced_at_str,
        "warehouses": warehouses_sorted,
        "products": filtered_out,
    }
    with open("sapo_5_kho.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            html_src = f.read()
        s_i = html_src.find("/* [BLOCK:DATA_PAYLOAD_START] */")
        e_i = html_src.find("/* [BLOCK:DATA_PAYLOAD_END] */")
        if s_i != -1 and e_i > s_i:
            e_i += len("/* [BLOCK:DATA_PAYLOAD_END] */")
            wh_js = json.dumps(warehouses_sorted, ensure_ascii=False)
            pr_js = json.dumps(filtered_out, ensure_ascii=False)
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
    return os.path.exists(urllib.parse.unquote(u.lstrip("./")))


def apply_priority_media_to_list(products_list: list, preserved_by_sku: dict, purged_skus: set = None) -> list:
    purged_skus = purged_skus or set()
    codes = [str(p.get("sku") or "").upper() for p in products_list]
    repo_media = scan_repo_media_folders(codes)

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
            custom_imgs = [u for u in (repo_m.get("images") or []) if "SAT533" not in u.upper() and local_media_url_exists(u)]
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
            sapo_imgs = [u for u in sapo_imgs if u not in sat533_sapo_set and "sat533" not in u.lower()]

        final_imgs = custom_imgs if len(custom_imgs) > 0 else sapo_imgs
        p["sapoImages"] = sapo_imgs
        p["hasCustomMedia"] = bool(len(custom_imgs) > 0 or vid)
        p["images"] = final_imgs
        p["image"] = final_imgs[0] if final_imgs else ""
        p["video"] = vid
        p["sizeChart"] = szc

    return products_list


def build_compact_stock_feed(warehouses_sorted: list, all_sale_products: list, synced_at_str: str) -> str:
    wh_ids = [w.get("id") for w in warehouses_sorted]
    items_compact = {}
    for p in all_sale_products:
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
        "v": 25,
        "full": 1,
        "min_stock": MIN_TOTAL_STOCK,
        "synced_at": synced_at_str,
        "synced_epoch": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        "warehouses": warehouses_sorted,
        "items": items_compact,
    }
    return json.dumps(feed_obj, ensure_ascii=False, separators=(",", ":"))


def publish_live_release_feed(warehouses_sorted: list, all_sale_products: list, synced_at_str: str) -> bool:
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "moonchuyensi-cmd/havana-sale").strip()
    if not gh_token or not repo_slug:
        return False

    compact_body = build_compact_stock_feed(warehouses_sorted, all_sale_products, synced_at_str)
    if len(compact_body) > 120000:
        data_tmp = json.loads(compact_body)
        for k in data_tmp.get("items", {}).values():
            k.pop("n", None)
        compact_body = json.dumps(data_tmp, ensure_ascii=False, separators=(",", ":"))

    api_headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "havana-sale-auto-sync-v25",
    }

    tag_url = f"https://api.github.com/repos/{repo_slug}/releases/tags/live-stock"
    rel_id = None
    try:
        req = urllib.request.Request(tag_url, headers=api_headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            rel_id = json.loads(resp.read().decode("utf-8")).get("id")
    except urllib.error.HTTPError as he:
        if he.code != 404:
            print(f"[!] Loi kiem tra release live-stock ({he.code}): {he}")
    except Exception:
        pass

    payload_bytes = json.dumps({
        "tag_name": "live-stock",
        "name": f"Live Sapo Stock ({synced_at_str}) [Min Stock >= {MIN_TOTAL_STOCK}]",
        "body": compact_body,
        "draft": False,
        "prerelease": True,
    }, ensure_ascii=False).encode("utf-8")

    try:
        url = f"https://api.github.com/repos/{repo_slug}/releases/{rel_id}" if rel_id else f"https://api.github.com/repos/{repo_slug}/releases"
        method = "PATCH" if rel_id else "POST"
        req = urllib.request.Request(url, data=payload_bytes, headers=api_headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            _ = resp.read()
        print(f"[+] Da phat Live Stock Feed v25 ({len(compact_body)} bytes) luc {synced_at_str}!")
        return True
    except Exception as e:
        print(f"[!] Khong cap nhat duoc Release live-stock: {e}")
        return False


def compute_products_fingerprint(products_list: list) -> str:
    norm_items = [
        {
            "s": p.get("sku"),
            "op": p.get("origPrice"),
            "sp": p.get("salePrice"),
            "im": p.get("images") or [],
            "vd": p.get("video") or "",
            "sz": p.get("sizeChart") or "",
            "vr": p.get("variants") or [],
        }
        for p in sorted(products_list, key=lambda x: str(x.get("sku") or ""))
    ]
    return hashlib.sha256(json.dumps(norm_items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def compute_media_rules_fingerprint(products_list: list) -> str:
    norm_items = [
        {
            "s": p.get("sku"),
            "op": p.get("origPrice"),
            "sp": p.get("salePrice"),
            "im": p.get("images") or [],
            "vd": p.get("video") or "",
            "sz": p.get("sizeChart") or "",
        }
        for p in sorted(products_list, key=lambda x: str(x.get("sku") or ""))
    ]
    return hashlib.sha256(json.dumps(norm_items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def last_git_commit_age_seconds() -> int:
    try:
        out = subprocess.check_output(["git", "log", "-1", "--format=%ct"], text=True, timeout=5).strip()
        return max(0, int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - int(out))
    except Exception:
        return 999999


def main():
    upgraded_files = upgrade_repo_files_v25()
    purged_now = purge_wrong_custom_media_once()
    sale_rules = load_black_friday_rules()
    filter_only_bf = len(sale_rules) > 0

    existing_warehouses, existing_products, preserved_media_by_sku = [], [], {}
    if os.path.exists("sapo_5_kho.json"):
        try:
            with open("sapo_5_kho.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
            existing_warehouses = old_data.get("warehouses") or []
            existing_products = old_data.get("products") or []
        except Exception:
            pass
    elif os.path.exists("index.html"):
        try:
            with open("index.html", "r", encoding="utf-8") as f:
                h_src = f.read()
            m_wh = re.search(r"let WAREHOUSE_CONFIG = (\[[^\n]*\]);", h_src)
            m_bp = re.search(r"let BASELINE_PRODUCTS = (\[[^\n]*\]);", h_src)
            if m_wh:
                existing_warehouses = json.loads(m_wh.group(1))
            if m_bp:
                existing_products = json.loads(m_bp.group(1))
        except Exception:
            pass

    for op in existing_products:
        c_imgs, s_imgs, vid, szc = split_custom_and_sapo_media(op)
        c = clean_base_code(str(op.get("sku") or "").split("-")[0])
        if c:
            preserved_media_by_sku[c] = {"customImages": c_imgs, "sapoImages": s_imgs, "video": vid, "sizeChart": szc}

    candidates = build_header_candidates()
    loc_meta, all_products = {}, []
    sapo_err_reason = ""

    if not candidates:
        sapo_err_reason = "Chưa có SAPO_COOKIE trong GitHub Secrets (hoặc .sapo_cookie.txt)"
        print(f"[*] {sapo_err_reason} -> lọc trực tiếp trên dữ liệu hiện có.")
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
                except Exception:
                    pass

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
                                        all_products.extend(fut.result().get("products") or [])
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
        updated_existing = []
        for ep in existing_products:
            c = clean_base_code(str(ep.get("sku") or "").split("-")[0])
            if not c or c in EXCLUDED_SKUS or (filter_only_bf and c not in sale_rules):
                continue
            if c in sale_rules:
                r_info = sale_rules[c]
                orig_p = r_info.get("origPrice") or int(ep.get("origPrice") or 299000)
                sale_p = r_info.get("salePrice") or int(ep.get("salePrice") or 159000)
                disc_p = max(1, round((1.0 - sale_p / orig_p) * 100)) if orig_p > sale_p > 0 else r_info.get("discountPct", 30)
                sub_t = r_info.get("subType") or ep.get("subType") or PK_KNOWN_SUBTYPES.get(c, "")
                ep["sku"] = c
                ep["origPrice"] = orig_p
                ep["salePrice"] = sale_p
                ep["discountPct"] = disc_p
                ep["subType"] = sub_t
                ep["category"] = infer_category(c, ep.get("name") or c, sub_t)
            if calc_product_total_stock(ep) >= MIN_TOTAL_STOCK:
                updated_existing.append(ep)

        cleaned = apply_priority_media_to_list(updated_existing, preserved_media_by_sku, purged_now)
        wh_out = existing_warehouses or [
            {"id": "wh_havana", "sapo_name": "HAVANA", "name": "HAVANA (93-95 Hồ Phi Tích)"},
            {"id": "wh_online", "sapo_name": "HAVANA ONLINE", "name": "HAVANA ONLINE"},
            {"id": "wh_pct", "sapo_name": "261 - 263 phan chu trinh", "name": "261 - 263 Phan Châu Trinh"},
            {"id": "wh_auco", "sapo_name": "101 Âu Cơ", "name": "101 Âu Cơ"},
            {"id": "wh_leduan", "sapo_name": "240 Lê Duẩn", "name": "240 Lê Duẩn"},
        ]
        write_outputs(wh_out, cleaned, vn_now_str(), sale_rules)
        print(f"[+] Da loc & giu lai {len(cleaned)} ma co tong ton >= {MIN_TOTAL_STOCK}!")
        write_github_summary(
            f"### ⚠️ Sapo chưa trả dữ liệu mới — Đã lọc trên dữ liệu sẵn có\n"
            f"- **Nguyên nhân Sapo:** `{sapo_err_reason}`\n"
            f"- **Số mã giữ lại (Tồn từ {MIN_TOTAL_STOCK} trở lên):** `{len(cleaned)}` mã"
        )
        return

    grouped_by_code = {}
    for p in all_products:
        variants_raw = p.get("variants", [])
        if not variants_raw:
            continue
        base_code = extract_base_code_from_sapo_product(p, sale_rules)
        if not base_code or base_code in EXCLUDED_SKUS or (filter_only_bf and base_code not in sale_rules):
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
        if base_code in grouped_by_code:
            grouped_by_code[base_code]["variants"].extend(v_list)
            for u in sapo_imgs_found:
                if u not in grouped_by_code[base_code]["sapoImages"]:
                    grouped_by_code[base_code]["sapoImages"].append(u)
        else:
            grouped_by_code[base_code] = {
                "sku": base_code,
                "name": pname_nfc,
                "subType": sub_type,
                "category": infer_category(base_code, pname_nfc, sub_type),
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

    all_sale_products = apply_priority_media_to_list(list(grouped_by_code.values()), preserved_media_by_sku, purged_now)
    warehouses_sorted = [
        {k: v for k, v in item.items() if k != "order"}
        for item in sorted(loc_meta.values(), key=lambda x: x["order"])
    ]

    removed_low_stock = [
        f"{p.get('sku')}({calc_product_total_stock(p)})"
        for p in all_sale_products
        if calc_product_total_stock(p) < MIN_TOTAL_STOCK
    ]
    products_out = [
        p for p in all_sale_products
        if calc_product_total_stock(p) >= MIN_TOTAL_STOCK
    ]

    now_stamp = vn_now_str()
    old_sku_set = {str(p.get("sku") or "").upper() for p in existing_products}
    new_sku_set = {str(p.get("sku") or "").upper() for p in products_out}
    sku_list_changed = (old_sku_set != new_sku_set)

    old_full_fp = compute_products_fingerprint(existing_products) if existing_products else ""
    old_media_fp = compute_media_rules_fingerprint(existing_products) if existing_products else ""
    new_full_fp = compute_products_fingerprint(products_out)
    new_media_fp = compute_media_rules_fingerprint(products_out)

    live_feed_ok = publish_live_release_feed(warehouses_sorted, all_sale_products, now_stamp)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    commit_age = last_git_commit_age_seconds()
    media_or_price_changed = (old_media_fp != new_media_fp)
    stock_changed = (old_full_fp != new_full_fp)

    should_commit_main = True
    if (
        event_name == "schedule"
        and live_feed_ok
        and not upgraded_files
        and not purged_now
        and not media_or_price_changed
        and not sku_list_changed
    ):
        if not stock_changed or commit_age < 1800:
            should_commit_main = False

    if should_commit_main:
        if os.path.exists(".skip_git_commit"):
            os.remove(".skip_git_commit")
        write_outputs(warehouses_sorted, products_out, now_stamp, sale_rules)
    else:
        with open(".skip_git_commit", "w", encoding="utf-8") as sf:
            sf.write(now_stamp)

    print(f"[+] Hoan tat Auto-Sync v25.1 ({now_stamp}): Giu {len(products_out)} ma (ton >= {MIN_TOTAL_STOCK}), da loai {len(removed_low_stock)} ma ton thap.")
    sample_removed = ", ".join(removed_low_stock[:20]) + ("..." if len(removed_low_stock) > 20 else "")
    write_github_summary(
        f"### ✅ Đồng bộ Sapo v25.1 — Lọc Tồn từ {MIN_TOTAL_STOCK} trở lên ({now_stamp})\n"
        f"- **Mã đủ điều kiện (Tổng tồn từ {MIN_TOTAL_STOCK} trở lên):** `{len(products_out)}` mã\n"
        f"- **Mã tự động loại khỏi file (Tổng tồn 0–2):** `{len(removed_low_stock)}` mã ({sample_removed or 'Không có'})"
    )


if __name__ == "__main__":
    main()
