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
# AUTO_SYNC_SAPO.PY (v29 - BO SUNG GHI CHU TON THUC TE TREN BILL + LOC TON >= 3)
# Cap nhat dong bo Popup Bill, Anh PNG Bill & Tin nhan Zalo + Giu nguyen v28
# ==============================================================================

MIN_TOTAL_STOCK = 3
MIN_VARIANT_STOCK = 3
DEFAULT_COOKIE = ""
DOMAIN = os.environ.get("SAPO_DOMAIN", "havanago.mysapogo.com").strip()
SKU_REGEX = re.compile(r"^([A-Z]{1,5}\d{1,5})", re.I)
EXCLUDED_SKUS = {"LEN374", "QJ358"}
PRICE_PATCH_V29 = {
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

FORCE_RESET_TO_SAPO_SKUS = {"SAT532", "CV538"}
PURGE_STATE_FILE = ".purged_wrong_media_v27.json"

CF_STOCK_JS_V29 = r'''// Cloudflare Pages Function: /api/stock (v29 Sync + Epoch Guard + Cookie Expiry Flag + Variant >= 3)
const MIN_TOTAL_STOCK = 3;
const MIN_VARIANT_STOCK = 3;
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
const FORCE_SAPO_MEDIA_SKUS = new Set(["SAT532", "CV538"]);
const GITHUB_REPO_SLUG = "moonchuyensi-cmd/havana-sale";
const SAPO_CDN_RX = /(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)/i;
const PRICE_PATCH_V29 = {
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

function filterProductVariantsMinStock(variantsArr, minVarStock = MIN_VARIANT_STOCK) {
  if (!Array.isArray(variantsArr)) return [];
  const order = [];
  const map = {};
  for (const v of variantsArr) {
    if (!v) continue;
    const sz = String(v.size || "Freesize").trim() || "Freesize";
    if (!map[sz]) {
      order.push(sz);
      map[sz] = {};
    }
    const st = (v.stocks && typeof v.stocks === "object") ? v.stocks : {};
    for (const [wid, q] of Object.entries(st)) {
      map[sz][wid] = (map[sz][wid] || 0) + Math.max(0, Number(q) || 0);
    }
  }
  const out = [];
  for (const sz of order) {
    const st = map[sz];
    let vTot = 0;
    for (const q of Object.values(st)) vTot += q;
    if (vTot >= minVarStock) {
      out.push({ size: sz, stocks: st });
    }
  }
  return out;
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
  const cacheKey = new Request(new URL("/api/stock-edge-v29", request.url).toString());
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
        for (const [k, patch] of Object.entries(PRICE_PATCH_V29)) {
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
          item.variants = filterProductVariantsMinStock(liveByBase[code] || [], MIN_VARIANT_STOCK);
          if (nameByBase[code]) item.name = nameByBase[code];
        }
        if (saleRules) {
          for (const [code, rule] of Object.entries(saleRules)) {
            if (!seen.has(code) && !EXCLUDED_SKUS.has(code) && liveByBase[code]) {
              const cleanVars = filterProductVariantsMinStock(liveByBase[code], MIN_VARIANT_STOCK);
              if (cleanVars.length > 0) {
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
                  variants: cleanVars
                });
              }
            }
          }
        }
        const nowVN = new Date(Date.now() + 7 * 3600 * 1000);
        const pad = (n) => String(n).padStart(2, "0");
        payload.synced_at = `${pad(nowVN.getUTCHours())}:${pad(nowVN.getUTCMinutes())} ${pad(nowVN.getUTCDate())}/${pad(nowVN.getUTCMonth() + 1)}`;
        payload.synced_epoch = Math.floor(Date.now() / 1000);
        payload.sapo_cookie_expired = false;
        syncedLive = true;
        syncSource = "cloudflare_edge_sapo_live";
        break;
      }
    } catch (_) {}
  }

  if (!syncedLive && Array.isArray(payload.products)) {
    try {
      const ghRes = await fetch(`https://api.github.com/repos/${GITHUB_REPO_SLUG}/releases/tags/live-stock`, {
        headers: { "Accept": "application/vnd.github+json", "User-Agent": "havana-sale-cf-edge-v29" }
      });
      if (ghRes.ok) {
        const relJson = await ghRes.json();
        if (relJson && relJson.body) {
          const feed = JSON.parse(relJson.body);
          if (feed && feed.items && Array.isArray(feed.warehouses)) {
            const feedEpoch = Number(feed.synced_epoch || 0);
            const baseEpoch = Number(payload.synced_epoch || 0);
            const feedMins = parseStampToMinutes(feed.synced_at);
            const baseMins = parseStampToMinutes(payload.synced_at);
            const isNewer = (feedEpoch > 0 && baseEpoch > 0)
              ? (feedEpoch >= baseEpoch)
              : (feedMins > baseMins);

            if (isNewer) {
              const whIds = feed.warehouses.map((w) => w.id);
              payload.warehouses = feed.warehouses;
              const seenInBase = new Set();
              for (const item of payload.products) {
                const code = String(item.sku || "").trim().toUpperCase().split("-")[0];
                seenInBase.add(code);
                const fEntry = feed.items[code];
                if (fEntry && Array.isArray(fEntry.v)) {
                  const rawVars = fEntry.v.map(([sz, qArr]) => {
                    const st = {};
                    whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
                    return { size: sz || "Freesize", stocks: st };
                  });
                  item.variants = filterProductVariantsMinStock(rawVars, MIN_VARIANT_STOCK);
                  if (fEntry.n) item.name = fEntry.n;
                  if (Array.isArray(fEntry.si) && fEntry.si.length) {
                    sapoImgsByBase[code] = fEntry.si;
                  }
                } else if (feed.full) {
                  item.variants = [];
                }
              }
              if (saleRules) {
                for (const [code, fEntry] of Object.entries(feed.items)) {
                  if (!seenInBase.has(code) && saleRules[code] && !EXCLUDED_SKUS.has(code) && Array.isArray(fEntry.v)) {
                    const rule = saleRules[code];
                    const rawVars = fEntry.v.map(([sz, qArr]) => {
                      const st = {};
                      whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
                      return { size: sz || "Freesize", stocks: st };
                    });
                    const cleanVars = filterProductVariantsMinStock(rawVars, MIN_VARIANT_STOCK);
                    if (cleanVars.length > 0) {
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
                        variants: cleanVars
                      });
                    }
                  }
                }
              }
              payload.synced_at = feed.synced_at;
              payload.synced_epoch = feedEpoch || payload.synced_epoch || 0;
              payload.sapo_cookie_expired = Boolean(feed.cookie_expired);
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
      p.variants = filterProductVariantsMinStock(p.variants || [], MIN_VARIANT_STOCK);
      return p.variants.length > 0 && calcTotalStock(p) >= MIN_TOTAL_STOCK;
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
      const forceSapoOnly = FORCE_SAPO_MEDIA_SKUS.has(code);
      const rawImgs = Array.isArray(p.images) ? p.images : [];
      let customImgs = [];
      let sapoImgs = [];
      const seedSapo = (forceSapoOnly && (sapoImgsByBase[code] || []).length > 0)
        ? [...sapoImgsByBase[code]]
        : [...(sapoImgsByBase[code] || []), ...(Array.isArray(p.sapoImages) ? p.sapoImages : [])];

      for (const u of [p.image, ...rawImgs, ...seedSapo]) {
        const s = String(u || "").trim();
        if (!s || s.startsWith("data:image/svg+xml")) continue;
        if (isExternalSapoUrl(s)) {
          if (code === "SAT532" && (sat533SapoSet.has(s) || s.toLowerCase().includes("sat533"))) continue;
          if (!sapoImgs.includes(s)) sapoImgs.push(s);
        } else {
          if (forceSapoOnly) continue;
          if (!customImgs.includes(s)) customImgs.push(s);
        }
      }
      if (forceSapoOnly) {
        p.video = "";
        p.sizeChart = "";
      }
      const finalImgs = customImgs.length > 0 ? customImgs : sapoImgs;
      p.sapoImages = sapoImgs;
      p.hasCustomMedia = customImgs.length > 0 || Boolean(p.video && !isExternalSapoUrl(String(p.video)));
      p.images = finalImgs;
      p.image = finalImgs[0] || "";
    }
  }

  payload.min_stock_rule = MIN_TOTAL_STOCK;
  payload.min_variant_stock_rule = MIN_VARIANT_STOCK;
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

V29_INDEX_JS_ADDON = r'''/* === v29: BILL STOCK DISCLAIMER (MODAL + ZALO TEXT + PNG IMAGE) + v28 FULL ENGINE === */
    const MIN_TOTAL_STOCK_V29 = 3;
    const MIN_VARIANT_STOCK_V29 = 3;
    const FORCE_SAPO_MEDIA_V29 = new Set(["SAT532", "CV538"]);
    const BILL_NOTE_VI_V29 = "📌 Lưu ý: Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";
    const BILL_NOTE_EN_V29 = "📌 Note: Sale items have limited stock and may slightly differ from actual store inventory. Thank you for your understanding!";
    let v29SyncInFlight = false;
    let v29LastReleaseCheckMs = 0;

    if (typeof RESET_TO_SAPO_SKUS_V21 !== "undefined" && RESET_TO_SAPO_SKUS_V21 && typeof RESET_TO_SAPO_SKUS_V21.add === "function") {
      RESET_TO_SAPO_SKUS_V21.add("CV538");
      RESET_TO_SAPO_SKUS_V21.add("SAT532");
    }

    try {
      sessionStorage.removeItem("havana_gh_tree_v13");
      sessionStorage.removeItem("havana_live_rel_v22");
      sessionStorage.removeItem("havana_live_rel_v25");
      sessionStorage.removeItem("havana_live_rel_v26");
      sessionStorage.removeItem("havana_live_rel_v27");
      sessionStorage.removeItem("havana_live_rel_v28");
    } catch (_) {}

    if (typeof MEDIA_CACHE_VERSION !== "undefined" && MEDIA_CACHE_VERSION < 29) {
      MEDIA_CACHE_VERSION = 29;
    }

    (async function purgeStaleCv538VaultOnce() {
      try {
        if (!localStorage.getItem("havana_purged_cv538_v27")) {
          ["CV538", "SAT532"].forEach((targetCode) => {
            if (typeof vaultObjectUrlsBySku === "object" && vaultObjectUrlsBySku) {
              delete vaultObjectUrlsBySku[targetCode];
            }
            if (typeof vaultBlobsMap === "object" && vaultBlobsMap) {
              Object.keys(vaultBlobsMap).forEach((k) => {
                if (k.toUpperCase().includes(targetCode)) delete vaultBlobsMap[k];
              });
            }
          });
          if (typeof openVaultDB === "function") {
            const db = await openVaultDB();
            if (db) {
              const tx = db.transaction("files", "readwrite");
              const store = tx.objectStore("files");
              const req = store.getAll();
              req.onsuccess = () => {
                (req.result || []).forEach((row) => {
                  const rSku = String(row.sku || "").toUpperCase();
                  const rPath = String(row.path || "").toUpperCase();
                  if (FORCE_SAPO_MEDIA_V29.has(rSku) || rPath.includes("CV538") || rPath.includes("SAT532")) {
                    store.delete(row.path);
                  }
                });
              };
            }
          }
          localStorage.setItem("havana_purged_cv538_v27", "1");
        }
      } catch (_) {}
    })();

    // 1. CAP NHAT NOI DUNG GHI CHU TON KHO TREN CA 3 KENH BILL (POPUP + TEXT ZALO + ANH PNG)
    const v29GetBillDisclaimer = () => (currentLang === "EN" ? BILL_NOTE_EN_V29 : BILL_NOTE_VI_V29);

    const _preV29RenderBillModal = renderBillModal;
    renderBillModal = function() {
      _preV29RenderBillModal();
      const sumEl = document.getElementById("billSummaryBox");
      if (sumEl && !sumEl.querySelector(".bill-stock-disclaimer")) {
        const noteDiv = document.createElement("div");
        noteDiv.className = "bill-stock-disclaimer";
        noteDiv.style.cssText = "margin-top:8px;padding:9px 12px;border-radius:11px;background:#fffbeb;border:1px dashed #f59e0b;color:#92400e;font-size:12.5px;line-height:1.45;font-weight:600;";
        noteDiv.textContent = v29GetBillDisclaimer();
        sumEl.appendChild(noteDiv);
      }
    };

    const _preV29BuildFormattedBillText = buildFormattedBillText;
    buildFormattedBillText = function() {
      const baseTxt = _preV29BuildFormattedBillText();
      const noteLine = v29GetBillDisclaimer();
      if (baseTxt.includes("lệch so với tồn thực tế") || baseTxt.includes("differ from actual store inventory")) {
        return baseTxt;
      }
      return baseTxt + "\n--------------------------\n" + noteLine;
    };

    const v29RoundRectPath = (ctx, x, y, w, h, radii) => {
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") {
        ctx.roundRect(x, y, w, h, radii);
      } else {
        const r = Array.isArray(radii) ? (radii[0] || 12) : (radii || 12);
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
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

      const W = 760;
      const rowH = 62;
      const fontStack = "'Be Vietnam Pro', -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      const rawCanvasNote = currentLang === "EN"
        ? "* Note: Sale items have limited stock and may slightly differ from actual store inventory. Thank you for your understanding!"
        : "* Lưu ý: Hàng sale số lượng ít nên có thể lệch so với tồn thực tế, mong quý khách hàng thông cảm ạ!";

      // Tu dong ngat dong vua khit khung hoa don de khong bao gio bi tran chu
      const tmpCv = document.createElement("canvas");
      const tmpCtx = tmpCv.getContext("2d");
      const noteFont = `600 13.5px ${fontStack}`;
      tmpCtx.font = noteFont;
      const maxNoteW = W - 132;
      const words = rawCanvasNote.split(/\s+/);
      const noteLines = [];
      let curLine = "";
      words.forEach((w) => {
        const candidate = curLine ? (curLine + " " + w) : w;
        if (tmpCtx.measureText(candidate).width > maxNoteW && curLine) {
          noteLines.push(curLine);
          curLine = w;
        } else {
          curLine = candidate;
        }
      });
      if (curLine) noteLines.push(curLine);

      const noteLineH = 20;
      const noteBoxH = 16 + noteLines.length * noteLineH;
      const H = 314 + cart.length * rowH + (custNote ? 26 : 0) + noteBoxH;

      const canvas = document.createElement("canvas");
      canvas.width = W;
      canvas.height = H;
      const ctx = canvas.getContext("2d");

      // Nen ngoai xanh mint nhat
      ctx.fillStyle = "#f2f9f8";
      ctx.fillRect(0, 0, W, H);

      // Khung phieu trang
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = "#91d2ce";
      ctx.lineWidth = 3;
      v29RoundRectPath(ctx, 24, 24, W - 48, H - 48, 22);
      ctx.fill();
      ctx.stroke();

      // Dai tieu de xanh co vit dam
      ctx.fillStyle = "#165954";
      v29RoundRectPath(ctx, 24, 24, W - 48, 86, [22, 22, 0, 0]);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.font = `bold 24px ${fontStack}`;
      ctx.fillText("HAVANA — PHIẾU ĐƠN BLACK FRIDAY", 52, 65);
      ctx.fillStyle = "#bbf7d0";
      ctx.font = `600 14px ${fontStack}`;
      ctx.fillText("Thời gian xuất bill: " + nowStr + " • Giá gốc tại kho", 52, 92);

      let y = 142;
      ctx.fillStyle = "#11302e";
      ctx.font = `bold 15px ${fontStack}`;
      ctx.fillText("Khách hàng: " + custName + (custPhone ? "   |   SĐT: " + custPhone : ""), 52, y);
      if (custNote) {
        y += 26;
        ctx.fillStyle = "#52706e";
        ctx.font = `500 14px ${fontStack}`;
        ctx.fillText("Ghi chú / Nhận hàng: " + custNote.slice(0, 68), 52, y);
      }

      y += 22;
      ctx.strokeStyle = "#cdebe8";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(52, y);
      ctx.lineTo(W - 52, y);
      ctx.stroke();

      y += 30;
      cart.forEach((item, i) => {
        const q = Number(item.qty || 1);
        const lineSum = Number(item.finalPrice || 0) * q;
        ctx.fillStyle = "#11302e";
        ctx.font = `bold 15px ${fontStack}`;
        const shortName = (`${i + 1}. [${item.sku}] ` + item.name).slice(0, 48);
        ctx.fillText(shortName, 52, y);

        ctx.fillStyle = "#52706e";
        ctx.font = `600 13.5px ${fontStack}`;
        ctx.fillText(`Size: ${item.size}   •   SL: ${q} x ${formatVND(item.finalPrice)}`, 52, y + 22);

        ctx.fillStyle = "#be123c";
        ctx.font = `bold 16px ${fontStack}`;
        const priceTxt = formatVND(lineSum);
        ctx.fillText(priceTxt, W - 52 - ctx.measureText(priceTxt).width, y + 10);

        y += rowH;
      });

      ctx.strokeStyle = "#91d2ce";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(52, y - 10);
      ctx.lineTo(W - 52, y - 10);
      ctx.stroke();

      y += 18;
      ctx.fillStyle = "#52706e";
      ctx.font = `600 14.5px ${fontStack}`;
      ctx.fillText(`Tổng SL: ${totalQty} sp   |   Giá gốc: ${formatVND(totalOrig)}   |   Tiết kiệm: -${formatVND(totalSaved)}`, 52, y);

      y += 36;
      ctx.fillStyle = "#165954";
      ctx.font = `bold 20px ${fontStack}`;
      ctx.fillText("TỔNG THANH TOÁN:", 52, y);

      ctx.fillStyle = "#be123c";
      ctx.font = `bold 24px ${fontStack}`;
      const totStr = formatVND(totalSale);
      ctx.fillText(totStr, W - 52 - ctx.measureText(totStr).width, y);

      // Khung luu y ton kho hang Sale o day phieu
      const noteBoxY = y + 18;
      ctx.fillStyle = "#fffbeb";
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 1.2;
      v29RoundRectPath(ctx, 52, noteBoxY, W - 104, noteBoxH, 11);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = "#92400e";
      ctx.font = noteFont;
      noteLines.forEach((ln, idx) => {
        ctx.fillText(ln, 66, noteBoxY + 22 + idx * noteLineH);
      });

      const link = document.createElement("a");
      link.download = "Bill_Havana_BlackFriday_" + Date.now().toString().slice(-5) + ".png";
      link.href = canvas.toDataURL("image/png");
      link.click();
      showToast("🖼️ Đã tải ảnh Phiếu Bill nét cao về máy!");
    };

    // 2. GIU NGUYEN HE THONG DONG BO V28 (LOC KEP TON >= 3, ANH SAPO CV538/SAT532, CANH BAO COOKIE)
    const v29ShouldForceSapoMedia = (code) => {
      if (!FORCE_SAPO_MEDIA_V29.has(code)) return false;
      if (typeof sessionFreshUploadedSkus !== "undefined" && sessionFreshUploadedSkus && sessionFreshUploadedSkus.has(code)) {
        return false;
      }
      return true;
    };

    const v29CleanForcedSapoItem = (p) => {
      if (!p) return;
      const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
      if (!v29ShouldForceSapoMedia(code)) return;

      if (typeof repoLiveMediaBySku === "object" && repoLiveMediaBySku && repoLiveMediaBySku[code]) {
        delete repoLiveMediaBySku[code];
      }

      const s533Set = (code === "SAT532" && typeof getSat533ImageSet === "function") ? getSat533ImageSet() : new Set();
      const sapoUrls = [];
      const candidates = [
        ...(Array.isArray(p.sapoImages) ? p.sapoImages : []),
        ...(Array.isArray(p.images) ? p.images : []),
        p.image
      ];
      candidates.forEach((u) => {
        const s = String(u || "").trim();
        if (!s || !isSapoImageUrl(s)) return;
        if (code === "SAT532" && (s533Set.has(s) || s.toLowerCase().includes("sat533"))) return;
        if (!sapoUrls.includes(s)) sapoUrls.push(s);
      });

      p.sapoImages = sapoUrls;
      p.hasCustomMedia = false;
      p.video = "";
      p.sizeChart = "";
      p.images = [...sapoUrls];
      p.image = sapoUrls[0] || "";
    };

    const _preV29StripSapo = stripSapoImagesFromProducts;
    stripSapoImagesFromProducts = function(list) {
      _preV29StripSapo(list);
      if (Array.isArray(list)) {
        list.forEach((p) => v29CleanForcedSapoItem(p));
      }
    };

    const _preV29GetProductSlides = getProductSlides;
    getProductSlides = function(p) {
      const code = normalizeBaseCode(String(p?.sku || "").split("-")[0]);
      if (v29ShouldForceSapoMedia(code)) {
        v29CleanForcedSapoItem(p);
        if (Array.isArray(p.sapoImages) && p.sapoImages.length > 0) {
          return p.sapoImages.map((src) => ({ type: "image", src }));
        }
        return [{ type: "image", src: svgPlaceholder(p.sku, p.name, "#d8f1ef", "#eef9f8") }];
      }
      return _preV29GetProductSlides(p);
    };

    const v29ApplyPriceFixes = (rulesMap) => {
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

    const v29FilterVariants = (variantsArr) => {
      if (!Array.isArray(variantsArr)) return [];
      const order = [];
      const map = {};
      variantsArr.forEach((v) => {
        if (!v) return;
        const sz = String(v.size || "Freesize").trim() || "Freesize";
        if (!map[sz]) {
          order.push(sz);
          map[sz] = {};
        }
        const st = (v.stocks && typeof v.stocks === "object") ? v.stocks : {};
        Object.keys(st).forEach((wid) => {
          map[sz][wid] = (map[sz][wid] || 0) + Math.max(0, Number(st[wid]) || 0);
        });
      });
      const valid = [];
      order.forEach((sz) => {
        const st = map[sz];
        const vTot = Object.values(st).reduce((a, b) => a + b, 0);
        if (vTot >= MIN_VARIANT_STOCK_V29) {
          valid.push({ size: sz, stocks: st });
        }
      });
      return valid;
    };

    const v29CalcTotalStock = (p) => {
      if (!p || !Array.isArray(p.variants)) return 0;
      return p.variants.reduce((sum, v) => {
        const st = (v && v.stocks && typeof v.stocks === "object") ? v.stocks : {};
        return sum + Object.values(st).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0);
      }, 0);
    };

    const v29SanitizeProductList = (list) => {
      if (!Array.isArray(list)) return [];
      const out = [];
      list.forEach((p) => {
        if (!p) return;
        const code = normalizeBaseCode(String(p.sku || "").split("-")[0]);
        if (!code) return;
        const rule = SALE_RULES_MASTER && SALE_RULES_MASTER[code];
        if (rule) {
          if (Number(rule.origPrice) > 0) p.origPrice = Number(rule.origPrice);
          if (Number(rule.salePrice) > 0) p.salePrice = Number(rule.salePrice);
          p.discountPct = getProductDiscountPct(p);
        }
        v29CleanForcedSapoItem(p);
        p.variants = v29FilterVariants(p.variants);
        if (p.variants.length > 0 && v29CalcTotalStock(p) >= MIN_TOTAL_STOCK_V29) {
          out.push(p);
        }
      });
      return out;
    };

    ensureDefaultSelectedSizes = function() {
      if (!Array.isArray(PRODUCTS)) return;
      PRODUCTS.forEach((p) => {
        if (!Array.isArray(p.variants) || !p.variants.length) {
          delete selectedVariantBySku[p.sku];
          return;
        }
        const curSz = selectedVariantBySku[p.sku];
        const curVar = curSz ? p.variants.find((v) => v.size === curSz) : null;
        if (!curVar || getVariantTotal(curVar) < MIN_VARIANT_STOCK_V29) {
          const firstValid = p.variants.find((v) => getVariantTotal(v) >= MIN_VARIANT_STOCK_V29) || p.variants[0];
          if (firstValid) selectedVariantBySku[p.sku] = firstValid.size;
        }
      });
    };

    const v29SyncStockBadgeLabel = () => {
      const stTxt = document.getElementById("onlyInStockText");
      if (stTxt) {
        stTxt.textContent = currentLang === "VI" ? "Tồn ≥ 3" : "Stock ≥ 3";
      }
    };

    const v29EnforceMinStock = (notifyCartChange = false) => {
      v29ApplyPriceFixes(SALE_RULES_MASTER);
      if (Array.isArray(BASELINE_PRODUCTS)) {
        BASELINE_PRODUCTS = v29SanitizeProductList(BASELINE_PRODUCTS);
      }
      if (Array.isArray(PRODUCTS)) {
        PRODUCTS = v29SanitizeProductList(PRODUCTS);
      }
      ensureDefaultSelectedSizes();

      if (Array.isArray(cart) && cart.length) {
        let adjusted = false;
        cart = cart.filter((c) => {
          const matchedP = (PRODUCTS || []).find((x) => x.sku === c.sku);
          if (!matchedP || v29CalcTotalStock(matchedP) < MIN_TOTAL_STOCK_V29) {
            adjusted = true;
            return false;
          }
          c.origPrice = matchedP.origPrice;
          c.finalPrice = getProductSalePrice(matchedP);
          c.discountPct = getProductDiscountPct(matchedP);
          if (v29ShouldForceSapoMedia(c.sku)) {
            c.image = matchedP.image || "";
          }
          const maxAvail = getMaxStockForSkuSize(c.sku, c.size);
          if (maxAvail < MIN_VARIANT_STOCK_V29) {
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
            showToast("Tồn kho Sapo vừa thay đổi — các mẫu/size tồn dưới 3 đã tự động rút khỏi danh sách & Phiếu Bill!");
          }
        }
      }
      v29SyncStockBadgeLabel();
    };

    const _preV29MergeRules = mergeAndApplySaleRules;
    mergeAndApplySaleRules = function(liveList, rulesObj) {
      v29ApplyPriceFixes(SALE_RULES_MASTER);
      v29ApplyPriceFixes(rulesObj);
      const cleanedInput = v29SanitizeProductList(Array.isArray(liveList) ? liveList : []);
      const baseRules = (rulesObj && Object.keys(rulesObj).length > 0) ? rulesObj : SALE_RULES_MASTER;
      const scopedRules = {};
      if (baseRules) {
        const liveSet = new Set(cleanedInput.map((it) => normalizeBaseCode(String(it?.sku || "").split("-")[0])).filter(Boolean));
        Object.keys(baseRules).forEach((k) => {
          if (liveSet.has(k)) scopedRules[k] = baseRules[k];
        });
      }
      const merged = _preV29MergeRules(cleanedInput, scopedRules);
      return v29SanitizeProductList(merged);
    };

    const _preV29RenderCatalog = renderCatalog;
    renderCatalog = function() {
      v29EnforceMinStock(false);
      _preV29RenderCatalog();
    };

    const _preV29SetLang = setLang;
    setLang = function(lang) {
      _preV29SetLang(lang);
      v29SyncStockBadgeLabel();
      if (document.getElementById("billModal")?.classList.contains("open")) {
        renderBillModal();
      }
    };

    if (typeof exportCleanIndexHtml === "function") {
      const _preV29ExportCleanHtml = exportCleanIndexHtml;
      exportCleanIndexHtml = function() {
        v29EnforceMinStock(false);
        _preV29ExportCleanHtml();
      };
    }

    const v29ParseStampMins = (stampStr) => {
      const m = String(stampStr || "").trim().match(/^(\d{1,2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})/);
      if (!m) return 0;
      return (((Number(m[4]) * 32 + Number(m[3])) * 24 + Number(m[1])) * 60) + Number(m[2]);
    };

    const v29FetchLiveRelease = async (forceNow = false) => {
      const cacheKey = "havana_live_rel_v29";
      const nowMs = Date.now();
      if (!forceNow && (nowMs - v29LastReleaseCheckMs < 45000)) {
        try {
          const cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null");
          if (cached && (nowMs - cached.ts < 45000)) return cached.feed;
        } catch (_) {}
      }
      v29LastReleaseCheckMs = nowMs;
      try {
        const repoSlug = typeof GITHUB_REPO_SLUG !== "undefined" ? GITHUB_REPO_SLUG : "moonchuyensi-cmd/havana-sale";
        const res = await fetch(`https://api.github.com/repos/${repoSlug}/releases/tags/live-stock?t=${nowMs}`, {
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

    const v29ApplyReleaseFeed = (basePayload, feed) => {
      if (!feed || !feed.items || !Array.isArray(feed.warehouses) || !basePayload || !Array.isArray(basePayload.products)) {
        return basePayload;
      }
      const feedMins = v29ParseStampMins(feed.synced_at);
      const baseMins = v29ParseStampMins(basePayload.synced_at);
      const feedEpoch = Number(feed.synced_epoch || 0);
      const baseEpoch = Number(basePayload.synced_epoch || 0);

      const feedIsOlder = (feedEpoch > 0 && baseEpoch > 0)
        ? (feedEpoch < baseEpoch)
        : (baseMins > 0 && feedMins < baseMins);

      if (feedIsOlder) {
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
          const rawVars = fEntry.v.map(([sz, qArr]) => {
            const st = {};
            whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
            return { size: sz || "Freesize", stocks: st };
          });
          item.variants = v29FilterVariants(rawVars);
          if (fEntry.n) item.name = fEntry.n;
          if (Array.isArray(fEntry.si) && fEntry.si.length) {
            item.sapoImages = fEntry.si;
          }
        } else if (feed.full) {
          item.variants = [];
        }
      });

      if (SALE_RULES_MASTER) {
        Object.keys(feed.items).forEach((code) => {
          if (seen.has(code) || !SALE_RULES_MASTER[code]) return;
          const fEntry = feed.items[code];
          if (!fEntry || !Array.isArray(fEntry.v)) return;
          const rawVars = fEntry.v.map(([sz, qArr]) => {
            const st = {};
            whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
            return { size: sz || "Freesize", stocks: st };
          });
          const cleanVars = v29FilterVariants(rawVars);
          if (cleanVars.length > 0) {
            basePayload.products.push({
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
              variants: cleanVars
            });
          }
        });
      }

      basePayload.products = v29SanitizeProductList(basePayload.products);
      basePayload.synced_at = feed.synced_at || basePayload.synced_at;
      basePayload.synced_epoch = feedEpoch || baseEpoch || 0;
      if (typeof feed.cookie_expired === "boolean") {
        basePayload.sapo_cookie_expired = feed.cookie_expired;
      }
      basePayload.sync_source = "github_release_live_feed";
      return basePayload;
    };

    const v29UpdateHeaderSyncStatus = (syncedAt, isExpired) => {
      const badgeEl = document.getElementById("liveSyncBadge");
      const dotEl = badgeEl ? badgeEl.querySelector(".live-sync-dot") : null;
      const timeEl = document.getElementById("liveSyncTimeText");
      if (!timeEl) return;

      if (isExpired) {
        timeEl.textContent = currentLang === "VI"
          ? `⚠️ Cookie Sapo hết hạn (${syncedAt || "Cũ"})`
          : `⚠️ Sapo Cookie Expired (${syncedAt || "Stale"})`;
        if (badgeEl) {
          badgeEl.style.background = "var(--warn-bg)";
          badgeEl.style.color = "var(--warn-amber)";
        }
        if (dotEl) dotEl.style.background = "var(--warn-amber)";
      } else {
        timeEl.textContent = (currentLang === "VI" ? "Cập nhật: " : "Synced: ") + (syncedAt || "Live");
        if (badgeEl) {
          badgeEl.style.background = "";
          badgeEl.style.color = "";
        }
        if (dotEl) dotEl.style.background = "";
      }
    };

    const v29SilentUpdateDOM = () => {
      if (typeof purgeExcludedSkusEverywhere === "function") purgeExcludedSkusEverywhere();
      v29EnforceMinStock(false);
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
        if (!Array.isArray(p.variants) || !p.variants.length || v29CalcTotalStock(p) < MIN_TOTAL_STOCK_V29) return false;
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
        if (onlyInStock && getProductTotal(p) < MIN_TOTAL_STOCK_V29) return false;
        return true;
      });

      const badgeCnt = document.getElementById("countResultBadge");
      if (badgeCnt) {
        badgeCnt.textContent = currentLang === "VI" ? `${filteredProductsCache.length} mẫu` : `${filteredProductsCache.length} items`;
      }

      if (!filteredProductsCache.length) {
        _preV29RenderCatalog();
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
    window.silentUpdateCatalogInPlace = v29SilentUpdateDOM;

    refreshLiveSapoStock = async function(silent = true) {
      if (v29SyncInFlight) return false;
      v29SyncInFlight = true;
      const timeEl = document.getElementById("liveSyncTimeText");
      if (!silent && timeEl) {
        timeEl.textContent = currentLang === "VI" ? "Đang kiểm tra Sapo..." : "Checking Sapo...";
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
          const liveFeed = await v29FetchLiveRelease(!silent);
          if (liveFeed) data = v29ApplyReleaseFeed(data, liveFeed);
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
                v29ApplyPriceFixes(remoteRules);
                SALE_RULES_MASTER = remoteRules;
              }
            }
          } catch (_) {}

          const cleanLiveProds = v29SanitizeProductList(data.products);
          stripSapoImagesFromProducts(cleanLiveProds);
          PRODUCTS = mergeAndApplySaleRules(cleanLiveProds, SALE_RULES_MASTER);
          v29EnforceMinStock(true);

          const isExpired = Boolean(data.sapo_cookie_expired);
          v29UpdateHeaderSyncStatus(data.synced_at, isExpired);

          if (!silent) {
            if (isExpired) {
              showToast(`⚠️ SAPO_COOKIE trên GitHub đã hết hạn (Tồn đang dừng ở ${data.synced_at || "mốc cũ"}) — Hãy dán lại Cookie mới!`);
            } else if (data.synced_at) {
              showToast(`⚡ Đã đồng bộ Sapo mới nhất (${data.synced_at}) • Hiện ${PRODUCTS.length} mẫu (mọi size đều ≥ 3)`);
            }
          }
          lastSyncedStamp = data.synced_at || "";

          v29SilentUpdateDOM();
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
        v29SyncInFlight = false;
      }
      return false;
    };

    v29EnforceMinStock(false);
    v29SilentUpdateDOM();
    updateCartBar();
    if (document.getElementById("billModal")?.classList.contains("open")) {
      renderBillModal();
    }
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


def filter_variants_min_stock(variants_list: list, min_stock: int = MIN_VARIANT_STOCK) -> list:
    merged_order = []
    merged_stocks = {}
    for v in (variants_list or []):
        if not isinstance(v, dict):
            continue
        sz = norm_nfc(v.get("size") or "Freesize") or "Freesize"
        st = v.get("stocks") or {}
        if sz not in merged_stocks:
            merged_order.append(sz)
            merged_stocks[sz] = {}
        if isinstance(st, dict):
            for wid, qty in st.items():
                try:
                    q_val = max(0, int(qty or 0))
                except (ValueError, TypeError):
                    q_val = 0
                merged_stocks[sz][wid] = merged_stocks[sz].get(wid, 0) + q_val

    valid_variants = []
    for sz in merged_order:
        st_map = merged_stocks[sz]
        if sum(st_map.values()) >= min_stock:
            valid_variants.append({"size": sz, "stocks": st_map})
    return valid_variants


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


def upgrade_repo_files_v29() -> bool:
    changed = False

    os.makedirs(os.path.join("functions", "api"), exist_ok=True)
    stock_js_path = os.path.join("functions", "api", "stock.js")
    old_js = ""
    if os.path.exists(stock_js_path):
        with open(stock_js_path, "r", encoding="utf-8") as f:
            old_js = f.read()
    if old_js.strip() != CF_STOCK_JS_V29.strip():
        with open(stock_js_path, "w", encoding="utf-8") as f:
            f.write(CF_STOCK_JS_V29)
        print("[+] Da cap nhat functions/api/stock.js -> v29")
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
                    for code, (orig_p, sale_p, disc_p, _) in PRICE_PATCH_V29.items():
                        if code in sr_data:
                            sr_data[code]["origPrice"] = orig_p
                            sr_data[code]["salePrice"] = sale_p
                            sr_data[code]["discountPct"] = disc_p
                    filtered_bp = []
                    for item in bp_data:
                        c = clean_base_code(str(item.get("sku") or "").split("-")[0])
                        if not c or c in EXCLUDED_SKUS:
                            continue
                        if c in PRICE_PATCH_V29:
                            item["origPrice"] = PRICE_PATCH_V29[c][0]
                            item["salePrice"] = PRICE_PATCH_V29[c][1]
                            item["discountPct"] = PRICE_PATCH_V29[c][2]
                        if c in FORCE_RESET_TO_SAPO_SKUS:
                            _, s_imgs, _, _ = split_custom_and_sapo_media(item)
                            item["sapoImages"] = s_imgs
                            item["images"] = s_imgs
                            item["image"] = s_imgs[0] if s_imgs else ""
                            item["video"] = ""
                            item["sizeChart"] = ""
                            item["hasCustomMedia"] = False
                        item["variants"] = filter_variants_min_stock(item.get("variants"), MIN_VARIANT_STOCK)
                        if len(item["variants"]) > 0 and calc_product_total_stock(item) >= MIN_TOTAL_STOCK:
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
                for m in ("/* === v22:", "/* === v23:", "/* === v25:", "/* === v26:", "/* === v27:", "/* === v28:", "/* === v29:")
            ]
            valid_cuts = [pos for pos in cuts if pos != -1]
            cut_at = min(valid_cuts) if valid_cuts else end_logic
            html = (
                html[:cut_at].rstrip()
                + "\n\n    "
                + V29_INDEX_JS_ADDON.strip()
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
            print("[+] Da nang cap index.html -> v29 (Cap nhat ghi chu Bill tren Popup, Zalo Text & Anh PNG)")
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
        return set(), []

    compiled = build_sku_matcher(list(pending))
    removed_files = []
    touched_dirs = set()

    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel_root = root.replace("\\", "/").lstrip("./")
        for fname in files:
            low = fname.lower()
            if low == "keep.txt" or not (low.endswith(IMG_EXTS) or low.endswith(VID_EXTS)):
                continue
            fpath = os.path.join(root, fname)
            for _, rx in compiled:
                if rx.search(fname) or (rel_root and rx.search(rel_root)):
                    try:
                        os.remove(fpath)
                        removed_files.append(fpath.replace("\\", "/"))
                        if rel_root:
                            touched_dirs.add(root)
                    except Exception:
                        pass
                    break

    for dpath in sorted(touched_dirs, key=len, reverse=True):
        rel_d = dpath.replace("\\", "/").lstrip("./")
        top_seg = rel_d.split("/")[0] if rel_d else "."
        if top_seg in PROTECTED_ROOT_DIRS or not os.path.isdir(dpath):
            continue
        try:
            rem = [x for x in os.listdir(dpath) if x not in (".DS_Store", "Thumbs.db")]
            if not rem:
                shutil.rmtree(dpath, ignore_errors=True)
        except Exception:
            pass

    try:
        with open(PURGE_STATE_FILE, "w", encoding="utf-8") as pf:
            json.dump(sorted(already_purged | pending), pf, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return pending, removed_files


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


def fetch_existing_live_release_meta() -> tuple:
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "moonchuyensi-cmd/havana-sale").strip()
    if not repo_slug:
        return "", 0
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "havana-sale-auto-sync-v29"}
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo_slug}/releases/tags/live-stock", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            rel = json.loads(resp.read().decode("utf-8"))
            if rel and rel.get("body"):
                feed = json.loads(rel["body"])
                return str(feed.get("synced_at") or ""), int(feed.get("synced_epoch") or 0)
    except Exception:
        pass
    return "", 0


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
                    if c_val in PRICE_PATCH_V29:
                        _, target_sale, _, target_pct_dec = PRICE_PATCH_V29[c_val]
                        cur_sale = parse_money(ws_e.cell(r_idx, 3).value)
                        if cur_sale in (99000, 159000) and cur_sale != target_sale:
                            ws_e.cell(r_idx, 3).value = target_sale
                            ws_e.cell(r_idx, 4).value = target_pct_dec
                            xlsx_patched = True
            if xlsx_patched:
                wb_edit.save(target_xlsx)
        except Exception:
            pass

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
    for code, (orig_p, sale_p, disc_p, _) in PRICE_PATCH_V29.items():
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
        except Exception:
            pass

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


def write_outputs(
    warehouses_sorted: list,
    products_out: list,
    synced_at_str: str,
    sale_rules: dict = None,
    synced_epoch: int = 0,
    sapo_cookie_expired: bool = False
):
    filtered_out = []
    for p in products_out:
        clean_vars = filter_variants_min_stock(p.get("variants"), MIN_VARIANT_STOCK)
        if len(clean_vars) > 0:
            p_copy = dict(p)
            p_copy["variants"] = clean_vars
            if calc_product_total_stock(p_copy) >= MIN_TOTAL_STOCK:
                filtered_out.append(p_copy)

    payload = {
        "min_stock_rule": MIN_TOTAL_STOCK,
        "min_variant_stock_rule": MIN_VARIANT_STOCK
