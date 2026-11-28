// Cloudflare Pages Function: /api/stock (v41 ONLY ATH T-Shirts Online)
const MIN_TOTAL_STOCK = 3;
const MIN_VARIANT_STOCK = 3;
const EXCLUDED_SKUS = new Set(["LEN374", "QJ358"]);
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
      headers: { "Accept": "application/vnd.github+json", "User-Agent": "havana-cf-v41" }
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
              const r = saleRules[c];
              if (!seen.has(c) && r && (r.section === "ATH" || c.startsWith("ATH")) && !EXCLUDED_SKUS.has(c) && Array.isArray(fe.v) && fe.v.length) {
                const sp = Number(r.salePrice || r.origPrice || 159000);
                const op = Math.max(Number(r.origPrice || sp), sp);
                const dp = op > sp ? Math.max(1, Math.round((1 - sp / op) * 100)) : 0;
                const built = fe.v.map(([sz, qa]) => {
                  const st = {};
                  whIds.forEach((w, i) => { st[w] = Number((qa && qa[i]) || 0); });
                  return { size: sz || "Freesize", stocks: st };
                });
                const itemObj = {
                  sku: c, name: fe.n || ("Áo thun Havana " + c), subType: "Áo thun",
                  section: "ATH", origPrice: op, salePrice: sp, discountPct: dp,
                  image: "", images: [], sapoImages: fe.si || [], video: "", sizeChart: "", variants: built
                };
                if (r.varPrices) itemObj.varPrices = r.varPrices;
                payload.products.push(itemObj);
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
      if (saleRules && Object.keys(saleRules).length > 0) {
        const r = saleRules[c];
        if (!r || (r.section && r.section !== "ATH" && !c.startsWith("ATH"))) return false;
        p.section = "ATH";
        const sp = Number(r.salePrice || p.salePrice || 0);
        const op = Math.max(Number(r.origPrice || 0), Number(p.origPrice || 0), sp);
        if (sp > 0) p.salePrice = sp;
        if (op > 0) p.origPrice = op;
        if (r.varPrices) p.varPrices = r.varPrices;
        p.discountPct = (p.origPrice > p.salePrice && p.salePrice > 0)
          ? Math.max(1, Math.round((1 - p.salePrice / p.origPrice) * 100))
          : 0;
      } else if (p.section !== "ATH" && !c.startsWith("ATH")) {
        return false;
      }
      p.variants = filterVars(p.variants);
      return p.variants.length > 0 && calcTot(p) >= MIN_TOTAL_STOCK;
    });
  }
  payload.sync_source = "v41_only_ath";
  return new Response(JSON.stringify(payload), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store, max-age=0" }
  });
}
