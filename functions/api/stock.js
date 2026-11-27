// Cloudflare Pages Function: /api/stock (v38 ATH Priority + Single ATH Price)
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
