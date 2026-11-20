export async function onRequestGet(context) {
  const { request, env } = context;
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
    }
  } catch (_) {}

  const sapoRx = /(dktcdn\.net|mysapogo\.com|sapo\.vn|sapogo\.com)/i;
  if (Array.isArray(payload.products)) {
    if (saleRules && Object.keys(saleRules).length > 0) {
      payload.products = payload.products.filter((p) => {
        const baseSku = String(p.sku || "").trim().toUpperCase().split("-")[0];
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

  return new Response(JSON.stringify(payload), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store, max-age=0"
    }
  });
}
