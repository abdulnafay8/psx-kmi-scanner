// KMI Scanner API (Cloudflare Worker)
//
//   GET  /api/market            live prices of every KMI All Share stock (<= 30 s old)
//   GET  /api/ts/int/<SYMBOL>   today's intraday trades: [unix seconds, price, volume]
//   GET  /api/ts/eod/<SYMBOL>   daily history (~5 years): [unix seconds, close, volume, open]
//   POST /api/chat              {messages:[{role, content}], symbol?} -> {reply}
//
// Every route needs the X-Passcode header to match the APP_PASSCODE secret:
// PSX data may not be republished publicly without a PSX licence, so this is
// for the owner's personal use only.

const PSX = "https://dps.psx.com.pk";
const UA = "Mozilla/5.0 (compatible; KMI-Scanner)";
const SYMBOL_RE = /^[A-Z0-9]{1,12}$/;

// ---- small in-memory cache (per Worker instance) --------------------------
const memo = new Map();
async function cached(key, ttlMs, make) {
  const hit = memo.get(key);
  if (hit && Date.now() - hit.at < ttlMs) return hit.value;
  const value = await make();
  memo.set(key, { value, at: Date.now() });
  return value;
}

// ---- PSX data portal access (same token flow as the psxdata library) -------
let token = { value: null, at: 0 };
async function psxToken() {
  if (token.value && Date.now() - token.at < 200_000) return token.value;
  const html = await (await fetch(PSX + "/", { headers: { "User-Agent": UA } })).text();
  const m = html.match(/window\.__ps\s*=\s*(\{[\s\S]*?\})\s*(?:;|<\/script>)/);
  const k = m ? JSON.parse(m[1])._k : null;
  if (!k || !/^[A-Za-z0-9_-]{16,}$/.test(k)) throw new Error("PSX request token not found");
  token = { value: k, at: Date.now() };
  return k;
}

async function psx(path, init = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch(PSX + path, {
      ...init,
      headers: { "User-Agent": UA, "X-Requested-With": "XMLHttpRequest", "X-Req-Id": await psxToken(), ...(init.headers || {}) },
    });
    if (r.status === 403 && attempt === 0) { token.value = null; continue; }   // token rotated
    if (!r.ok) throw new Error(`PSX ${path} returned ${r.status}`);
    return r;
  }
  throw new Error(`PSX ${path} refused the request token`);
}

const num = (s) => { const v = parseFloat(String(s).replace(/,/g, "")); return Number.isFinite(v) ? v : null; };
const strip = (s) => s.replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").trim();

function parseMarket(html) {
  const out = [];
  for (const row of html.split("<tr>").slice(1)) {
    const cells = row.match(/<td[^>]*>[\s\S]*?<\/td>/g);
    if (!cells || cells.length < 11) continue;
    const listed = strip(cells[2]);
    if (!listed.split(",").includes("KMIALLSHR")) continue;       // halal allow-list
    const order = (c) => { const m = c.match(/data-order="([^"]*)"/); return num(m ? m[1] : strip(c)); };
    const sym = (cells[0].match(/data-search="([^"]+)"/) || [])[1];
    if (!sym) continue;
    out.push({
      s: sym, name: strip((cells[0].match(/data-title="([^"]*)"/) || [])[1] || ""),
      ldcp: order(cells[3]), open: order(cells[4]), high: order(cells[5]), low: order(cells[6]),
      cur: order(cells[7]), chg: order(cells[8]), pct: order(cells[9]), vol: order(cells[10]),
    });
  }
  return out;
}

const market = () => cached("market", 30_000, async () => ({
  at: new Date().toISOString(),
  stocks: parseMarket(await (await psx("/market-watch")).text()),
}));

async function timeseries(type, sym) {
  const ttl = type === "int" ? 30_000 : 3_600_000;
  return cached(`ts:${type}:${sym}`, ttl, async () => {
    const j = await (await psx(`/timeseries/${type}/${sym}`)).json();
    const data = (Array.isArray(j) ? j : j.data || []).slice().reverse();   // oldest first
    return { at: new Date().toISOString(), symbol: sym, data };
  });
}

// ---- AI chat (free Workers AI model, grounded in the app's own data) -------
const SYSTEM = `You are the assistant inside KMI Scanner, a halal stock scanner for the Pakistan Stock Exchange (PSX). You help one user understand PSX and the stocks on the KMI All Share Index (the Shariah-screened list).

Rules you always follow:
1. Facts come only from the DATA block below or from well-known general facts about how PSX works. Never invent prices, dates, dividends, news, targets or results. If the DATA doesn't contain something, say you don't have it.
2. Only stocks in the DATA's KMI list are covered. If asked about another stock, say it isn't on the KMI All Share list, so the app doesn't cover it.
3. Buying: the app's only tested buy signals are its "Buy plans" (setups that passed years of honest backtesting after costs). If a stock has a Buy plan, explain the plan (order, stop, target, hold, size from risk), its backtest record, and that single trades win only about half the time. If it has no Buy plan, say plainly there is no tested signal today and the app sees no edge either way. Never predict that a price will rise or fall, and never present Running, Fading or the lights as buy or sell signals: they are context only.
4. Selling: for a plan the user is in, the exits are the plan's stop, target and maximum hold. For any other holding, describe what the DATA shows (trend vs averages, status) and say the decision is theirs.
5. Seasonal and dividend notes are weak patterns from history, not signals.
6. Be short and plain: money in PKR, no jargon. End any buy or sell guidance with "Not investment advice."

PSX facts: trading hours Mon-Thu 09:32-15:30, Fri 09:17-12:00 and 14:32-16:30 (Pakistan time); settlement T+1 since 9 Feb 2026; daily price limit about +/-10% (or Rs 1); prices in the app's history are not adjusted for bonus shares or splits.`;

const signals = (env) => cached("signals", 300_000, async () =>
  (await fetch(env.SIGNALS_URL + "?t=" + Date.now())).json());

function stockFacts(s, live) {
  return {
    symbol: s.symbol, name: s.name, sector: s.sector, status: s.status_text,
    close: s.close, close_date: s.date, live: live || null,
    plans: (s.plans || []).map((p) => ({
      setup: p.setup_label, order: p.order, valid: "next session only", stop_pct: p.stop_pct,
      target_pct: p.target_pct, max_hold_days: p.max_hold_days, shares: p.shares_est,
      backtest_2024_26: p.backtest, confidence: p.confidence && p.confidence.level,
    })),
    active_plan: s.active_plan ? { alerted: s.active_plan.signal_date, state: s.active_plan.result.state } : null,
    checks: Object.fromEntries(Object.entries(s.reasons || {}).map(([k, rows]) => [k, rows.map((r) => `${r.ok ? "PASS" : "FAIL"} ${r.check} (${r.rule})`)])),
    lights: (s.lights || []).map((l) => `${l.on ? "green" : "red"}: ${l.name}, ${l.text}`),
    levels: s.levels, notes: s.notes || [],
  };
}

async function chat(body, env) {
  const sig = await signals(env);
  const known = new Map(sig.stocks.map((s) => [s.symbol, s]));
  const msgs = (Array.isArray(body.messages) ? body.messages : []).slice(-8)
    .map((m) => ({ role: m.role === "assistant" ? "assistant" : "user", content: String(m.content || "").slice(0, 2000) }))
    .filter((m) => m.content);
  if (!msgs.length || msgs[msgs.length - 1].role !== "user") throw new Error("send a question");
  const question = msgs[msgs.length - 1].content;

  // stocks the question is about: by symbol, or by company name
  const picked = new Set();
  if (body.symbol && known.has(String(body.symbol).toUpperCase())) picked.add(String(body.symbol).toUpperCase());
  for (const w of question.toUpperCase().match(/\b[A-Z][A-Z0-9]{1,9}\b/g) || []) if (known.has(w)) picked.add(w);
  const q = question.toLowerCase();
  for (const s of sig.stocks) {
    const first = (s.name || "").toLowerCase().split(/\s+/)[0];
    if (picked.size < 4 && first.length >= 4 && q.includes(first)) picked.add(s.symbol);
  }
  let live = {};
  try { live = Object.fromEntries((await market()).stocks.map((m) => [m.s, m])); } catch { /* daily data only */ }

  const data = {
    today: new Date().toISOString().slice(0, 10),
    prices_as_of_close: sig.as_of, plans_valid_for: sig.plans_for_session, kmi_list_date: sig.kmi_list_date,
    tested_setups: Object.fromEntries(Object.entries(sig.tested_setups || {}).map(([k, v]) => [v.label, {
      settings: v.params, backtest_2019_23: v.train, backtest_2024_26: v.test, by_year: v.by_year }])),
    live_confidence: sig.confidence,
    todays_buy_plans: sig.stocks.filter((s) => s.status === "buy_plan")
      .map((s) => ({ symbol: s.symbol, setup: s.plans[0] && s.plans[0].setup_label, order: s.plans[0] && s.plans[0].order })),
    stocks_asked_about: [...picked].slice(0, 4).map((sym) => stockFacts(known.get(sym), live[sym])),
    kmi_list: [...known.keys()].join(" "),
  };
  const res = await env.AI.run(env.CHAT_MODEL, {
    messages: [{ role: "system", content: SYSTEM + "\n\nDATA:\n" + JSON.stringify(data) }, ...msgs],
    max_tokens: 700,
    temperature: 0.3,
  });
  return { reply: (res && res.response) || "Sorry, no answer came back. Try again.", stocks: [...picked] };
}

// ---- HTTP plumbing ---------------------------------------------------------
function sameText(a, b) {                       // constant-time passcode check
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const ok = env.ALLOWED_ORIGINS.split(",").includes(origin);
  return ok ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Content-Type, X-Passcode",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Vary": "Origin" } : {};
}

const json = (data, status, headers) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
});

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404, cors);
    if (!env.APP_PASSCODE || !sameText(request.headers.get("X-Passcode") || "", env.APP_PASSCODE))
      return json({ error: "wrong or missing passcode" }, 401, cors);
    try {
      if (url.pathname === "/api/market" && request.method === "GET") return json(await market(), 200, cors);
      const ts = url.pathname.match(/^\/api\/ts\/(int|eod)\/([A-Z0-9]+)$/);
      if (ts && request.method === "GET") {
        if (!SYMBOL_RE.test(ts[2])) return json({ error: "bad symbol" }, 400, cors);
        const kmi = (await market()).stocks.some((m) => m.s === ts[2]);
        if (!kmi) return json({ error: "not on the KMI All Share list" }, 404, cors);
        return json(await timeseries(ts[1], ts[2]), 200, cors);
      }
      if (url.pathname === "/api/chat" && request.method === "POST") {
        const body = await request.json().catch(() => ({}));
        return json(await chat(body, env), 200, cors);
      }
      return json({ error: "not found" }, 404, cors);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502, cors);
    }
  },
};
