// Cloudflare Pages Function: /api/stock (v22 Real-Time 45s Parallel Sapo Edge Sync + GitHub Live-Stock Release Bridge)
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
const GITHUB_REPO_SLUG = "moonchuyensi-cmd/havana-sale";
const SAPO_CDN_RX = /(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com|sapocdn\.net|amazonaws\.com|cloudfront\.net)/i;

function isExternalSapoUrl(u) {
  const s = String(u || "").trim();
  if (!s || s.startsWith("./") || s.startsWith("images/") || s.startsWith("videos/") || s.startsWith("sizes/") || s.startsWith("data:") || s.startsWith("blob:")) return false;
  if (s.includes("raw.githubusercontent.com/" + GITHUB_REPO_SLUG)) return false;
  return SAPO_CDN_RX.test(s) || /^https?:\/\//i.test(s);
}

function extractSmartSecret(rawVal) {
  let raw = String(rawVal || "").trim();
  if (!raw) return "";
  const mb = raw.match(/(?:-b|--cookie)\s+(['"])([\s\S]+?)\1/);
  if (mb) {
    raw = mb[2];
  } else {
    const mh = raw.match(/(?:-H|--header)\s+(['"])cookie\s*:\s*([\s\S]+?)\1/i);
    if (mh) {
      raw = mh[2];
    } else {
      const ml = raw.match(/(?:^|[\r\n])cookie\s*:\s*([^\r\n]+)/i);
      if (ml) raw = ml[1];
    }
  }
  if (raw.includes("^") && (raw.includes("sapo-admin") || raw.includes("AspNetCore"))) {
    raw = raw.replace(/\^([\"%&|<>^])/g, "$1");
  }
  let s = raw.trim().replace(/^[\"']+|[\"']+$/g, "").replace(/[\r\n]+/g, " ").trim();
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
  return String(str || "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .trim();
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
  if (prod.image) {
    addU(typeof prod.image === "object" ? (prod.image.full_path || prod.image.src || prod.image.path) : prod.image);
  }
  for (const im of (prod.images || [])) {
    addU(typeof im === "object" ? (im.full_path || im.src || im.path) : im);
  }
  for (const v of (prod.variants || [])) {
    for (const vim of (v.images || [])) {
      addU(typeof vim === "object" ? (vim.full_path || vim.src || vim.path) : vim);
    }
    if (v.image) {
      addU(typeof v.image === "object" ? (v.image.full_path || v.image.src || v.image.path) : v.image);
    }
  }
  return urls;
}

function parseStampToMinutes(stampStr) {
  // Format: "HH:MM DD/MM"
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
  const cacheKey = new Request(new URL("/api/stock-edge-v22", request.url).toString());
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
    }
  } catch (_) {}

  let syncedLive = false;
  let syncSource = "static_json";
  const sapoImgsByBase = {};

  // LUONG 1 (NHANH NHAT - REAL-TIME 45 GIAY): Ket noi thang Sapo tu Cloudflare Edge neu co SAPO_COOKIE
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
          if (liveByBase[code] && liveByBase[code].length) {
            item.variants = liveByBase[code];
            if (nameByBase[code]) item.name = nameByBase[code];
          }
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

  // LUONG 2 (TU DONG 100% KHONG CAN CAU HINH CLOUDFLARE): Doc Live Stock Feed 5 phut tu GitHub Release [live-stock]
  if (!syncedLive && Array.isArray(payload.products)) {
    try {
      const ghRes = await fetch(`https://api.github.com/repos/${GITHUB_REPO_SLUG}/releases/tags/live-stock`, {
        headers: {
          "Accept": "application/vnd.github+json",
          "User-Agent": "havana-sale-cf-edge-v22"
        }
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
              for (const item of payload.products) {
                const code = String(item.sku || "").trim().toUpperCase().split("-")[0];
                const fEntry = feed.items[code];
                if (fEntry && Array.isArray(fEntry.v) && fEntry.v.length) {
                  item.variants = fEntry.v.map(([sz, qArr]) => {
                    const st = {};
                    whIds.forEach((wid, idx) => { st[wid] = Number((qArr && qArr[idx]) || 0); });
                    return { size: sz || "Freesize", stocks: st };
                  });
                  if (fEntry.n) item.name = fEntry.n;
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
    if (saleRules && Object.keys(saleRules).length > 0) {
      payload.products = payload.products.filter((p) => {
        const baseSku = String(p.sku || "").trim().toUpperCase().split("-")[0];
        if (EXCLUDED_SKUS.has(baseSku)) return false;
        const rule = saleRules[baseSku];
        if (!rule) return false;
        if (rule.origPrice > 0) p.origPrice = rule.origPrice;
        if (rule.salePrice > 0) p.salePrice = rule.salePrice;
        if (p.origPrice > p.salePrice && p.salePrice > 0) {
          p.discountPct = Math.max(1, Math.round((1 - p.salePrice / p.origPrice) * 100));
        }
        if (rule.subType && !p.subType) p.subType = rule.subType;
        return true;
      });
    }

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

      if (code === "SAT532") {
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
