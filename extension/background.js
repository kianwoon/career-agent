/**
 * Career Agent — background service worker (HTTP polling edition).
 *
 * MV3 suspends idle service workers and kills WebSockets — unreliable.
 * Polling is platform-reliable: every HTTP request wakes the worker.
 *
 * Loop: poll /agent/poll → execute any command → POST /agent/result → repeat.
 */

const DEFAULT_API = "https://career-agent-kianwoon-88223cd5.koyeb.app";
const POLL_INTERVAL_MS = 2500; // idle polling
const ACTIVE_POLL_MS = 300; // fast polling while a flow runs

let API_BASE = DEFAULT_API;
let busy = false;
let loopTimer = null;
// The tab the agent opened for its own work — all fill/click/extract target
// THIS tab, never whatever the user happens to have focused.
let agentTabId = null;

async function loadConfig() {
  const stored = await chrome.storage.local.get(["apiBase"]);
  API_BASE = (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#2e9e5b" : "#999" });
}

// --- command execution (unchanged semantics) ------------------------------

async function ensureTab(url) {
  // Always open agent navigation in a NEW background tab — never hijack the
  // user's current tab (it may be the app itself, or anything else).
  // Reuse the existing agent tab if it's still open (one workspace per agent).
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      if (t && t.id !== undefined) {
        await chrome.tabs.update(agentTabId, { url });
        await waitForComplete(agentTabId);
        return agentTabId;
      }
    } catch {
      agentTabId = null; // tab was closed — fall through and create a new one
    }
  }
  const tab = await chrome.tabs.create({ active: false, url });
  agentTabId = tab.id;
  await waitForComplete(tab.id);
  return tab.id;
}

function waitForComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("Page load timed out")), timeoutMs);
    chrome.tabs.onUpdated.addListener(function listener(id, info) {
      if (id === tabId && info.status === "complete") {
        clearTimeout(t);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });
  });
}

// NOTE: there is deliberately no "active tab" concept anywhere in this agent.
// All navigation and script execution happens in the agent-owned tab.

async function execOnTab(fn, args = []) {
  // Always target the agent's own tab. If it doesn't exist yet, create it
  // on the site first — the agent NEVER touches the user's focused tab.
  let tabId;
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      tabId = t && t.id;
    } catch {
      agentTabId = null;
    }
  }
  if (!tabId) tabId = await ensureTab("about:blank");
  const res = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: fn,
    args,
  });
  return res && res[0] && res[0].result;
}

async function cmdNavigate(url) {
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  await ensureTab(url);
  await sleep(2500); // SPA render beat
  return { url };
}

async function cmdFill(selector, text) {
  const r = await execOnTab((sel, txt) => {
    const el = document.querySelector(sel);
    if (!el) return { ok: false, error: "Element not found: " + sel };
    el.focus();
    el.scrollIntoView({ block: "center" });
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, txt);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  }, [selector, String(text ?? "")]);
  if (!r || !r.ok) throw new Error((r && r.error) || "fill failed");
  return r;
}

async function cmdClick(selector) {
  const r = await execOnTab((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { ok: false, error: "Element not found: " + sel };
    el.scrollIntoView({ block: "center" });
    el.click();
    return { ok: true };
  }, [selector]);
  if (!r || !r.ok) throw new Error((r && r.error) || "click failed");
  await sleep(1500);
  return r;
}

async function cmdPress(key) {
  await execOnTab((k) => {
    const el = document.activeElement || document.body;
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
    if (k === "Enter" && el.form) {
      el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    }
    el.dispatchEvent(new KeyboardEvent("keyup", { key: k, bubbles: true }));
  }, [key]);
  await sleep(2000);
  return { ok: true, key };
}

async function cmdExtract(cardSelector, fields, maxItems) {
  return (
    (await execOnTab((card, fieldMap, max) => {
      const cards = document.querySelectorAll(card);
      const out = [];
      for (const c of Array.from(cards).slice(0, max || 30)) {
        const pick = (sel) => {
          if (!sel) return "";
          const el = c.querySelector(sel);
          return el ? (el.textContent || "").trim().slice(0, 300) : "";
        };
        out.push({
          title: pick(fieldMap && fieldMap.title),
          company: pick(fieldMap && fieldMap.company),
          location: pick(fieldMap && fieldMap.location),
          summary: pick(fieldMap && fieldMap.summary),
          // Prefer a child link; fall back to the card itself being one
          // (e.g. <a class="result-card">…</a> — querySelector misses that).
          url: (c.querySelector("a") && c.querySelector("a").href) || c.href || "",
          raw_text: (c.innerText || "").slice(0, 500),
        });
      }
      return out;
    }, [cardSelector, fields || {}, maxItems || 30])) || []
  );
}

async function cmdGetCookies(url) {
  const u = new URL(url);
  const cookies = await chrome.cookies.getAll({ domain: u.hostname });
  return cookies.map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path,
    expires: c.expirationDate || -1,
    httpOnly: c.httpOnly,
    secure: c.secure,
    // Playwright only accepts Strict|Lax|None. Chrome's cookies API returns
    // "unspecified" | "lax" | "strict" | "no_restriction" — normalize all of them.
    sameSite: c.sameSite === "no_restriction" || c.sameSite === "none"
      ? "None"
      : c.sameSite === "strict"
        ? "Strict"
        : "Lax", // covers "lax" and "unspecified"
  }));
}

async function cmdRunFlow(baseUrl, query, steps) {
  const results = [];
  for (const step of steps || []) {
    const action = step.action;
    if (action === "navigate") {
      // URL templates may contain {query} — substitute the search text.
      const url = (step.url || baseUrl).replaceAll("{query}", encodeURIComponent(query || ""));
      await cmdNavigate(url);
    }
    else if (action === "fill") await cmdFill(step.selector, step.param === "query" ? query : step.value || "");
    else if (action === "click") await cmdClick(step.selector);
    else if (action === "press") await cmdPress(step.key || "Enter");
    else if (action === "wait") await sleep((step.seconds || 2) * 1000);
    else if (action === "card" || step.card) {
      // Session-expiry guard: if the results page is actually a login wall,
      // report needs_human so the search PAUSES for re-login instead of
      // silently returning "no results".
      const wall = await execOnTab(() => {
        const hasPw = !!document.querySelector("input[type='password']");
        const t = (document.body?.innerText || "").slice(0, 400).toLowerCase();
        return hasPw || /sign in|log in|authwall|join linkedin|sign up/.test(t);
      });
      if (wall) {
        return { results: [], needs_human: true, error: "Session expired — the site is showing a login page" };
      }
      results.push(...(await cmdExtract(step.card, step.fields || {}, 30)));
    }
  }
  return { results };
}

async function cmdDiscoverFlow(baseUrl, query) {
  const u = new URL(baseUrl);
  // ALWAYS work in the agent's own tab — never inspect or reuse the user's
  // current tab. Navigate to the site there.
  await cmdNavigate(baseUrl);
  await sleep(1500);

  // Guest-wall guard: LinkedIn (and similar) may show a login page to the
  // agent tab if the browser isn't signed in — say so precisely instead of
  // the generic "no search box".
  const pageKind = await execOnTab(() => {
    const hasPw = !!document.querySelector("input[type='password']");
    const t = (document.body?.innerText || "").slice(0, 400).toLowerCase();
    return {
      loginWall: hasPw || /sign in|log in|sign up|join linkedin/.test(t),
      url: location.href.slice(0, 120),
    };
  });
  if (pageKind && pageKind.loginWall) {
    throw new Error(
      "The agent tab is showing a sign-in page — click Re-login first, sign in to LinkedIn in the tab that opens, then press Record again"
    );
  }

  const searchSel = await execOnTab(() => {
    const inputs = Array.from(
      document.querySelectorAll("input[type='search'], input[name*='query' i], input[name*='search' i], input[placeholder*='search' i], input[aria-label*='search' i], input[type='text']")
    );
    const visible = inputs.filter((el) => el.offsetWidth || el.offsetHeight);
    const el = visible[0];
    if (!el) return null;
    el.scrollIntoView({ block: "center" });
    if (el.id) return "#" + CSS.escape(el.id);
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return `${el.tagName.toLowerCase()}[type="${el.type}"]`;
  });
  if (!searchSel) throw new Error("No search box found on the page");

  const steps = [
    { action: "navigate", url: baseUrl },
    { action: "fill", selector: searchSel, param: "query" },
    { action: "press", key: "Enter" },
    { action: "wait", seconds: 3 },
  ];

  await cmdFill(searchSel, query);
  await cmdPress("Enter");
  await sleep(3500);

  const cardCandidates = (await execOnTab(() => {
    const scored = [];
    for (const el of document.querySelectorAll("article, li, div, section")) {
      if (!el.querySelector("a")) continue;
      const textLen = (el.innerText || "").length;
      if (textLen < 60) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < 150 || rect.height < 40) continue;
      const siblings = el.parentElement
        ? Array.from(el.parentElement.children).filter((c) => c.tagName === el.tagName).length
        : 1;
      if (siblings >= 2) {
        let sel = el.tagName.toLowerCase();
        if (el.id && document.querySelectorAll("#" + CSS.escape(el.id)).length === 1) {
          sel += "#" + CSS.escape(el.id);
        } else if (el.className && typeof el.className === "string") {
          const cls = el.className.trim().split(/\s+/)[0];
          if (cls) sel += "." + CSS.escape(cls);
        }
        scored.push({ sel, textLen });
      }
    }
    scored.sort((a, b) => b.textLen - a.textLen);
    return scored.slice(0, 5).map((s) => s.sel);
  })) || [];
  if (cardCandidates.length === 0) {
    throw new Error("Could not find repeated result cards — search for something first, then retry");
  }

  const card = cardCandidates[0];
  const fields = (await execOnTab((cardSel) => {
    const c = document.querySelector(cardSel);
    if (!c) return {};
    const link = c.querySelector("a h1, a h2, a h3, a [class*='title'], a");
    const out = {};
    if (link) {
      const tag = link.tagName.toLowerCase();
      out.title =
        tag +
        (link.className && typeof link.className === "string" && link.className.trim()
          ? "." + link.className.trim().split(/\s+/)[0]
          : "");
    }
    return out;
  }, [card])) || {};

  steps.push({ card, fields });
  return { steps, card, fields, raw: [] };
}

// --- LinkedIn people search (ported from services/linkedin_people.py) ------

// Certification/acronym lines the old location heuristic mistook for
// locations (e.g. "CISA, ITIL Expert, PMP, CEH" under a name).
const CERT_LINE_RE =
  /\b(CISA|CISM|CISSP|ITIL|PMP|PRINCE2|CEH|ACCA|CPA|CFA|CIA|CFP|MBA|B\.?Com|CPAA|CA\s?\(?.?SG\)?)\b/i;

function linkedinSearchUrl(kind, query) {
  // kind: "people" | "jobs" — same URLs the backend builds today.
  const q = encodeURIComponent(query || "");
  return kind === "jobs"
    ? `https://www.linkedin.com/jobs/search/?keywords=${q}`
    : `https://www.linkedin.com/search/results/people/?keywords=${q}`;
}

async function linkedinWallGuard() {
  // Returns an error string when the agent tab is on a login/captcha wall.
  const state = await execOnTab(() => ({
    url: location.href,
    title: document.title || "",
    hasPw: !!document.querySelector("input[type='password']"),
    text: (document.body?.innerText || "").slice(0, 400).toLowerCase(),
  }));
  const url = (state.url || "").toLowerCase();
  if (/authwall|login|checkpoint/.test(url)) {
    return "LinkedIn login wall / session expired";
  }
  if (state.hasPw && /linkedin/.test(url)) {
    return "LinkedIn login wall / session expired";
  }
  if (/captcha|challenge|unusual activity/.test((state.title || "").toLowerCase())) {
    return "LinkedIn presented a CAPTCHA/challenge";
  }
  return null;
}

async function cmdLinkedinPeopleExtract() {
  // Port of _extract_candidates: find the results container (element whose
  // children each hold a /in/ profile link), then parse the same fields.
  const cards =
    (await execOnTab(() => {
      const main = document.querySelector("main") || document.body;
      // Score EVERY candidate container instead of taking the first match:
      // earlier wrapper strips (suggested searches, network rails) can match
      // first in document order with 3+ /in/-link children whose innerText
      // is empty (image-only links) — that yielded 0 candidates silently.
      // The REAL results list has the most in-link children, most of them
      // with visible name text.
      let best = null;
      for (const el of Array.from(main.querySelectorAll("*"))) {
        const kids = Array.from(el.children);
        const inKids = kids.filter((k) => k.querySelector("a[href*='/in/']"));
        if (inKids.length < 3) continue;
        let named = 0;
        let textLen = 0;
        for (const k of inKids) {
          const a = k.querySelector("a[href*='/in/']");
          if ((a.innerText || "").trim().length > 0) named++;
          textLen += (k.innerText || "").length;
        }
        const score = inKids.length * 10000 + named * 100 + Math.min(textLen, 9999);
        if (!best || score > best.score) best = { el, score };
      }
      if (!best) return { error: "no results container found", results: [] };
      const seen = new Set();
      const results = [];
      for (const kid of Array.from(best.el.children)) {
        const nameLink = kid.querySelector("a[href*='/in/']");
        if (!nameLink) continue;
        let href = nameLink.getAttribute("href") || "";
        if (href && seen.has(href)) continue;
        if (href) seen.add(href);
        // Name fallbacks: image-only links have empty innerText.
        let name =
          (nameLink.innerText || "").trim() ||
          (nameLink.getAttribute("aria-label") || "").trim() ||
          (nameLink.querySelector("img")?.getAttribute("alt") || "").trim();
        results.push({
          name,
          href,
          text: (kid.innerText || "").trim(),
        });
      }
      return { results };
    })) || {};
  if (cards.error) return { error: cards.error, candidates: [] };

  const candidates = [];
  for (const item of (cards.results || []).slice(0, 25)) {
    let name = (item.name || "").trim();
    name = name.split("•")[0].trim(); // "Name • 2nd" connection degree
    // Still unnamed after fallbacks (e.g. hidden rail entries) — skip only
    // if there is no profile URL to anchor the row either.
    if (!name && !item.href) continue;
    if (!name) name = "LinkedIn Member";
    const lines = (item.text || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const location =
      lines.slice(1, 6).find(
        (ln) =>
          ln.length < 50 &&
          ln.includes(",") &&
          !CERT_LINE_RE.test(ln) &&
          !ln.startsWith("Current:") &&
          !ln.startsWith("Past:")
      ) || null;
    const headline =
      lines
        .slice(1, 8)
        .find(
          (ln) =>
            ln.length > 15 &&
            !ln.startsWith("Current:") &&
            !ln.startsWith("Past:") &&
            ln !== location &&
            !ln.includes(" is a mutual connection") &&
            !["Connect", "Message", "Follow"].includes(ln)
        ) || null;
    const currentLine = lines.find((ln) => ln.startsWith("Current:"));
    candidates.push({
      id: "li-people-ext-" + Math.abs([...(name + item.href)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7)),
      name,
      headline,
      location,
      summary: (item.text || "").slice(0, 500),
      current_role: currentLine ? currentLine.slice(9) : null,
      skills: [],
      source: "linkedin_people",
      source_url: item.href || "",
      experience: (item.text || "").slice(0, 800),
    });
  }
  return { candidates };
}

async function cmdLinkedinProfileDetail(profileUrl) {
  // Port of _extract_profile_detail: sections keyed by h2 heading.
  let full = "";
  if (profileUrl.startsWith("/")) profileUrl = "https://www.linkedin.com" + profileUrl;
  await cmdNavigate(profileUrl);
  await sleep(1200);
  const wall = await linkedinWallGuard();
  if (wall) return { error: wall };
  await execOnTab(() => window.scrollBy(0, 1400));
  await sleep(900);
  const sections =
    (await execOnTab(() => {
      const result = {};
      document.querySelectorAll("section").forEach((sec) => {
        const h2 = sec.querySelector("h2");
        const heading = h2 ? h2.innerText.trim() : "";
        if (heading) result[heading] = (sec.innerText || "").trim().slice(0, 8000);
      });
      return result;
    })) || {};
  const bodyText =
    (await execOnTab(() => (document.body?.innerText || "").slice(0, 20000))) || "";
  let skillsText = sections["Top skills"] || sections["Skills"] || "";
  if (!skillsText) {
    const m = bodyText.match(/Top skills\s*\n(.+)/);
    if (m) skillsText = "Top skills\n" + m[1].split("\n")[0];
  }
  const lines = (skillsText || "").split("\n").map((l) => l.trim()).filter(Boolean);
  const skillLine = lines.slice(1).find((ln) => ln.includes("•"));
  const skills = skillLine
    ? skillLine.split("•").map((s) => s.trim()).filter(Boolean)
    : lines.length > 1
      ? [lines[1]]
      : [];
  return {
    summary: sections["About"] || "",
    skills,
    experience: sections["Experience"] || "",
    education: sections["Education"] || "",
    certifications: sections["Licenses & certifications"] || "",
  };
}

async function cmdLinkedinPeoplePlan(params) {
  // Full sourcing plan in ONE command so the backend's single dispatch maps
  // to one atomic extension execution (no interleaved queue state).
  const { queries = [], excludes = [], location = "", enrichBudget = 10 } = params;
  const merged = [];
  const perQuery = [];
  let blocker = null;
  for (const q of queries) {
    await sleep(1200 + Math.floor(Math.random() * 1500)); // polite pacing
    await cmdNavigate(linkedinSearchUrl("people", q));
    await sleep(1500);
    const wall = await linkedinWallGuard();
    if (wall) {
      blocker = blocker || wall;
      perQuery.push(`${q.slice(0, 30)}…: blocked`);
      continue;
    }
    const { candidates, error } = await cmdLinkedinPeopleExtract();
    perQuery.push(`${q.slice(0, 30)}${q.length > 30 ? "…" : ""}: ${error ? 0 : candidates.length}`);
    merged.push(...candidates);
  }
  // Safety net: if every query parsed empty, harvest ALL /in/ links on the
  // LAST results page directly (name from link text / aria-label / img alt).
  // The container heuristic should not be able to zero out a real results
  // page — this guarantees rows whenever profile links exist.
  if (!merged.length && !blocker) {
    const fallback = await execOnTab(() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll("a[href*='/in/']")) {
        const href = a.getAttribute("href") || "";
        if (!href || seen.has(href)) continue;
        const name =
          (a.innerText || "").trim() ||
          (a.getAttribute("aria-label") || "").trim() ||
          (a.querySelector("img")?.getAttribute("alt") || "").trim();
        const card = a.closest("li, div");
        const text = (card?.innerText || "").trim();
        if (!name && !text) continue;
        seen.add(href);
        out.push({ name, href, text: text.slice(0, 800) });
      }
      return out.slice(0, 25);
    });
    for (const f of fallback || []) {
      let name = (f.name || "").trim().split("•")[0].trim();
      if (!name) name = "LinkedIn Member";
      merged.push({
        id: "li-people-ext-" + Math.abs([...(name + f.href)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7)),
        name,
        headline: null,
        location: null,
        summary: (f.text || "").slice(0, 500),
        current_role: null,
        skills: [],
        source: "linkedin_people",
        source_url: f.href,
        experience: (f.text || "").slice(0, 800),
      });
    }
    perQuery.push(`fallback-harvest: ${merged.length}`);
  }
  // Dedupe by normalized profile URL (host → www, strip query/#/trailing /).
  const seen = new Map();
  for (const c of merged) {
    let u = c.source_url || "";
    if (u.startsWith("/")) u = "https://www.linkedin.com" + u;
    u = u.replace("://linkedin.com", "://www.linkedin.com").split("?")[0].split("#")[0].replace(/\/+$/, "");
    if (!u) continue;
    if (seen.has(u)) seen.get(u)._hit_count += 1;
    else {
      c._hit_count = 1;
      seen.set(u, c);
    }
  }
  let uniques = Array.from(seen.values());
  // Location post-filter: drop only cards clearly naming another country.
  const target = (location || "").trim().toLowerCase();
  let dropped = 0;
  if (target) {
    uniques = uniques.filter((c) => {
      const loc = (c.location || "").trim().toLowerCase();
      if (loc && !loc.includes(target)) {
        dropped += 1;
        return false;
      }
      return true;
    });
  }
  // Shared enrich budget over the merged top candidates.
  let enriched = 0;
  for (const c of uniques.slice(0, enrichBudget)) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    const detail = await cmdLinkedinProfileDetail(c.source_url);
    if (detail.error) {
      blocker = blocker || detail.error;
      break;
    }
    c.summary = detail.summary || c.summary;
    c.skills = detail.skills?.length ? detail.skills : c.skills;
    c.experience = detail.experience || c.experience;
    c.education = detail.education;
    c.certifications = detail.certifications;
    enriched += 1;
  }
  return {
    raw_results: uniques,
    needs_human: uniques.length === 0 && !!blocker,
    human_reason: blocker,
    plan_detail: `Extension plan v2: ${queries.length} queries [${perQuery.join("; ")}] → ${uniques.length} unique (${dropped} location-dropped, ${enriched} enriched)`,
  };
}

// Standalone enrichment: open top-N profile URLs and merge detail sections.
// Used by the backend to top-up unenriched rows after merge (relaxed pass).
async function cmdLinkedinPeopleEnrich(params) {
  const { candidates = [], enrichBudget = 10 } = params;
  const targets = candidates.slice(0, enrichBudget);
  let blocker = null;
  for (const c of targets) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    const detail = await cmdLinkedinProfileDetail(c.source_url);
    if (detail.error) {
      blocker = blocker || detail.error;
      break;
    }
    c.summary = detail.summary || c.summary;
    c.skills = detail.skills?.length ? detail.skills : c.skills;
    c.experience = detail.experience || c.experience;
    c.education = detail.education;
    c.certifications = detail.certifications;
  }
  return { candidates: targets, error: blocker };
}

// --- LinkedIn jobs search (ported from services/linkedin.py) ---------------

async function cmdLinkedinJobsSearch(params) {
  // One search + detail opens, mirroring search_linkedin_jobs' row shape.
  const { query = "", location = "", maxJobs = 25, detailBudget = 5 } = params;
  await sleep(1200 + Math.floor(Math.random() * 1500));
  await cmdNavigate(linkedinSearchUrl("jobs", query));
  await sleep(2500); // jobs list renders slower
  const wall = await linkedinWallGuard();
  if (wall) {
    return { raw_results: [], needs_human: true, human_reason: wall };
  }
  const rows =
    (await execOnTab(() => {
      const cards = document.querySelectorAll("li.scaffold-layout__list-item");
      const out = [];
      for (const c of Array.from(cards)) {
        const titleEl = c.querySelector(".job-card-container__link");
        const title = titleEl ? (titleEl.innerText || "").trim() : "";
        if (!title) continue;
        const company = (c.querySelector(".artdeco-entity-lockup__subtitle")?.innerText || "").trim();
        const metadata = (c.querySelector(".job-card-container__metadata-wrapper")?.innerText || "").trim();
        const footer = (c.querySelector(".job-card-list__footer-wrapper")?.innerText || "").trim();
        const mLines = metadata.split("\n").map((l) => l.trim()).filter(Boolean);
        const fLines = footer.split("\n").map((l) => l.trim()).filter(Boolean);
        out.push({
          title,
          href: (titleEl && titleEl.getAttribute("href")) || "",
          company,
          location: mLines[0] || null,
          salary_text: mLines.find((ln) => ln.includes("SGD") || ln.includes("$") || ln.includes("K")) || null,
          posted_at: fLines[0] || null,
          metadata_footer: footer,
        });
      }
      return out;
    })) || [];
  const seen = new Set();
  const jobs = [];
  for (const r of rows.slice(0, maxJobs)) {
    let href = r.href || "";
    if (href.startsWith("/")) href = "https://www.linkedin.com" + href;
    const key = r.title + "|" + r.company + "|" + href;
    if (seen.has(key)) continue;
    seen.add(key);
    jobs.push({
      id: "li-ext-" + Math.abs([...key].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) | 0, 7)),
      title: r.title,
      company: r.company,
      location: r.location,
      salary_text: r.salary_text,
      description: "",
      source: "linkedin",
      source_url: href,
      posted_at: r.posted_at,
      metadata_footer: r.metadata_footer,
    });
  }
  // Detail opens for the top jobs (description text).
  let opened = 0;
  for (const j of jobs.slice(0, detailBudget)) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    await cmdNavigate(j.source_url);
    await sleep(1500);
    const w2 = await linkedinWallGuard();
    if (w2) break;
    await execOnTab(() => window.scrollBy(0, 1200));
    await sleep(800);
    const text =
      (await execOnTab(() => {
        const el =
          document.querySelector(".jobs-description-content__text") ||
          document.querySelector(".jobs-box__html-content");
        if (el) return (el.innerText || "").trim().slice(0, 20000);
        for (const e of document.querySelectorAll("*")) {
          const t = (e.innerText || "").trim();
          if (t.startsWith("About the job") && t.length > 200) return t.slice(0, 20000);
        }
        return "";
      })) || "";
    j.description = text;
    if (!j.posted_at) {
      const top =
        (await execOnTab(() =>
          (document.querySelector(".jobs-unified-top-card__content--two-pane, .jobs-unified-top-card")?.innerText || "")
        )) || "";
      const lines = top.split("\n").map((l) => l.trim()).filter(Boolean);
      j.posted_at = lines.find((ln) => /ago|day|week|month/.test(ln)) || j.posted_at;
    }
    opened += 1;
  }
  return {
    raw_results: jobs,
    needs_human: false,
    human_reason: null,
    plan_detail: `Extension jobs: ${query.slice(0, 40)} → ${jobs.length} jobs (${opened} detail-opened)`,
  };
}

// --- manual filter recorder --------------------------------------------------
// Records the user's clicks in the agent tab (filter panels, dropdowns, tabs)
// so a flow can replay them after the keyword search. Two commands:
//   start_record — injects a capture-phase click listener into the agent tab
//   stop_record  — reads the collected events and tears the listener down
// Events live on window.__caRecord in the page itself, so soft navigations
// within an SPA (seek's filter panel re-renders in place) don't lose them.

async function cmdStartRecord() {
  // Ensure we have an agent tab (about:blank is fine — the user will navigate
  // it or it's already on the site from a previous discover).
  await execOnTab(() => {
    if (window.__caRecord) return { already: true };
    window.__caRecord = { events: [], capture: null };
    const state = window.__caRecord;

    // Build a CSS selector for a clicked element: prefer unique id, then
    // data-testid/aria-label, then a short tag+class path (max 4 levels).
    function buildSelector(el) {
      if (!(el instanceof Element)) return null;
      if (el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1) {
        return `#${CSS.escape(el.id)}`;
      }
      const dt = el.getAttribute("data-testid") || el.getAttribute("data-test");
      if (dt && document.querySelectorAll(`[data-testid="${dt}"]`).length === 1) {
        return `[data-testid="${dt}"]`;
      }
      const aria = el.getAttribute("aria-label");
      if (aria && document.querySelectorAll(`[aria-label="${aria}"]`).length === 1) {
        return `[aria-label="${aria}"]`;
      }
      // name+type for form controls
      if (el.name && document.getElementsByName(el.name).length === 1) {
        return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
      }
      const parts = [];
      let cur = el;
      let depth = 0;
      while (cur && cur instanceof Element && depth < 4) {
        let part = cur.tagName.toLowerCase();
        if (cur.id && document.querySelectorAll(`#${CSS.escape(cur.id)}`).length === 1) {
          parts.unshift(`#${CSS.escape(cur.id)}`);
          break;
        }
        if (cur.className && typeof cur.className === "string" && cur.className.trim()) {
          const cls = cur.className.trim().split(/\s+/).slice(0, 2);
          const scoped = cls.map((c) => `.${CSS.escape(c)}`).join("");
          part += scoped;
        }
        // nth-of-type disambiguation among siblings
        const parent = cur.parentElement;
        if (parent) {
          const sameTag = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
          if (sameTag.length > 1) {
            part += `:nth-of-type(${sameTag.indexOf(cur) + 1})`;
          }
        }
        parts.unshift(part);
        cur = cur.parentElement;
        depth += 1;
      }
      return parts.join(" > ");
    }

    const capture = (ev) => {
      try {
        // Only left clicks on real elements; ignore the recorder's own UI.
        if (ev.button !== 0) return;
        const el = ev.target;
        if (!(el instanceof Element)) return;
        if (el.closest("[data-ca-record-ignore]")) return;
        const label = (
          el.getAttribute("aria-label") ||
          el.getAttribute("title") ||
          (el.textContent || "").trim().slice(0, 60) ||
          el.tagName.toLowerCase()
        );
        state.events.push({
          action: "click",
          selector: buildSelector(el),
          text: label,
          ts: Date.now(),
        });
        // Visual feedback flash so the user sees what's captured.
        const prev = el.style.outline;
        el.style.outline = "2px solid #2e9e5b";
        setTimeout(() => {
          el.style.outline = prev;
        }, 400);
      } catch {
        /* never let the recorder break the page */
      }
    };
    state.capture = capture;
    // capture phase + pointerdown so dropdown option handlers (which may
    // unmount the element on click) still get recorded.
    document.addEventListener("click", capture, true);
    return { ok: true };
  });
  return { ok: true, recording: true };
}

async function cmdStopRecord() {
  const events =
    (await execOnTab(() => {
      const state = window.__caRecord;
      if (!state) return null;
      const out = state.events.slice();
      if (state.capture) document.removeEventListener("click", state.capture, true);
      delete window.__caRecord;
      return out;
    })) || [];
  // Drop events with no usable selector; collapse rapid duplicate clicks
  // (double-click on the same target records twice).
  const cleaned = [];
  for (const e of events) {
    if (!e.selector) continue;
    const last = cleaned[cleaned.length - 1];
    if (last && last.selector === e.selector && e.ts - last.ts < 400) continue;
    cleaned.push({ action: "click", selector: e.selector, text: e.text });
  }
  return { ok: true, events: cleaned, count: cleaned.length };
}

// --- dispatch -------------------------------------------------------------

async function executeCommand(cmd) {
  const { action, params = {} } = cmd;
  switch (action) {
    case "navigate": return cmdNavigate(params.url);
    case "fill": return cmdFill(params.selector, params.text);
    case "click": return cmdClick(params.selector);
    case "press": return cmdPress(params.key || "Enter");
    case "extract": return cmdExtract(params.card, params.fields, params.maxItems);
    case "run_flow": return cmdRunFlow(params.baseUrl, params.query, params.steps);
    case "discover_flow": return cmdDiscoverFlow(params.baseUrl, params.query, params.flowType);
    case "get_cookies": return cmdGetCookies(params.url);
    case "start_record": return cmdStartRecord();
    case "stop_record": return cmdStopRecord();
    case "linkedin_people_plan": return cmdLinkedinPeoplePlan(params);
    case "linkedin_people_enrich": return cmdLinkedinPeopleEnrich(params);
    case "linkedin_jobs_search": return cmdLinkedinJobsSearch(params);
    default: throw new Error(`Unknown action: ${action}`);
  }
}

// --- polling loop ----------------------------------------------------------

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollOnce() {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch(`${API_BASE}/api/v1/agent/poll`);
    if (!res.ok) {
      setBadge(false);
      return;
    }
    const data = await res.json();
    if (data.command) {
      const { id, action, params } = data.command;
      setBadge(true);
      try {
        const result = await executeCommand({ action, params });
        await postResult(id, true, result, null);
      } catch (e) {
        await postResult(id, false, null, String((e && e.message) || e));
      }
    } else {
      // poll itself proves liveness server-side; badge reflects that.
      setBadge(true);
    }
  } catch (e) {
    // API unreachable — expected when backend is down.
    setBadge(false);
  } finally {
    busy = false;
  }
}

async function postResult(id, ok, data, error) {
  try {
    await fetch(`${API_BASE}/api/v1/agent/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, ok, data, error }),
    });
  } catch {
    /* server will time the command out; nothing else to do */
  }
}

async function loop() {
  await loadConfig();
  while (true) {
    await pollOnce();
    await sleep(POLL_INTERVAL_MS);
  }
}

// Guard against multiple concurrent loops (alarm + startup + install can
// each kick one): only one while-loop runs the poll cycle at a time.
let looping = false;
async function loopGuarded() {
  if (looping) return;
  looping = true;
  try {
    await loop();
  } finally {
    looping = false;
  }
}

// Popup "Save & connect" restarts the loop so a changed apiBase applies now
// instead of waiting for the next alarm wake.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "restart-loop") {
    looping = false; // allow a fresh loop with the new config
    loopGuarded();
  }
});

// A persistent while-loop in the service worker keeps it alive while active,
// and every fetch wakes it if suspended. Chrome suspends idle MV3 workers
// after ~30s regardless of pending work, killing the loop — so a
// chrome.alarms heartbeat (minimum period 30s, but reliable) re-kicks the
// loop whenever the worker is woken. Every fetch in pollOnce() itself wakes
// the worker too when a command arrives mid-suspension isn't possible; the
// alarm bounds the worst-case reconnect latency to ~30s.
const HEARTBEAT_ALARM = "career-agent-heartbeat";

chrome.runtime.onStartup.addListener(loopGuarded);
chrome.runtime.onInstalled.addListener(loopGuarded);

chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM) {
    loopGuarded();
  }
});

loopGuarded();
