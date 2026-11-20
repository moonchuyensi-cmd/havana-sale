// Cloudflare Pages Function: /api/stock (v18 Live 5-Warehouse Sapo Sync + Hard Excluded SKUs)
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
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

export async function onRequestGet(context) {
  const { request, env } = context;
  const domain = (env.SAPO_DOMAIN || "havanago.mysapogo.com").trim();
  const token = (env.SAPO_ACCESS_TOKEN || "").trim();
  const cookie = (env.SAPO_COOKIE || "").trim();

  const cache = caches.default;
  const cacheKey = new Request(new URL("/api/stock-edge-v17", request.url).toString());
  if (token || cookie) {
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
    if (rulesAsset.ok) { saleRules = await rulesAsset.json(); for (const ex of EXCLUDED_SKUS) { if (saleRules) delete saleRules[ex]; } }
  } catch (_) {}

  let syncedLive = false;
  if (token || cookie) {
    const headers = {
      "accept": "application/json",
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
    };
    if (token) {
      headers["X-Sapo-Access-Token"] = token;
    } else {
      headers["cookie"] = cookie;
      headers["referer"] = `https://${domain}/admin/products`;
      headers["x-sapo-client"] = "sapo-frontend-v3";
      headers["x-sapo-serviceid"] = "sapo-frontend-v3";
    }

    try {
      const locMap = {};
      const locRes = await fetch(`https://${domain}/admin/locations.json`, { headers });
      if (locRes.ok) {
        const locData = await locRes.json();
        for (const l of (locData.locations || [])) {
          locMap[String(l.id)] = mapWarehouseId(l.label || l.name || l.location_name, l.id);
        }
      }

      const liveByBase = {};
      const nameByBase = {};
      for (let page = 1; page <= 25; page++) {
        const pRes = await fetch(`https://${domain}/admin/products.json?page=${page}&limit=250`, { headers });
        if (!pRes.ok) break;
        const pData = await pRes.json();
        const batch = pData.products || [];
        if (!batch.length) break;

        for (const prod of batch) {
          const variants = prod.variants || [];
          if (!variants.length) continue;
          let baseCode = "";
          for (const v of variants) {
            const rawSku = String(v.sku || "").trim().toUpperCase().split("-")[0];
            const m = rawSku.match(/^([A-Z]{1,5}\d{1,5})/);
            if (m) {
              if (saleRules && saleRules[m[1]]) { baseCode = m[1]; break; }
              if (!m[1].startsWith("PVN") && !baseCode) baseCode = m[1];
            }
          }
          if (!baseCode || EXCLUDED_SKUS.has(baseCode)) continue;
          if (!nameByBase[baseCode] && prod.name) nameByBase[baseCode] = String(prod.name).trim();
          liveByBase[baseCode] = liveByBase[baseCode] || [];

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
        if (batch.length < 250) break;
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
            if (!seen.has(code) && liveByBase[code]) {
              payload.products.push({
                sku: code,
                name: nameByBase[code] || ("Sản phẩm Havana " + code),
                subType: rule.subType || "",
                origPrice: rule.origPrice || 299000,
                salePrice: rule.salePrice || 159000,
                discountPct: rule.discountPct || 30,
                image: "",
                images: [],
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
        syncedLive = true;
      }
    } catch (_) {}
  }

  const sapoRx = /(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com)/i;
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
    for (const p of payload.products) {
      if (sapoRx.test(String(p.image || ""))) p.image = "";
      if (Array.isArray(p.images)) p.images = p.images.filter((u) => u && !sapoRx.test(String(u)));
    }
  }

  const outResp = new Response(JSON.stringify(payload), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": syncedLive ? "public, s-maxage=60, stale-while-revalidate=60" : "no-store, max-age=0"
    }
  });
  if (syncedLive) context.waitUntil(cache.put(cacheKey, outResp.clone()));
  return outResp;
}
