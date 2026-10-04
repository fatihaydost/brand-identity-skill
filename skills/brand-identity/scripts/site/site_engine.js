// site_engine.js - the measuring and recolouring code of the live-site tool (scripts/site_palette.py).
//
// Two kinds of functions live here, both moved unchanged from the former Node tool (site_palette.mjs):
//  * page functions (pageExtract, pageRecolor, pageFontSwap, pageLogoSwap, ...): the Python driver sends their
//    source (fn.toString()) into the site's page over the Chrome DevTools protocol and calls them there, exactly as
//    Playwright's page.evaluate did;
//  * pure functions (colorLib, buildRoles, layoutStress, parkedReason, ...): they run in a blank "engine" page of
//    the same browser, so the arithmetic is the same JavaScript as before (V8 doubles, toFixed, regexes).
// The driver loads this file once per browser; SiteEngine.call(name, args) runs a pure function, SiteEngine.sources
// hands out page-function sources. No network, no file access, no DOM changes in the engine page.
(function () {
'use strict';

const ROLE_KEYS = ['background', 'surface', 'surfaceAlt', 'border', 'text', 'textMuted', 'primary', 'onPrimary',
  'accent', 'onAccent', 'link', 'focus', 'success', 'warning', 'danger', 'info'];

// ---------------------------------------------------------------- colour library
// Self-contained so it can be stringified into the page (page.evaluate) and used in Node.
function colorLib() {
  const NAMED = { white: '#ffffff', black: '#000000', red: '#ff0000', blue: '#0000ff', navy: '#000080', gray: '#808080', grey: '#808080', silver: '#c0c0c0', green: '#008000', orange: '#ffa500', yellow: '#ffff00', purple: '#800080', maroon: '#800000', teal: '#008080', whitesmoke: '#f5f5f5', gainsboro: '#dcdcdc', lightgray: '#d3d3d3', lightgrey: '#d3d3d3', darkgray: '#a9a9a9', darkgrey: '#a9a9a9', dimgray: '#696969', aliceblue: '#f0f8ff', ghostwhite: '#f8f8ff', ivory: '#fffff0', beige: '#f5f5dc' };
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const s2l = c => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const l2s = c => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);
  function rgbToOklab({ r, g, b }) {
    const R = s2l(r / 255), G = s2l(g / 255), B = s2l(b / 255);
    const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
    const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
    const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
    return { L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s };
  }
  function oklabToLinear({ L, a, b }) {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  }
  const inGamut = lin => lin.every(v => v >= -1e-4 && v <= 1 + 1e-4);
  const linToRgb = lin => { const [r, g, b] = lin.map(v => Math.round(clamp(l2s(clamp(v, 0, 1)), 0, 1) * 255)); return { r, g, b }; };
  function oklchToRgb({ L, C, h }) {
    L = clamp(L, 0, 1);
    const lab = c => ({ L, a: c * Math.cos(h * Math.PI / 180), b: c * Math.sin(h * Math.PI / 180) });
    let lin = oklabToLinear(lab(C));
    if (!inGamut(lin)) { // reduce chroma until the colour fits sRGB
      let lo = 0, hi = C;
      for (let i = 0; i < 20; i++) { const mid = (lo + hi) / 2; if (inGamut(oklabToLinear(lab(mid)))) lo = mid; else hi = mid; }
      lin = oklabToLinear(lab(lo));
    }
    return linToRgb(lin);
  }
  function toOklch(rgb) { const { L, a, b } = rgbToOklab(rgb); const C = Math.hypot(a, b); let h = Math.atan2(b, a) * 180 / Math.PI; if (h < 0) h += 360; return { L, C, h }; }
  const hex2 = n => Math.round(clamp(+n || 0, 0, 255)).toString(16).padStart(2, '0');
  function toHex({ r, g, b }) { return '#' + hex2(r) + hex2(g) + hex2(b); }
  function num(s, scale) { s = s.trim(); if (s.endsWith('%')) return parseFloat(s) / 100 * scale; return parseFloat(s); }
  function hslToRgb(h, s, l) {
    s /= 100; l /= 100; const k = n => (n + h / 30) % 12; const a = s * Math.min(l, 1 - l);
    const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return { r: Math.round(f(0) * 255), g: Math.round(f(8) * 255), b: Math.round(f(4) * 255) };
  }
  // Parse one CSS colour token -> {r,g,b,a,fmt} or null. Hex, rgb(a), hsl(a), oklch, oklab and common names.
  function parse(str) {
    if (!str) return null; let s = String(str).trim().toLowerCase();
    if (NAMED[s]) s = NAMED[s];
    let m;
    if ((m = s.match(/^#([0-9a-f]{3,8})$/))) {
      let h = m[1]; if (h.length === 3 || h.length === 4) h = [...h].map(c => c + c).join('');
      if (h.length !== 6 && h.length !== 8) return null;
      return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1, fmt: 'hex' };
    }
    if ((m = s.match(/^(rgba?|hsla?|oklch|oklab)\((.*)\)$/))) {
      const fn = m[1]; const parts = m[2].replace(/\//, ' / ').split(/[\s,]+/).filter(Boolean);
      const slash = parts.indexOf('/'); let alpha = 1;
      if (slash >= 0) { alpha = num(parts[slash + 1], 1); parts.splice(slash); } else if (parts.length === 4) { alpha = num(parts[3], 1); parts.length = 3; }
      if (parts.length < 3 || parts.some(p => p === 'none' || /var|calc|from/.test(p))) return null;
      let rgb;
      if (fn.startsWith('rgb')) rgb = { r: Math.round(num(parts[0], 255)), g: Math.round(num(parts[1], 255)), b: Math.round(num(parts[2], 255)) };
      else if (fn.startsWith('hsl')) rgb = hslToRgb(parseFloat(parts[0]), parseFloat(parts[1]), parseFloat(parts[2]));
      else if (fn === 'oklch') rgb = oklchToRgb({ L: num(parts[0], 1), C: num(parts[1], 0.4), h: parseFloat(parts[2]) });
      else rgb = linToRgb(oklabToLinear({ L: num(parts[0], 1), a: num(parts[1], 0.4), b: num(parts[2], 0.4) }));
      if ([rgb.r, rgb.g, rgb.b, alpha].some(Number.isNaN)) return null;
      return { ...rgb, a: alpha, fmt: fn };
    }
    return null;
  }
  function format(c, a) { const al = a == null ? 1 : a; if (al >= 0.999) return toHex(c); return `rgba(${c.r}, ${c.g}, ${c.b}, ${+al.toFixed(3)})`; }
  function deltaE(x, y) { const A = rgbToOklab(x), B = rgbToOklab(y); return Math.hypot(A.L - B.L, A.a - B.a, A.b - B.b); }
  const hueDist = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };
  function luminance({ r, g, b }) { return 0.2126 * s2l(r / 255) + 0.7152 * s2l(g / 255) + 0.0722 * s2l(b / 255); }
  function contrast(x, y) { const a = luminance(x), b = luminance(y); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); }

  // Palette remapper: old role colours -> new role colours, for ANY colour on the page.
  // Neutrals: piecewise-linear along the old background->text lightness axis, onto the new ramp
  //   (anchors background, surface, border, textMuted, text).
  // Chromatic: nearest old chromatic anchor by hue (primary, link, accent, heading); the colour keeps
  //   its L / C-ratio / h offset from that anchor. Hues further than `hueTolerance` from every anchor
  //   (status reds and greens) are left alone.
  function makeMapper(oldRoles, newRoles, { neutralChroma = 0.04, hueTolerance = 55, scales = null } = {}) {
    const P = h => (h ? parse(h) : null);
    const oBg = P(oldRoles.background), oTx = P(oldRoles.text), nBg = P(newRoles.background), nTx = P(newRoles.text);
    const Lb = toOklch(oBg).L, Lt = toOklch(oTx).L;
    const tOf = c => (Lt === Lb ? 0 : (toOklch(c).L - Lb) / (Lt - Lb));
    let ramp = [{ t: 0, c: rgbToOklab(nBg) }, { t: 1, c: rgbToOklab(nTx) }];
    for (const k of ['surface', 'border', 'textMuted']) {
      const o = P(oldRoles[k]), n = P(newRoles[k]);
      if (o && n && toOklch(o).C < neutralChroma * 1.5) { const t = tOf(o); if (t > 0.01 && t < 0.99) ramp.push({ t, c: rgbToOklab(n) }); }
    }
    ramp.sort((a, b) => a.t - b.t);
    ramp = ramp.filter((p, i) => i === 0 || p.t - ramp[i - 1].t > 0.01);
    function neutral(c) {
      const t = clamp(tOf(c), 0, 1);
      let i = 0; while (i < ramp.length - 2 && t > ramp[i + 1].t) i++;
      const A = ramp[i], B = ramp[i + 1], u = (t - A.t) / (B.t - A.t || 1);
      return linToRgb(oklabToLinear({ L: A.c.L + (B.c.L - A.c.L) * u, a: A.c.a + (B.c.a - A.c.a) * u, b: A.c.b + (B.c.b - A.c.b) * u }));
    }
    const fallbackNew = { link: newRoles.link || newRoles.primary, heading: newRoles.text, accent: newRoles.accent || newRoles.primary, primary: newRoles.primary };
    const anchors = [];
    for (const k of ['primary', 'link', 'accent', 'heading']) {
      const o = P(oldRoles[k]); const n = P(k === 'heading' ? fallbackNew.heading : (newRoles[k] || fallbackNew[k]));
      if (o && n && toOklch(o).C >= neutralChroma) anchors.push({ k, o: toOklch(o), n: toOklch(n) });
    }
    // Pale tints of a chromatic anchor (highlighted row, selected chip: low chroma but clearly in the anchor's hue)
    // follow that anchor to the new palette instead of turning grey. With palette scales, the scale step nearest
    // in lightness is used; otherwise the new anchor's hue at the ramp lightness.
    const roleNeutrals = ['background', 'surface', 'border', 'textMuted', 'text'].map(k => P(oldRoles[k])).filter(Boolean);
    const scaleOf = k => { const sc = scales && scales[k === 'link' ? 'primary' : k]; const st = sc && (sc.steps || sc); return st && typeof st === 'object' ? Object.values(st).map(P).filter(Boolean).map(x => ({ x, L: toOklch(x).L })) : null; };
    function tint(c, lch) {
      if (lch.C < 0.012 || roleNeutrals.some(r => deltaE(r, c) < 0.02)) return null;
      let best = null, bd = 1e9;
      for (const A of anchors) { if (A.k === 'heading') continue; const d = hueDist(lch.h, A.o.h); if (d < bd - 1e-9 || (Math.abs(d - bd) < 1e-9 && A.k === 'accent')) { bd = d; best = A; } }
      if (!best || bd > 30) return null;
      const nL = toOklch(neutral(c)).L;
      const steps = scaleOf(best.k);
      if (steps && steps.length) return steps.reduce((a, b) => (Math.abs(b.L - nL) < Math.abs(a.L - nL) ? b : a)).x;
      return oklchToRgb({ L: nL, C: Math.min(0.06, lch.C * (best.n.C / Math.max(best.o.C, 0.01))), h: best.n.h });
    }
    return function map(c) {
      const lch = toOklch(c);
      if (lch.C < neutralChroma) return tint(c, lch) || neutral(c);
      let best = null, bd = 1e9;
      for (const A of anchors) { const d = hueDist(lch.h, A.o.h) + Math.abs(lch.L - A.o.L) * 60; if (d < bd) { bd = d; best = A; } }
      if (!best || hueDist(lch.h, best.o.h) > hueTolerance) return c;
      const C = best.n.C < neutralChroma ? best.n.C : clamp(best.n.C * (lch.C / best.o.C), 0, 0.37);
      return oklchToRgb({ L: best.n.L + (lch.L - best.o.L), C, h: best.n.h + (lch.h - best.o.h) });
    };
  }

  // Rewrite every colour token inside a CSS *value*; url(...) segments are left untouched.
  const TOKEN = /url\([^)]*\)|#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?|oklch|oklab)\([^()]*\)|\b(?:white|black|red|blue|navy|gray|grey|silver|green|orange|yellow|purple|maroon|teal|whitesmoke|gainsboro|lightgr[ae]y|darkgr[ae]y|dimgray|aliceblue|ghostwhite|ivory|beige)\b/g;
  const COLOR_PROP = /^(--|color$|background|border|outline|box-shadow|text-shadow|fill$|stroke$|stop-color|flood-color|lighting-color|text-decoration|caret-color|column-rule|accent-color|scrollbar-color|-webkit-text-fill-color|-webkit-text-stroke|-webkit-tap-highlight-color|text-emphasis-color)/i;
  function rewriteValue(value, map) {
    return value.replace(TOKEN, tok => {
      if (tok.startsWith('url(')) return tok;
      const c = parse(tok); if (!c) return tok;
      return format(map(c), c.a);
    });
  }
  // Rewrite colour declarations in raw CSS text (stylesheets fetched over the network). Selectors are untouched.
  function rewriteCss(text, map) {
    return text.replace(/(^|[{;\s])(--[\w-]+|-?[a-zA-Z][\w-]*)(\s*:\s*)([^;{}]+)(?=[;}])/g, (all, pre, prop, colon, val) =>
      COLOR_PROP.test(prop) ? pre + prop + colon + rewriteValue(val, map) : all);
  }
  return { parse, format, toHex, toOklch, rgbToOklab, oklchToRgb, deltaE, hueDist, contrast, makeMapper, rewriteValue, rewriteCss, COLOR_PROP };
}
const C = colorLib();
const COLOR_LIB_SRC = `(${colorLib.toString()})()`;

// ---------------------------------------------------------------- capture hygiene
// Durations 0 (not `animation: none`): scroll-reveal elements jump to their visible end state.
const FREEZE_CSS = `*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;transition-duration:0s!important;transition-delay:0s!important;scroll-behavior:auto!important;caret-color:transparent!important}`;
const CONSENT_SEL = ['#onetrust-banner-sdk', '#onetrust-consent-sdk', '#CybotCookiebotDialog', '#usercentrics-root', '.cc-window', '.cky-consent-container', '.osano-cm-window', '#truste-consent-track', '.qc-cmp2-container', '[id^="sp_message_container"]', '#cookie-law-info-bar', '.cookie-notice', '#cookie-notice', '[aria-label*="cookie" i][role="dialog"]'];

// Bot walls (Cloudflare "Just a moment", Akamai "Access Denied", captchas) load fine and would be measured as the
// site: a near-empty page in browser-default link blue. Detect them and fail instead of returning fake roles.
function pageBlockCheck(status) {
  const title = (document.title || '').trim();
  const text = (document.body ? document.body.innerText : '').trim();
  const short = text.length < 1500;
  const PATS = [/access denied/i, /just a moment/i, /attention required/i, /checking (if the site connection is secure|your browser)/i,
    /verify(ing)? (that )?you are (a )?human/i, /are you a (robot|human)/i, /captcha/i, /you have been blocked|request (was )?blocked|unusual traffic|bot detected/i,
    /pardon our interruption/i, /enable javascript and cookies to continue/i];
  const tHit = PATS.find(p => p.test(title));
  if (tHit) return `page title "${title.slice(0, 60)}"`;
  const el = document.querySelector('#challenge-form, #challenge-running, #cf-challenge-running, .cf-challenge, [id^="cf-chl"], .cf-error-details, script[src*="challenges.cloudflare.com"], iframe[src*="challenges.cloudflare.com"], iframe[src*="captcha"], .g-recaptcha, .h-captcha, #px-captcha, #captcha-container');
  if (el && short) return `challenge element ${el.id ? '#' + el.id : el.className ? '.' + String(el.className).split(' ')[0] : el.tagName.toLowerCase()}`;
  const bHit = short && PATS.find(p => p.test(text.slice(0, 1500)));
  if (bHit) return `page text "${(text.match(bHit) || [''])[0]}"`;
  if (status >= 400 && short) return `HTTP ${status} with a near-empty page`;
  const links = [...document.querySelectorAll('a')].slice(0, 40).map(a => getComputedStyle(a).color);
  const uaLinks = links.length > 0 && links.every(c => c === 'rgb(0, 0, 238)' || c === 'rgb(85, 26, 139)');
  if (text.length < 400 && uaLinks && document.styleSheets.length <= 1) return 'near-empty page with browser-default link colours and no site styles';
  return null;
}

// ---------------------------------------------------------------- extraction (runs in the page)
// The site's logo: img / svg / role=img, a text wordmark in the home link, or a CSS background image on a
// logo-named element. Scored on logo-ish names, home link, header, top-left and alt ~ site name. Ties keep the
// earlier (image) candidate. Returns {el, kind: img|svg|text|bg, score} or null. Stringified into the page.
function logoFinder() {
  const vw = innerWidth;
  const home = new Set(['/', './', 'index.html', '/index.html', '#', location.origin, location.origin + '/', location.href, location.pathname]);
  const siteWord = (document.title.split(/[|\-–—:·]/)[0] || '').trim().toLowerCase();
  const cls = e => String(e.className && e.className.baseVal != null ? e.className.baseVal : e.className || '');
  const isHome = a => a && (home.has(a.getAttribute('href')) || (() => { try { const u = new URL(a.href); return u.origin === location.origin && (u.pathname === '/' || u.pathname === location.pathname) && !u.hash.slice(1); } catch { return false; } })());
  const near = r => r.width >= 16 && r.height >= 12 && r.top + scrollY <= 400 && r.width <= vw * 0.6;
  let best = null;
  const consider = (el, kind, sc) => { if (!best || sc > best.score) best = { el, kind, score: sc }; };
  for (const e of document.querySelectorAll('img, svg, [role="img"]')) {
    if (e.closest('svg') && e.tagName.toLowerCase() !== 'svg') continue;
    if (e.parentElement && e.parentElement.closest('svg')) continue;
    const r = e.getBoundingClientRect();
    if (!near(r)) continue;
    const name = [e.id, cls(e), e.getAttribute('alt'), e.getAttribute('src'), e.getAttribute('aria-label'), e.parentElement && cls(e.parentElement)].join(' ').toLowerCase();
    let sc = 0;
    if (/logo|brand/.test(name)) sc += 3;
    if (e.closest('[class*="logo" i], [id*="logo" i], [class*="brand" i]')) sc += 2;
    if (isHome(e.closest('a[href]'))) sc += 3;
    if (e.closest('header, nav, [role="banner"]')) sc += 2;
    if (r.left < vw / 2) sc += 1;
    if (siteWord && (e.getAttribute('alt') || '').toLowerCase().includes(siteWord)) sc += 2;
    if (/sponsor|partner|client|foreign|avatar|badge|flag/.test(name)) sc -= 4;
    const tag = e.tagName.toLowerCase();
    consider(e, tag === 'svg' ? 'svg' : 'img', sc);
  }
  // text wordmark: a home link or logo/brand-named element with short text and no image inside
  for (const e of document.querySelectorAll('a[href], [class*="logo" i], [id*="logo" i], [class*="brand" i]')) {
    if (e.querySelector('img, svg, [role="img"]') || e.closest('svg')) continue;
    const t = (e.innerText || '').trim();
    const r = e.getBoundingClientRect();
    if (!near(r) || t.length < 2 || t.length > 40 || /\n/.test(t)) continue;
    const name = [e.id, cls(e), e.getAttribute('aria-label')].join(' ').toLowerCase();
    let sc = 0;
    if (/logo|brand/.test(name)) sc += 3;
    if (isHome(e.tagName === 'A' ? e : e.closest('a[href]'))) sc += 3;
    if (e.closest('header, nav, [role="banner"]')) sc += 2;
    if (r.left < vw / 2) sc += 1;
    if (siteWord && t.toLowerCase().includes(siteWord)) sc += 2;
    if (/^(home|ana ?sayfa|start|inicio|accueil|startseite)$/i.test(t)) sc -= 5;
    if (/skip|menu|toggle|sponsor/.test(name)) sc -= 4;
    if (parseFloat(getComputedStyle(e).fontSize) < 13) sc -= 2;
    consider(e, 'text', sc);
  }
  // css background logo: a logo-named element painting a url() image
  for (const e of document.querySelectorAll('[class*="logo" i], [id*="logo" i]')) {
    if (e.querySelector('img, svg')) continue;
    const bi = getComputedStyle(e).backgroundImage;
    if (!bi || !/url\(/.test(bi)) continue;
    const r = e.getBoundingClientRect();
    if (!near(r)) continue;
    let sc = 5;
    if (isHome(e.closest('a[href]'))) sc += 3;
    if (e.closest('header, nav, [role="banner"]')) sc += 2;
    consider(e, 'bg', sc);
  }
  return best && best.score >= 3 ? best : null;
}
const LOGO_SRC = `(${logoFinder.toString()})`;

function pageExtract(arg) {
  const LIB = typeof arg === 'string' ? arg : arg.LIB;
  const LOGO = typeof arg === 'string' ? null : arg.LOGO;
  const L = eval(LIB);
  // Universal colour resolver: let the browser paint any CSS colour into a 1px canvas.
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  function rgba(str) {
    if (!str || str === 'transparent' || str === 'none' || str.startsWith('url')) return null;
    if (cache.has(str)) return cache.get(str);
    let out = L.parse(str);
    if (!out) {
      cx.clearRect(0, 0, 1, 1); cx.fillStyle = 'rgba(0,0,0,0)'; cx.fillStyle = str;
      if (cx.fillStyle === 'rgba(0, 0, 0, 0)' && !/transparent|0\)$/.test(str)) { cache.set(str, null); return null; }
      cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data;
      out = d[3] ? { r: d[0], g: d[1], b: d[2], a: d[3] / 255 } : null; // getImageData is already un-premultiplied
    }
    cache.set(str, out); return out;
  }
  const hex = c => L.toHex(c);
  const vw = innerWidth, vh = innerHeight;
  const docH = Math.max(document.documentElement.scrollHeight, vh);
  const stats = new Map();
  const S = h => { if (!stats.has(h)) stats.set(h, { hex: h, bg: 0, text: 0, border: 0, svg: 0, gradient: 0, fold: 0, n: 0 }); return stats.get(h); };
  const votes = { primary: new Map(), onPrimary: new Map(), link: new Map(), heading: new Map(), text: new Map(), border: new Map(), textOn: new Map() };
  const vote = (k, h, w) => { const v = votes[k].get(h) || { w: 0, n: 0 }; v.w += w; v.n++; votes[k].set(h, v); };
  const bgOf = new Map();
  function effectiveBg(el) {
    for (let p = el; p; p = p.parentElement) { if (bgOf.has(p)) return bgOf.get(p); const c = rgba(getComputedStyle(p).backgroundColor); if (c && c.a > 0.9) return hex(c); }
    return '#ffffff';
  }
  const root = rgba(getComputedStyle(document.documentElement).backgroundColor);
  const body = document.body ? rgba(getComputedStyle(document.body).backgroundColor) : null;
  const pageBg = body && body.a > 0.5 ? hex(body) : root && root.a > 0.5 ? hex(root) : '#ffffff';
  S(pageBg).bg += vw * docH; S(pageBg).n++; bgOf.set(document.body, pageBg);

  // Third-party widgets (chat bubbles, WhatsApp buttons, consent UIs) and floating overlays are not the brand:
  // they and everything inside them stay out of the measurement.
  const WIDGET = /whatsapp|wa\.me|intercom|crisp|tawk|zendesk|ze-?widget|\bdrift|hubspot|hs-chat|livechat|tidio|olark|freshchat|freshworks|jivo|chatra|smartsupp|messenger|customerchat|chat-?widget|chat-?bubble|chat-?button|chat-?launcher|cookie-?(banner|bar|notice|consent|law|policy|popup|modal|dialog)|consent|gdpr|onetrust|cookiebot|usercentrics|didomi|\bcmp-|back-?to-?top|scroll-?to-?top|scroll-?top/i;
  const skipped = new Set(); let widgetCount = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg') && !(el instanceof SVGGraphicsElement)) continue;
    if (skipped.has(el.parentElement)) { skipped.add(el); continue; }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)), h = r.height;
    if (w < 1 || h < 1) continue;
    {
      const cn = typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '';
      const sig = `${el.id || ''} ${cn} ${el.getAttribute('aria-label') || ''} ${el.tagName === 'A' ? el.getAttribute('href') || '' : ''}`;
      const floating = cs.position === 'fixed' && ((r.width <= 170 && r.height <= 170) || r.width * r.height > vw * vh * 0.5);
      if (el.tagName === 'IFRAME' || WIDGET.test(sig) || floating) { skipped.add(el); widgetCount++; continue; }
    }
    const top = r.top + scrollY, area = w * h, fold = top < vh ? 1 : 0;
    const foldArea = fold ? w * Math.max(0, Math.min(r.bottom + scrollY, vh) - top) : 0;
    const tag = el.tagName.toLowerCase();
    // background: area-weighted, subtracted from the parent surface it paints over
    const bgc = rgba(cs.backgroundColor);
    if (bgc && bgc.a > 0.05 && el !== document.body) {
      const h1 = hex(bgc); const st = S(h1); st.bg += area * bgc.a; st.n++; st.fold += foldArea;
      if (bgc.a > 0.9) { const parentBg = effectiveBg(el.parentElement); if (parentBg !== h1) S(parentBg).bg -= area; bgOf.set(el, h1); }
    }
    if (cs.backgroundImage && cs.backgroundImage.includes('gradient')) {
      const toks = cs.backgroundImage.match(/(?:rgba?|oklch|oklab|lab|lch|color)\([^()]*\)|#[0-9a-f]{3,8}/gi) || [];
      for (const t of toks) { const c = rgba(t); if (c && c.a > 0.1) S(hex(c)).gradient += area / toks.length; }
    }
    // text: direct text length x font size
    let chars = 0; for (const n of el.childNodes) if (n.nodeType === 3) chars += n.textContent.trim().length;
    if (chars > 0) {
      const c = rgba(cs.color); if (c && c.a > 0.3) {
        const h1 = hex(c), wt = chars * parseFloat(cs.fontSize) / 16; S(h1).text += wt; S(h1).n++;
        vote('textOn', effectiveBg(el) + '|' + h1, wt);
        if (!(el instanceof SVGElement)) { // chart / icon text inside SVG is not copy
          if (/^h[1-3]$/.test(tag)) vote('heading', h1, wt);
          else if (/^(p|li|td|dd|dt|blockquote|article|main|section|div|span|small|figcaption|label|em|strong|time)$/.test(tag) && chars > 12) vote('text', effectiveBg(el) + '|' + h1, wt);
          if (tag === 'a' && el.closest('p,li,td,article,main') && !(bgc && bgc.a > 0.9)) vote('link', h1, wt);
        }
      }
    }
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      const bw = parseFloat(cs['border' + side + 'Width']);
      if (bw > 0 && cs['border' + side + 'Style'] !== 'none') { const c = rgba(cs['border' + side + 'Color']); if (c && c.a > 0.1) { const len = (side === 'Top' || side === 'Bottom') ? w : h; S(hex(c)).border += bw * len; vote('border', hex(c), bw * len); } }
    }
    if (el instanceof SVGGraphicsElement && tag !== 'svg') {
      for (const p of ['fill', 'stroke']) { const c = rgba(cs[p]); if (c && c.a > 0.1) S(hex(c)).svg += Math.min(area, 4000); }
    }
    // button-like / CTA elements vote for primary
    const cls = el.className && el.className.baseVal == null ? String(el.className) : '';
    const btnLike = tag === 'button' || el.getAttribute('role') === 'button' || (tag === 'input' && /submit|button/.test(el.type)) ||
      (tag === 'a' && (/\b(btn|button|cta)\b/i.test(cls) || (bgc && bgc.a > 0.9 && parseFloat(cs.paddingLeft) >= 8)));
    if (btnLike && bgc && bgc.a > 0.9 && w < vw * 0.6 && h < 120) {
      const h1 = hex(bgc); if (h1 !== effectiveBg(el.parentElement)) {
        const wt = area * (fold ? 3 : 1) * (/primary|cta|brand/i.test(cls) ? 2 : 1);
        vote('primary', h1, wt); const tc = rgba(cs.color); if (tc) vote('onPrimary', h1 + '|' + hex(tc), wt);
      }
    }
  }

  // custom properties on :root that resolve to colours
  const vars = {};
  const rcs = getComputedStyle(document.documentElement);
  for (let i = 0; i < rcs.length; i++) {
    const p = rcs[i]; if (!p.startsWith('--')) continue;
    const v = rcs.getPropertyValue(p).trim();
    if (!v || v.length > 80 || /^[\d.]+(px|rem|em|%|s|ms)?$/.test(v) || /^(none|inherit|initial|auto)$/.test(v)) continue;
    const c = rgba(v); if (c) vars[p] = { value: v, hex: hex(c), alpha: +c.a.toFixed(3) };
  }
  const inlineCss = [...document.querySelectorAll('style')].map(s => s.textContent).join('\n');

  const ff = sel => { const e = document.querySelector(sel); return e ? getComputedStyle(e).fontFamily : null; };
  const faces = []; document.fonts.forEach(f => { if (f.status === 'loaded') faces.push(`${f.family.replace(/"/g, '')} ${f.weight} ${f.style}`); });
  const fonts = { body: ff('body'), paragraph: ff('main p, article p, p'), heading: ff('h1') || ff('h2'), button: ff('button, .btn, a[class*=button]'), code: ff('code, pre'), loaded: [...new Set(faces)].slice(0, 20) };
  // where the heading and body families are used (apply rewrites these families; selectors are evidence for the user)
  {
    const first = s => (s || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
    const sel = e => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/)[0] : '');
    const roles = {};
    for (const [role, stack] of [['display', fonts.heading], ['text', fonts.paragraph || fonts.body]]) {
      if (!stack) continue;
      const fam = first(stack); const used = new Map();
      for (const e of document.querySelectorAll('body *')) {
        if (![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
        const cs = getComputedStyle(e);
        if (first(cs.fontFamily) !== fam || !e.getClientRects().length) continue;
        const k = sel(e); used.set(k, (used.get(k) || 0) + 1);
        if (used.size > 40) break;
      }
      const h1 = document.querySelector('h1');
      roles[role] = { family: fam, stack, weight: role === 'display' && h1 ? +getComputedStyle(h1).fontWeight : 400, selectors: [...used.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([k]) => k) };
    }
    fonts.roles = roles;
  }

  // logo: img / svg / text / css-background candidates near the top, scored (logoFinder)
  let logo = null;
  const found = LOGO ? eval(LOGO)() : null;
  if (found) {
    const e = found.el, r = e.getBoundingClientRect();
    e.setAttribute('data-bi-site-logo', '1');
    logo = { kind: found.kind, tag: e.tagName.toLowerCase(), src: e.currentSrc || e.src || null, alt: e.getAttribute('alt'), text: found.kind === 'text' ? (e.innerText || '').trim().slice(0, 60) : undefined, score: found.score, box: { x: r.x, y: r.y + scrollY, w: r.width, h: r.height } };
    if (logo.kind === 'svg') logo.svgColors = [...new Set([...e.querySelectorAll('*')].flatMap(n => { const s = getComputedStyle(n); return [s.fill, s.stroke]; }).map(rgba).filter(c => c && c.a > 0.1).map(hex))];
  }
  const favicon = (document.querySelector('link[rel~="icon"]') || {}).href || null;

  const meta = n => (document.querySelector(`meta[name="${n}"], meta[property="${n}"]`) || {}).content || null;
  const signals = {
    title: document.title, description: meta('description') || meta('og:description'), siteName: meta('og:site_name'),
    lang: document.documentElement.lang || null, themeColor: meta('theme-color'), colorScheme: meta('color-scheme'), generator: meta('generator'),
    h1: [...document.querySelectorAll('h1')].map(h => h.innerText.trim()).filter(Boolean).slice(0, 3),
    headings: [...document.querySelectorAll('h2')].map(h => h.innerText.trim()).filter(t => t && t.length < 90).slice(0, 8),
    nav: [...document.querySelectorAll('nav a, header a')].map(a => a.innerText.trim()).filter(t => t && t.length < 30).slice(0, 15),
    darkModeSupport: [...document.styleSheets].some(s => { try { return [...s.cssRules].some(r => r.media && /prefers-color-scheme:\s*dark/.test(r.media.mediaText)); } catch { return false; } }) || /(^|[\s,}>])(html)?\.dark[\s{,.:\[]|\[data-(bs-)?theme=["']?dark|prefers-color-scheme:\s*dark/.test(inlineCss),
  };
  const ser = m => [...m.entries()].map(([k, v]) => [k, v.w, v.n]).sort((a, b) => b[1] - a[1]).slice(0, 12);
  return { url: location.href, widgetsSkipped: widgetCount, viewport: { w: vw, h: vh, docH }, stats: [...stats.values()], votes: Object.fromEntries(Object.entries(votes).map(([k, m]) => [k, ser(m)])), pageBg, vars, inlineCss, fonts, logo, favicon, signals };
}

// ---------------------------------------------------------------- roles + confidence (Node)
const r2 = x => +(+x).toFixed(2);
const chromaOf = h => { const c = C.parse(h); return c ? C.toOklch(c).C : 0; };
const hueOf = h => { const c = C.parse(h); return c ? C.toOklch(c).h : 0; };
const near = (a, b, d = 0.03) => a && b && C.deltaE(C.parse(a), C.parse(b)) < d;

// confidence 0..1 from how clearly the winner beats the rest and how much evidence there is
function confidence({ dominance = 0, n = 0, nSat = 8, bonus = 0, cap = 0.97, fallback = false }) {
  const v = 0.15 + 0.5 * Math.max(0, Math.min(1, dominance)) + 0.35 * Math.min(1, n / nSat) + bonus;
  return r2(Math.max(0.05, Math.min(fallback ? 0.35 : cap, v)));
}
const level = c => (c >= 0.75 ? 'high' : c >= 0.5 ? 'medium' : 'low');

// ---- named tokens
const IGNORE_TOKEN = /^--(tw-|docsearch-|shiki|hljs|prism|vp-code|code-|syntax|chart|data-|scrollbar|selection|shadow|swiper|slick)|whatsapp|intercom|crisp|zendesk|cookie|consent/i;
const TOKEN_PREFIX = /^(color|colors|clr|col|c|theme|ui|site|app|bs|vp-c|md-sys-color|mantine-color|chakra-colors|pico|wp--preset--color-|sys|global|semantic|token|palette)-/;
const ROLE_WORDS = new Set(['accent', 'primary', 'brand', 'text', 'bg', 'background', 'surface', 'link', 'border', 'focus', 'muted', 'on', 'card', 'success', 'warning', 'danger', 'info', 'error', 'line', 'fg', 'ring']);
const TOKEN_ROLES = { // per role, in priority order (earlier = stronger)
  background: ['bg', 'background', 'page-bg', 'bg-page', 'body-bg', 'bg-body', 'canvas', 'bg-base', 'bg-default', 'background-default', 'paper', 'app-bg', 'bg-1'],
  surface: ['surface', 'surface-1', 'card', 'card-bg', 'bg-card', 'panel', 'bg-soft', 'bg-alt', 'bg-elevated', 'bg-surface', 'surface-default', 'popover', 'bg-2'],
  text: ['text', 'fg', 'foreground', 'ink', 'body-color', 'body-text', 'text-body', 'text-primary', 'text-default', 'text-1', 'fg-default', 'text-base', 'on-background', 'text-color'],
  textMuted: ['muted', 'text-muted', 'muted-foreground', 'fg-muted', 'text-secondary', 'text-2', 'text-subtle', 'text-soft', 'secondary-color', 'subtle', 'text-dim', 'on-surface-variant'],
  primary: ['primary', 'brand', 'brand-primary', 'brand-1', 'primary-color', 'primary-500', 'brand-500', 'primary-600', 'brand-600'],
  onPrimary: ['on-primary', 'primary-foreground', 'primary-fg', 'primary-contrast', 'text-on-primary', 'on-brand', 'brand-foreground'],
  accent: ['accent', 'accent-1', 'accent-color', 'highlight'],
  onAccent: ['on-accent', 'accent-foreground', 'accent-fg', 'accent-contrast'],
  link: ['link', 'link-color', 'text-link', 'link-fg'],
  focus: ['focus', 'focus-ring', 'ring', 'focus-color', 'focus-outline'],
  border: ['border', 'border-color', 'line', 'divider', 'hairline', 'border-default', 'separator', 'outline-variant'],
};
function tokenCores(name) {
  const full = name.replace(/^--/, '').toLowerCase();
  const out = new Map([[full, 0]]);
  let m = full;
  for (let i = 0; i < 3; i++) { const x = m.replace(TOKEN_PREFIX, ''); if (x === m) break; m = x; out.set(m, 0); }
  for (const base of [full, m]) { // a short project prefix (--kds-text): strip it unless it is itself a role word
    const segs = base.split('-');
    if (segs.length > 1 && segs[0].length <= 5 && !ROLE_WORDS.has(segs[0])) out.set(segs.slice(1).join('-'), 0.5);
  }
  return out;
}
function namedTokenRoles(vars, usage) {
  const best = {};
  for (const [name, v] of Object.entries(vars)) {
    if (IGNORE_TOKEN.test(name) || !(v.alpha > 0.9)) continue;
    const used = Object.values(usage[name] || {}).reduce((a, b) => a + b, 0);
    for (const [core, penalty] of tokenCores(name)) {
      for (const [role, list] of Object.entries(TOKEN_ROLES)) {
        const i = list.indexOf(core); if (i < 0) continue;
        const rank = i + penalty, b = best[role];
        if (!b || rank < b.rank || (rank === b.rank && used > b.used)) best[role] = { name, hex: v.hex, rank, used };
      }
    }
  }
  return best;
}
// where each custom property is used in CSS (background / text / border / svg / shadow)
function cssUsage(raw, cssTexts) {
  const usage = {};
  const allCss = cssTexts.join('\n') + '\n' + raw.inlineCss;
  for (const m of allCss.matchAll(/(?:^|[{;\s])(-?[a-z][\w-]*)\s*:\s*([^;{}]*var\(--[^;{}]*)/gi)) {
    const prop = m[1].toLowerCase(); const kind = /^background/.test(prop) ? 'background' : prop === 'color' ? 'text' : /^(border|outline)/.test(prop) ? 'border' : /^(fill|stroke)$/.test(prop) ? 'svg' : /shadow/.test(prop) ? 'shadow' : null;
    if (!kind) continue;
    for (const v of m[2].matchAll(/var\((--[\w-]+)/g)) { usage[v[1]] ??= {}; usage[v[1]][kind] = (usage[v[1]][kind] || 0) + 1; }
  }
  return usage;
}

function buildRoles(raw, cssTexts) {
  // 1) cluster near-identical colours (OKLab ΔE < 0.02) into the heaviest representative
  const VA = raw.viewport.w * raw.viewport.h, DA = raw.viewport.w * raw.viewport.docH;
  const items = raw.stats.map(s => ({ ...s, rgb: C.parse(s.hex) })).filter(s => s.rgb);
  const score = s => Math.max(0, s.bg) / VA * 100 + s.text * 0.05 + s.border * 0.002 + s.svg * 0.0005 + s.gradient / VA * 50;
  items.sort((a, b) => score(b) - score(a));
  const clusters = [], rep = new Map();
  for (const it of items) {
    const c = clusters.find(k => C.deltaE(k.rgb, it.rgb) < 0.01);
    if (c) { for (const f of ['bg', 'text', 'border', 'svg', 'gradient', 'fold', 'n']) c[f] += it[f]; c.members.push(it.hex); rep.set(it.hex, c.hex); }
    else { clusters.push({ ...it, members: [it.hex] }); rep.set(it.hex, it.hex); }
  }
  const R = h => rep.get(h) || h;
  const byHex = h => clusters.find(c => c.hex === h);
  // ranked vote table for one role: [{hex, w, n}] merged by cluster
  const ranked = (k, filter = () => true, keyPart = 0) => {
    const m = new Map();
    for (const [key, w, n] of raw.votes[k]) { const hx = R(key.split('|')[keyPart]); if (!filter(hx)) continue; const v = m.get(hx) || { hex: hx, w: 0, n: 0 }; v.w += w; v.n += n; m.set(hx, v); }
    return [...m.values()].sort((a, b) => b.w - a.w);
  };
  const roles = {}, details = {}, notes = [];
  const set = (role, hex, conf, evidence) => { if (!hex) return; roles[role] = hex; details[role] = { hex, confidence: conf, level: level(conf), ...evidence }; };
  const fromRanked = (role, list, method, nSat = 8) => {
    if (!list.length) return null;
    const tot = list.reduce((s, x) => s + x.w, 0) || 1;
    const top = list[0];
    set(role, top.hex, confidence({ dominance: top.w / tot, n: top.n, nSat }), {
      method, elements: top.n, share: r2(top.w / tot),
      alternatives: list.slice(1, 4).map(x => ({ hex: x.hex, share: r2(x.w / tot), elements: x.n })),
    });
    return top.hex;
  };

  // Named custom properties (--bg, --surface, --text, --muted, --primary, --brand, --link, --border, --ring …, with
  // framework prefixes stripped) are strong evidence of intent: when such a token is used or visible on the page it
  // wins over the measurement. Framework internals (--tw-*), widgets and chart/code palettes are ignored.
  const tokenUse = cssUsage(raw, cssTexts);
  const tokenRoles = namedTokenRoles(raw.vars, tokenUse);
  const visibleAs = (hx, role) => clusters.some(c => C.deltaE(c.rgb, C.parse(hx)) < 0.03 && (
    /^(background|surface)$/.test(role) ? c.bg > 0 : /^(text|textMuted|link|onPrimary|onAccent)$/.test(role) ? c.text > 0 : role === 'border' ? c.border > 0 : (c.bg > 0 || c.text > 0 || c.border > 0 || c.svg > 0 || c.gradient > 0)));
  function applyTokens(keys) {
    for (const k of keys) {
      const t = tokenRoles[k]; if (!t) continue;
      const shown = visibleAs(t.hex, k);
      if (!shown && !t.used) continue;
      const cur = details[k];
      if (/^(primary|accent|link|focus)$/.test(k) && chromaOf(t.hex) < 0.04 && !(k === 'primary' && shown)) continue;
      const rep = t.hex; // the token's own value: clustering may have merged it with a neighbour
      const same = cur && C.deltaE(C.parse(cur.hex), C.parse(rep)) < 0.03;
      const alts = cur && !same ? [{ hex: cur.hex, source: `measured (${cur.method})` }, ...(cur.alternatives || [])].slice(0, 3) : (cur ? cur.alternatives : []);
      const conf = Math.min(0.97, Math.max(same ? cur.confidence + 0.05 : 0, shown ? 0.85 : 0.7));
      set(k, rep, r2(conf),
        { method: `named token ${t.name}${shown ? '' : ' (declared, not seen on this page)'}`, elements: cur ? cur.elements : 0, share: cur ? cur.share : null, corroboration: same ? [...(cur.corroboration || []), 'measurement agrees'] : [], alternatives: alts || [] });
    }
  }

  // background / surface: area
  const byBg = clusters.filter(c => c.bg > 0).sort((a, b) => b.bg - a.bg);
  const bgTot = byBg.reduce((s, c) => s + c.bg, 0) || 1;
  if (byBg.length) {
    let b = byBg[0], method = 'largest painted background area', cap = 0.97;
    // a big coloured hero on a short page can out-paint the page ground: prefer a large neutral ground
    const ground = chromaOf(b.hex) >= 0.06 && byBg.find(c => chromaOf(c.hex) < 0.04 && c.bg >= 0.4 * b.bg);
    if (ground) { notes.push(`background: coloured area ${b.hex} is larger than the neutral ground ${ground.hex}; ${ground.hex} taken as background, ${b.hex} offered as primary surface. Confirm with the user.`); b = ground; method = 'largest neutral background area (a coloured area is larger)'; cap = 0.6; }
    set('background', b.hex, confidence({ dominance: b.bg / bgTot, n: 6, nSat: 6, cap }), { method, elements: b.n, share: r2(b.bg / bgTot), alternatives: byBg.filter(c => c.hex !== b.hex).slice(0, 3).map(c => ({ hex: c.hex, share: r2(c.bg / bgTot), elements: c.n })) });
  } else set('background', raw.pageBg, 0.3, { method: 'page background (no painted areas found)', elements: 1, share: 1, alternatives: [] });
  const surfs = byBg.filter(c => c.hex !== roles.background && chromaOf(c.hex) < 0.05 && C.deltaE(c.rgb, C.parse(roles.background)) < 0.25);
  if (surfs.length) {
    const s = surfs[0], st = surfs.reduce((a, c) => a + c.bg, 0) || 1;
    set('surface', s.hex, confidence({ dominance: s.bg / st, n: s.n, nSat: 6, cap: 0.9 }), { method: 'second neutral background close to the page background', elements: s.n, share: r2(s.bg / bgTot), alternatives: surfs.slice(1, 3).map(c => ({ hex: c.hex, share: r2(c.bg / bgTot), elements: c.n })) });
    if (surfs[1] && surfs[1].bg / bgTot > 0.01) set('surfaceAlt', surfs[1].hex, 0.4, { method: 'third neutral background', elements: surfs[1].n, share: r2(surfs[1].bg / bgTot), alternatives: [] });
  }

  // text / textMuted: copy painted on the page ground (or a neutral surface close to it), weighted by
  // chars x size x contrast; text that is not readable on the background (white hero text) never becomes "text".
  const bgHex = roles.background, bgL = C.toOklch(C.parse(bgHex)).L;
  const ctr = hx => C.contrast(C.parse(hx), C.parse(bgHex));
  const onGround = g => { const gc = C.parse(R(g)); if (!gc) return false; const o = C.toOklch(gc); return o.C < 0.04 && Math.abs(o.L - bgL) < 0.15; };
  const tv = new Map();
  for (const [key, w, n] of raw.votes.text) {
    const [g, f] = key.split('|'); if (!f || !onGround(g)) continue;
    const hx = R(f); if (!C.parse(hx)) continue;
    const v = tv.get(hx) || { hex: hx, w: 0, n: 0 }; v.w += w; v.n += n; tv.set(hx, v);
  }
  const copy = [...tv.values()];
  const textCands = copy.filter(v => ctr(v.hex) >= 3).map(v => ({ ...v, w: v.w * Math.min(1, ctr(v.hex) / 7) })).sort((a, b) => b.w - a.w);
  if (!fromRanked('text', textCands, 'body copy on the page ground (chars x size x contrast)', 10)) {
    const t = [...clusters].filter(c => c.text > 0 && ctr(c.hex) >= 3).sort((a, b) => b.text - a.text)[0];
    if (t) set('text', t.hex, confidence({ fallback: true, dominance: 1, n: t.n }), { method: 'fallback: most readable text overall', elements: t.n, share: null, alternatives: [] });
  }
  const headingList = ranked('heading', h => ctr(h) >= 3);
  const heading = headingList[0] && headingList[0].hex !== roles.text ? headingList[0].hex : null;
  fromRanked('link', ranked('link', h => ctr(h) >= 2), 'links inside body copy', 6);
  fromRanked('border', ranked('border', h => chromaOf(h) < 0.05 && h !== roles.text), 'border length (width x length)', 10);
  const txL = roles.text ? C.toOklch(C.parse(roles.text)).L : 0;
  const muted = copy.filter(v => v.hex !== roles.text && chromaOf(v.hex) < 0.04 && ctr(v.hex) >= 2 && Math.abs(C.toOklch(C.parse(v.hex)).L - bgL) > 0.2 && (C.toOklch(C.parse(v.hex)).L - txL) * (bgL - txL) > 0.03).sort((a, b) => b.w - a.w);
  if (muted.length) { const mt = muted.reduce((s, c) => s + c.w, 0) || 1; set('textMuted', muted[0].hex, confidence({ dominance: muted[0].w / mt, n: muted[0].n, nSat: 10, cap: 0.85 }), { method: 'second neutral copy colour between text and background', elements: muted[0].n, share: r2(muted[0].w / mt), alternatives: muted.slice(1, 3).map(c => ({ hex: c.hex, share: r2(c.w / mt), elements: c.n })) }); }
  applyTokens(['background', 'surface', 'text', 'textMuted', 'link', 'border']);

  // primary / accent: button votes vs large chromatic brand surface vs theme-color vs named tokens
  const themeColor = raw.signals.themeColor && C.parse(raw.signals.themeColor) ? C.toHex(C.parse(raw.signals.themeColor)) : null;
  const tokenBrand = Object.entries(raw.vars).filter(([n, v]) => /brand|primary/i.test(n) && v.alpha > 0.9 && chromaOf(v.hex) >= 0.04).map(([n, v]) => ({ name: n, hex: v.hex }));
  const btn = ranked('primary', h => chromaOf(h) >= 0.04);
  const btnTot = btn.reduce((s, x) => s + x.w, 0) || 1;
  const surfaceCands = clusters.filter(c => chromaOf(c.hex) >= 0.06 && c.bg > 0 && (c.bg / DA >= 0.04 || c.fold / VA >= 0.12)).sort((a, b) => b.bg - a.bg);
  const brandSurface = surfaceCands[0] || null;
  const corroborate = hx => {
    const out = [];
    if (themeColor && near(hx, themeColor, 0.08)) out.push(`meta theme-color ${themeColor}`);
    const tk = tokenBrand.filter(t => near(hx, t.hex, 0.03)).map(t => t.name).slice(0, 3);
    if (tk.length) out.push(`token ${tk.join(', ')}`);
    return out;
  };
  let primary = null, accentFromCta = null, pMethod = '', pDominance = 0, pN = 0;
  if (btn.length) { primary = btn[0].hex; pMethod = 'button-like elements (area, above-the-fold x3)'; pDominance = btn[0].w / btnTot; pN = btn[0].n; }
  if (brandSurface && (!primary || C.hueDist(hueOf(brandSurface.hex), hueOf(primary)) > 30)) {
    const surfShare = brandSurface.bg / DA;
    const surfBacked = corroborate(brandSurface.hex).length > 0;
    const btnBacked = primary ? corroborate(primary).length > 0 : false;
    if (!primary || (surfBacked && !btnBacked) || (surfShare >= 0.08 && !btnBacked)) {
      if (primary) { accentFromCta = primary; notes.push(`primary/accent ambiguous: large brand surface ${brandSurface.hex} (${(surfShare * 100).toFixed(1)}% of page area) taken as primary, CTA colour ${primary} as accent. Confirm with the user.`); }
      primary = brandSurface.hex; pMethod = 'largest chromatic background surface'; pDominance = primary && btn.length ? 0.45 : 0.6; pN = brandSurface.n;
    } else notes.push(`primary/accent ambiguous: CTA colour ${primary} taken as primary; large brand surface ${brandSurface.hex} (${(surfShare * 100).toFixed(1)}% of page area) is a strong alternative. Confirm with the user.`);
  }
  if (!primary) {
    const ch = clusters.filter(c => chromaOf(c.hex) >= 0.06 && c.hex !== roles.link).sort((a, b) => score(b) - score(a))[0];
    primary = ch?.hex || roles.link || null; pMethod = ch ? 'fallback: heaviest chromatic colour' : 'fallback: link colour';
  }
  if (primary) {
    const cor = corroborate(primary);
    const ambiguous = notes.some(n => n.startsWith('primary/accent'));
    let conf = confidence({ dominance: pDominance, n: pN, nSat: 6, bonus: cor.length ? 0.12 : 0, fallback: pMethod.startsWith('fallback') });
    if (ambiguous) conf = Math.min(conf, 0.5);
    const alts = [...btn.filter(x => x.hex !== primary).slice(0, 2).map(x => ({ hex: x.hex, share: r2(x.w / btnTot), elements: x.n, source: 'buttons' })),
      ...surfaceCands.filter(c => c.hex !== primary).slice(0, 2).map(c => ({ hex: c.hex, share: r2(c.bg / DA), elements: c.n, source: 'chromatic surface' }))];
    if (themeColor && !near(themeColor, primary, 0.08) && chromaOf(themeColor) >= 0.04) alts.push({ hex: themeColor, source: 'meta theme-color' });
    set('primary', primary, r2(conf), { method: pMethod, elements: pN, share: r2(pDominance), corroboration: cor, alternatives: alts });
  }
  // text on a coloured fill: what text is actually painted over that background
  const onColour = (role, fill) => {
    if (!fill) return;
    const m = new Map();
    const add = (votes, div) => { for (const [key, w, n] of votes) { const [b, t] = key.split('|'); if (R(b) !== fill) continue; const hx = R(t); const v = m.get(hx) || { hex: hx, w: 0, n: 0 }; v.w += w / div; v.n += n; m.set(hx, v); } };
    add(raw.votes.textOn, 1); add(raw.votes.onPrimary, 200); // button votes are area-weighted, text votes char-weighted
    const list = [...m.values()].sort((a, b) => b.w - a.w);
    if (list.length) fromRanked(role, list, `text painted on ${fill}`, 4);
  };
  applyTokens(['primary']);
  onColour('onPrimary', roles.primary);
  // accent: CTA colour displaced by a brand surface, else heaviest chromatic colour >= 30° from primary
  if (accentFromCta) {
    const b = btn.find(x => x.hex === accentFromCta);
    set('accent', accentFromCta, Math.min(0.5, confidence({ dominance: b ? b.w / btnTot : 0.5, n: b ? b.n : 1, nSat: 6 })), { method: 'call-to-action buttons (displaced from primary)', elements: b ? b.n : 1, share: b ? r2(b.w / btnTot) : null, alternatives: [] });
  } else if (roles.primary) {
    const ph = hueOf(roles.primary);
    const acc = clusters.filter(c => chromaOf(c.hex) > 0.08 && C.hueDist(C.toOklch(c.rgb).h, ph) > 30 && c.hex !== roles.link).sort((a, b) => score(b) - score(a));
    if (acc.length) { const at = acc.reduce((s, c) => s + score(c), 0) || 1; set('accent', acc[0].hex, confidence({ dominance: score(acc[0]) / at, n: acc[0].n, nSat: 8, cap: 0.75 }), { method: 'heaviest chromatic colour >= 30° hue from primary', elements: acc[0].n, share: r2(score(acc[0]) / at), alternatives: acc.slice(1, 3).map(c => ({ hex: c.hex, share: r2(score(c) / at), elements: c.n })) }); }
  }
  onColour('onAccent', roles.accent);
  applyTokens(['primary', 'onPrimary', 'accent', 'onAccent', 'focus']);

  const usage = tokenUse;
  // status / focus roles: only from token names (no reliable visual signal on a marketing page)
  const NAMED_ROLES = { danger: /danger|error|destructive|critical/i, success: /success|positive/i, warning: /warning|caution|\bwarn\b/i, info: /(^|-)info(-|$)/i, focus: /focus|ring/i };
  for (const [role, re] of Object.entries(NAMED_ROLES)) {
    if (roles[role]) continue;
    const cands = Object.entries(raw.vars).filter(([n, v]) => re.test(n) && !IGNORE_TOKEN.test(n) && v.alpha > 0.9 && !/soft|bg|light|subtle|border|text|hover|active/i.test(n) && (role === 'focus' || chromaOf(v.hex) >= 0.04));
    if (!cands.length) continue;
    cands.sort((a, b) => Object.values(usage[b[0]] || {}).reduce((s, x) => s + x, 0) - Object.values(usage[a[0]] || {}).reduce((s, x) => s + x, 0) || a[0].length - b[0].length);
    const [name, v] = cands[0];
    set(role, v.hex, usage[name] ? 0.45 : 0.35, { method: `token name ${name}`, elements: 0, share: null, alternatives: cands.slice(1, 3).map(([n, x]) => ({ hex: x.hex, source: n })) });
  }

  const tokens = {};
  for (const [name, v] of Object.entries(raw.vars)) {
    const rgb = C.parse(v.hex); const matched = Object.entries(roles).filter(([, h]) => v.alpha > 0.9 && C.deltaE(rgb, C.parse(h)) < 0.02).map(([k]) => k);
    tokens[name] = { ...v, usedAs: usage[name] || {}, roles: matched };
  }
  const colorTokenCount = Object.keys(tokens).length;
  const usedTokenCount = Object.values(tokens).filter(t => Object.keys(t.usedAs).length).length;
  const palette = clusters.filter(c => score(c) > 0.05).slice(0, 24).map(c => {
    const o = C.toOklch(c.rgb);
    return { hex: c.hex, oklch: `oklch(${(o.L * 100).toFixed(1)}% ${o.C.toFixed(3)} ${o.h.toFixed(0)})`, score: r2(score(c)), areaShare: +(Math.max(0, c.bg) / DA).toFixed(4), uses: { bg: Math.round(Math.max(0, c.bg)), text: Math.round(c.text), border: Math.round(c.border), svg: Math.round(c.svg), gradient: Math.round(c.gradient) }, elements: c.n, members: c.members.length > 1 ? c.members : undefined, roles: Object.entries(roles).filter(([, h]) => h === c.hex).map(([k]) => k) };
  });
  const contrast = {};
  const pc = (a, b) => (roles[a] && roles[b] ? r2(C.contrast(C.parse(roles[a]), C.parse(roles[b]))) : undefined);
  for (const [a, b] of [['text', 'background'], ['textMuted', 'background'], ['link', 'background'], ['onPrimary', 'primary'], ['onAccent', 'accent'], ['primary', 'background']]) { const v = pc(a, b); if (v !== undefined) contrast[`${a}/${b}`] = v; }
  const ordered = Object.fromEntries(ROLE_KEYS.filter(k => roles[k]).map(k => [k, roles[k]]));
  const orderedDetails = Object.fromEntries(ROLE_KEYS.filter(k => details[k]).map(k => [k, details[k]]));
  const confirm = Object.entries(orderedDetails).filter(([, d]) => d.confidence < 0.6).map(([k, d]) => `${k} ${d.hex} (${d.level}, ${d.method})`);
  return {
    strategy: colorTokenCount >= 6 && usedTokenCount >= 4 ? 'tokens' : 'computed',
    roles: ordered, roleDetails: orderedDetails, extraRoles: heading ? { heading } : {},
    missingRoles: ROLE_KEYS.filter(k => !roles[k]), confirm, notes, contrast, palette,
    tokens: { count: colorTokenCount, used: usedTokenCount, items: tokens },
  };
}

// ---------------------------------------------------------------- commands
// dark-scheme CSS: media query, .dark / [data-theme=dark] / [data-bs-theme=dark] / .theme-dark selectors
const DARK_CSS = /prefers-color-scheme:\s*dark|(^|[\s,}>])(html)?\.dark[\s{,.:\[]|\[data-(bs-)?theme=["']?dark|\.theme-dark\b/;

// A safe standalone copy of an inline-SVG logo: computed fill / stroke written as attributes, <style> (media
// queries, selectors that would leak into a page), class and style attributes removed, sprite <use> targets copied.
function pageLogoCopy() {
  const el = document.querySelector('[data-bi-site-logo]');
  if (!el || el.tagName.toLowerCase() !== 'svg') return null;
  // any CSS colour (rgb(), oklch(), color(), names) -> #rrggbb (+ alpha) through a 1px canvas; url()/none kept
  const cx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const hex = c => {
    if (!c || /^(none|transparent)$|url\(/i.test(c) || c.startsWith('#')) return c;
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = cx.getImageData(0, 0, 1, 1).data;
    const h = '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
    return a < 255 ? { h, a: +(a / 255).toFixed(3) } : h;
  };
  const clone = el.cloneNode(true);
  const src = [el, ...el.querySelectorAll('*')], dst = [clone, ...clone.querySelectorAll('*')];
  const PROPS = ['fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'fill-rule', 'stop-color', 'stop-opacity'];
  for (let i = 0; i < src.length; i++) {
    const s = src[i], d = dst[i]; const tag = s.tagName.toLowerCase();
    if (tag === 'style' || tag === 'script') continue;
    const cs = getComputedStyle(s);
    if (cs.display === 'none') d.setAttribute('display', 'none');
    for (const p of PROPS) {
      if (p.startsWith('stop') !== (tag === 'stop')) continue;
      let v = cs.getPropertyValue(p); if (!v) continue;
      if ((p === 'fill' || p === 'stroke' || p === 'stop-color') && !/^(none|transparent)$|url\(/i.test(v)) { const x = hex(v); if (typeof x === 'object') { d.setAttribute(p, x.h); d.setAttribute(p === 'stop-color' ? 'stop-opacity' : `${p}-opacity`, String(x.a)); continue; } v = x; }
      if (p === 'stroke-width') v = String(parseFloat(v));
      if ((p === 'opacity' || p.endsWith('-opacity')) && +v === 1) continue;
      d.setAttribute(p, v);
    }
    d.removeAttribute('class'); d.removeAttribute('style');
    for (const a of [...d.attributes]) if (/^data-b[ci]-/.test(a.name)) d.removeAttribute(a.name);
  }
  for (const n of clone.querySelectorAll('style, script')) n.remove();
  // sprite: <use href="#id"> pointing outside the logo
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  for (const u of clone.querySelectorAll('use')) {
    const id = (u.getAttribute('href') || u.getAttribute('xlink:href') || '').replace(/^#/, '');
    if (id && !clone.querySelector(`[id="${CSS.escape(id)}"]`)) { const t = document.getElementById(id); if (t) defs.appendChild(t.cloneNode(true)); }
  }
  if (defs.childNodes.length) clone.insertBefore(defs, clone.firstChild);
  const r = el.getBoundingClientRect();
  if (!clone.getAttribute('viewBox')) clone.setAttribute('viewBox', `0 0 ${r.width} ${r.height}`);
  clone.setAttribute('width', String(Math.round(r.width))); clone.setAttribute('height', String(Math.round(r.height)));
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return new XMLSerializer().serializeToString(clone);
}

// Near-empty page (the site lives under a sub-path, an SPA that rendered nothing): little visible text or a
// one-colour screenshot. Measured, so the user hears it instead of getting roles from a blank page.
function emptyPageReason(textChars, imagePal) {
  const top = imagePal && imagePal[0] ? imagePal[0].share : 0;
  if (textChars < 50) return `only ${textChars} visible characters`;
  if (top >= 0.98) return `the screenshot is one colour (${Math.round(top * 100)}% ${imagePal[0].hex})`;
  return null;
}

// Roles the user (or the agent, after checking) corrected in a previous extract.json survive a re-extract:
// a role counts as corrected when roles[k] differs from the measured roleDetails[k].hex, or when the file says
// "rolesConfirmed": true (then every role is kept).
function correctedRoles(prev, rolesKey, detailsKey) {
  const roles = (prev && prev[rolesKey]) || {}, det = (prev && prev[detailsKey]) || {};
  const keep = {};
  for (const [k, v] of Object.entries(roles)) {
    if (!v) continue;
    if (prev.rolesConfirmed || !det[k] || String(det[k].hex).toLowerCase() !== String(v).toLowerCase()) keep[k] = v;
  }
  return keep;
}

// In-page recolouring for what the network rewrite can't see: <style> elements (incl. CSS-in-JS
// insertRule), style="" attributes, SVG presentation attributes. Re-runs on DOM mutations.
function pageRecolor({ LIB, oldRoles, newRoles, keepLogo, scales }) {
  const L = eval(LIB); const map = L.makeMapper(oldRoles, newRoles, { scales });
  const done = window.__biDone || (window.__biDone = new WeakSet());
  const skip = el => keepLogo && el.closest && el.closest('[data-bi-site-logo], [class*="logo" i], #logo');
  function walk(rules) {
    for (const r of rules) {
      if (done.has(r)) continue; done.add(r);
      if (r.style) for (let i = 0; i < r.style.length; i++) { const p = r.style[i]; if (!L.COLOR_PROP.test(p)) continue; const v = r.style.getPropertyValue(p); const nv = L.rewriteValue(v, map); if (nv !== v) r.style.setProperty(p, nv, r.style.getPropertyPriority(p)); }
      if (r.cssRules) walk(r.cssRules);
    }
  }
  function pass() {
    for (const s of document.styleSheets) { if (s.ownerNode && s.ownerNode.tagName === 'LINK') continue; try { walk(s.cssRules); } catch { /* cross-origin */ } }
    for (const s of document.adoptedStyleSheets || []) { try { walk(s.cssRules); } catch { /* ignore */ } }
    for (const el of document.querySelectorAll('[style]')) {
      if (skip(el) || el.__bi === el.getAttribute('style')) continue;
      for (let i = 0; i < el.style.length; i++) { const p = el.style[i]; if (!L.COLOR_PROP.test(p)) continue; const v = el.style.getPropertyValue(p); const nv = L.rewriteValue(v, map); if (nv !== v) el.style.setProperty(p, nv, el.style.getPropertyPriority(p)); }
      el.__bi = el.getAttribute('style');
    }
    for (const el of document.querySelectorAll('[fill],[stroke],[stop-color]')) {
      if (skip(el) || el.__biA) continue; el.__biA = 1;
      for (const a of ['fill', 'stroke', 'stop-color']) { const v = el.getAttribute(a); if (v) { const nv = L.rewriteValue(v, map); if (nv !== v) el.setAttribute(a, nv); } }
    }
  }
  // UA defaults are never declared (white canvas, black text, light form controls): zero-specificity base.
  if (!document.querySelector('style[data-bi-base]')) {
    const tr = c => !c || /rgba\(0, 0, 0, 0\)|transparent/.test(c);
    const noBg = tr(getComputedStyle(document.documentElement).backgroundColor) && tr(document.body && getComputedStyle(document.body).backgroundColor);
    const fmt = c => L.format(map(L.parse(c)), 1);
    const bg = map(L.parse('#ffffff')); const dark = L.toOklch(bg).L < 0.5;
    const st = document.createElement('style'); st.setAttribute('data-bi-base', '');
    st.textContent = `:where(html){color:${fmt('#000000')};${noBg ? `background-color:${L.format(bg, 1)};` : ''}color-scheme:${dark ? 'dark' : 'light'}}`;
    document.head.prepend(st); done.add(st.sheet);
    for (const r of st.sheet.cssRules) done.add(r);
  }
  pass();
  if (!window.__biObs) { let t; window.__biObs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(pass, 50); }); window.__biObs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'class'] }); }
}

// In-page measuring helpers shared by the baseline pass and the guard (stringified, eval'd in the page).
// bgOf() returns the colours actually behind an element's text, or null when that cannot be measured:
// an ancestor painting an image (url(), image-set) or a translucent layer stops the climb instead of
// skipping it (a dark hero with `url(topo.svg), linear-gradient(...)` must never be measured as the white body).
function guardTools(L) {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
  const rgba = str => { const p = L.parse(str); if (p) return p; cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#0000'; cx.fillStyle = str; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return d[3] ? { r: d[0], g: d[1], b: d[2], a: d[3] / 255 } : null; };
  function bgOf(el) {
    for (let p = el; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      const bi = cs.backgroundImage;
      if (bi && bi !== 'none') {
        if (/url\(|image-set|element\(|cross-fade|paint\(/i.test(bi)) return null; // image behind the text: unmeasurable
        const stops = (bi.match(/(?:rgba?|hsla?|oklch|oklab|lab|lch|color)\([^()]*\)|#[0-9a-f]{3,8}\b/gi) || []).map(rgba).filter(Boolean);
        if (!stops.length || stops.some(c => c.a < 0.9)) return null; // translucent gradient over an unknown ground
        return stops; // pure gradient: judge against every stop
      }
      const c = rgba(cs.backgroundColor);
      if (c && c.a > 0.9) return [c];
      if (c && c.a > 0.1) return null; // translucent fill: the real ground is unknown
    }
    const root = rgba(getComputedStyle(document.documentElement).backgroundColor);
    return [root && root.a > 0.9 ? root : { r: 255, g: 255, b: 255, a: 1 }];
  }
  const worst = (fg, stops) => Math.min(...stops.map(b => L.contrast(fg, b)));
  function pathOf(el) {
    const parts = [];
    for (let p = el; p && p !== document.body && p.parentElement; p = p.parentElement) parts.push(p.tagName + ':' + Array.prototype.indexOf.call(p.parentElement.children, p));
    return parts.reverse().join('/');
  }
  function* textElements() {
    for (const el of document.querySelectorAll('body *')) {
      if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
      const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || !el.getClientRects().length) continue;
      yield [el, cs];
    }
  }
  return { rgba, bgOf, worst, pathOf, textElements };
}
const GUARD_SRC = `(${guardTools.toString()})`;

// Baseline on the ORIGINAL page (no recolouring): contrast per text element, -1 when unmeasurable.
function pageGuardBaseline({ LIB, GUARD }) {
  const L = eval(LIB); const T = eval(GUARD)(L);
  const out = {};
  for (const [el, cs] of T.textElements()) {
    const fg = T.rgba(cs.color); const bg = T.bgOf(el);
    out[T.pathOf(el)] = !fg || !bg || fg.a < 0.3 ? -1 : +T.worst(fg, bg).toFixed(3);
  }
  return out;
}

// Value mapping is context-free: white text on an old dark-primary bar maps to the new *background*
// even when that bar becomes a light primary. Repair text below 3:1 with the best of the new palette's
// text / background / onPrimary / textMuted / onAccent. Only text whose background can be measured, and
// which the SAME measurement found readable (>= 3:1) on the original page, is touched: if the original
// already "fails", the measurement is wrong (image or effect behind the text), not the design.
function pageContrastGuard({ LIB, GUARD, newRoles, baseline }) {
  const L = eval(LIB); const T = eval(GUARD)(L);
  const cands = ['text', 'background', 'onPrimary', 'textMuted', 'onAccent'].map(k => newRoles[k]).filter(Boolean).map(L.parse).filter(Boolean);
  let checked = 0, fixed = 0, skippedUnmeasurable = 0, skippedBaseline = 0; const samples = [];
  for (const [el, cs] of T.textElements()) {
    const fg = T.rgba(cs.color); const bg = T.bgOf(el);
    if (!fg || !bg || fg.a < 0.3) { skippedUnmeasurable++; continue; }
    checked++;
    const now = T.worst(fg, bg);
    if (now >= 3) continue;
    if (baseline) { const base = baseline[T.pathOf(el)]; if (base === undefined || base < 3) { skippedBaseline++; continue; } }
    const best = cands.reduce((a, c) => (T.worst(c, bg) > T.worst(a, bg) ? c : a));
    if (T.worst(best, bg) <= now) continue;
    el.style.setProperty('color', L.toHex(best), 'important'); fixed++;
    if (samples.length < 8) samples.push(`${el.tagName.toLowerCase()} "${el.textContent.trim().slice(0, 24)}" ${L.toHex(fg)}/${bg.map(L.toHex).join('+')} -> ${L.toHex(best)}`);
  }
  return { checked, fixed, skippedUnmeasurable, skippedBaseline, samples };
}
function pageMarkLogo() {
  const e = document.querySelector('header a[href="/"] svg, a[href="/"] svg, [class*="logo" i] svg, #logo svg');
  if (e) e.setAttribute('data-bi-site-logo', '1');
}

// ---------------------------------------------------------------- identity apply: fonts, logo, layout stress
// Font-family rewriting, self-contained so it runs in Node (network CSS) and in the page (CSSOM, inline styles).
function fontLib() {
  const unq = s => String(s).trim().replace(/^["']|["']$/g, '').trim().toLowerCase();
  const first = v => unq(String(v).split(',')[0] || '');
  const HEADISH = /(^|[\s,>+~(.#-])(h[1-6]|[\w-]*(heading|headline|title|display|hero)[\w-]*)\b/i;
  // plan: {display: {name, old}, text: {name, old}} with old = the site's first family for that role (lowercase).
  // Returns famFor(value, context) -> our family name or null. When the site uses one family for both roles,
  // declarations whose selector / custom-property name looks like a heading go to display, the rest to text.
  function makeFamilyMapper(plan) {
    const d = plan.display && plan.display.name ? plan.display : null, t = plan.text && plan.text.name ? plan.text : null;
    const mo = plan.mono && plan.mono.name && plan.mono.old ? plan.mono : null;
    const same = d && t && d.old && d.old === t.old;
    return function famFor(value, ctx = '') {
      const f = first(value);
      if (!f || f.startsWith('__bi_') || /^(var\(|inherit|initial|unset|revert)/.test(f)) return null;
      if (mo && f === mo.old && !(t && t.old === f) && !(d && d.old === f)) return mo.name;
      if (same) return f === t.old ? (HEADISH.test(ctx) ? d.name : t.name) : null;
      if (d && d.old && f === d.old) return d.name;
      if (t && t.old && f === t.old) return t.name;
      return null;
    };
  }
  const prepend = (value, fam) => `"${fam}", ${String(value).trim()}`;
  const SIZE = /^(.*?(?:^|\s)(?:[\d.]+(?:px|pt|em|rem|%|vw|vh|vmin|vmax|ch|ex|cap|lh|q|mm|cm|in|pc)|xx-small|x-small|small|medium|large|x-large|xx-large|xxx-large|smaller|larger|(?:calc|clamp|min|max|var)\([^)]*\))(?:\s*\/\s*[^\s,]+)?\s+)(.+)$/i;
  function rewriteShorthand(value, famFor, ctx) {
    const m = String(value).match(SIZE); if (!m) return value;
    const fam = famFor(m[2], ctx); return fam ? m[1] + prepend(m[2], fam) : value;
  }
  function rewriteDecl(prop, value, famFor, sel) {
    const p = prop.toLowerCase();
    if (p === 'font-family') { const fam = famFor(value, sel); return fam ? prepend(value, fam) : value; }
    if (p === 'font') return rewriteShorthand(value, famFor, sel);
    if (p.startsWith('--')) { const fam = famFor(value, p + ' ' + sel); return fam && /[a-z]/i.test(value) && !/^\s*[\d.]/.test(value) ? prepend(value, fam) : value; }
    return value;
  }
  // Raw CSS text: innermost blocks only; @font-face and other descriptor blocks are never touched.
  function rewriteFontCss(css, famFor) {
    return String(css).replace(/([^{}]*)\{([^{}]*)\}/g, (all, sel, body) => {
      if (/@(font-face|font-feature-values|font-palette-values|property|counter-style|page)\b/i.test(sel)) return all;
      const nb = body.replace(/(^|;)(\s*)(font-family|font|--[\w-]+)(\s*:\s*)([^;]+)/gi, (m, pre, sp, prop, colon, val) => {
        const nv = rewriteDecl(prop, val, famFor, sel); return nv === val ? m : pre + sp + prop + colon + nv;
      });
      return sel + '{' + nb + '}';
    });
  }
  return { first, makeFamilyMapper, rewriteFontCss, rewriteDecl, HEADISH };
}
const F = fontLib();
const FONT_LIB_SRC = `(${fontLib.toString()})()`;

// In-page: rewrite <style> rules, constructed sheets and style="" font families (network sheets are done already).
function pageRewriteFonts({ FONT, plan }) {
  const FL = eval(FONT); const famFor = FL.makeFamilyMapper(plan);
  let n = 0;
  const fix = (style, sel) => {
    for (let i = 0; i < style.length; i++) {
      const p = style[i]; if (p !== 'font-family' && !p.startsWith('--')) continue;
      const v = style.getPropertyValue(p); const nv = FL.rewriteDecl(p, v, famFor, sel || '');
      if (nv !== v) { style.setProperty(p, nv, style.getPropertyPriority(p)); n++; }
    }
  };
  const walk = rules => { for (const r of rules) { if (r.type === 5) continue; if (r.style) fix(r.style, r.selectorText); if (r.cssRules) walk(r.cssRules); } };
  for (const s of document.styleSheets) { if (s.ownerNode && s.ownerNode.tagName === 'LINK') continue; try { walk(s.cssRules); } catch { /* cross-origin */ } }
  for (const s of document.adoptedStyleSheets || []) { try { walk(s.cssRules); } catch { /* ignore */ } }
  for (const el of document.querySelectorAll('[style]')) fix(el.style, el.tagName.toLowerCase() + ' ' + (typeof el.className === 'string' ? el.className : ''));
  return n;
}

// Layout snapshot: per element, horizontal/vertical overflow and the number of lines its own text runs on.
// The first call tags elements (data-bi-i); later calls compare only tagged elements, so new nodes never count.
function pageLayoutSnapshot() {
  const out = {}; let i = 0;
  const tagged = document.querySelector('[data-bi-i]') !== null;
  const els = tagged ? document.querySelectorAll('[data-bi-i]') : document.querySelectorAll('body *');
  for (const el of els) {
    const svgText = el.tagName.toLowerCase() === 'text' && el.closest('svg');
    if (!tagged) { if (i >= 6000) break; if ((el.closest('svg') && !svgText) || el.closest('script, style, noscript, template')) continue; el.setAttribute('data-bi-i', String(i++)); }
    if (el.closest('[data-bi-logo]')) continue; // the logo itself is replaced on purpose
    if (!el.getClientRects().length) { out[el.getAttribute('data-bi-i')] = null; continue; }
    if (svgText) { // chart labels: clipped when the text's box leaves its clipping <svg> viewport
      const host = el.closest('svg'); const hb = host.getBoundingClientRect(); const r = el.getBoundingClientRect();
      const clips = getComputedStyle(host).overflow !== 'visible';
      const out_ = r.width > 0 && (r.left < hb.left - 0.5 || r.right > hb.right + 0.5 || r.top < hb.top - 0.5 || r.bottom > hb.bottom + 0.5);
      out[el.getAttribute('data-bi-i')] = { o: clips && out_, l: 0, t: el.textContent.trim().slice(0, 30), tag: 'svg text' };
      continue;
    }
    const cs = getComputedStyle(el);
    const ox = !/(auto|scroll)/.test(cs.overflowX) && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1;
    const oy = /(hidden|clip)/.test(cs.overflowY) && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 1;
    let lines = 0;
    const fs = parseFloat(cs.fontSize) || 16;
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      const tops = [...rg.getClientRects()].filter(r => r.width > 0).map(r => r.top).sort((a, b) => a - b);
      let last = -1e9; for (const t of tops) if (t - last > fs * 0.5) { lines++; last = t; }
    }
    const txt = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim().slice(0, 30);
    out[el.getAttribute('data-bi-i')] = { o: ox || oy, l: lines, t: txt, tag: el.tagName.toLowerCase() };
  }
  return out;
}

function layoutStress(before, after) {
  const items = [];
  for (const [k, b] of Object.entries(before)) {
    const a = after[k]; if (!a || !b) continue;
    const overflow = a.o && !b.o, gained = a.l > b.l && b.l > 0;
    if (overflow || gained) items.push({ tag: a.tag, text: a.t, overflow, lines: [b.l, a.l] });
  }
  return { count: items.length, overflow: items.filter(x => x.overflow).length, gainedLines: items.filter(x => x.lines[1] > x.lines[0]).length, samples: items.slice(0, 8).map(x => `${x.tag} "${x.text}"${x.overflow ? (x.tag === 'svg text' ? ' clipped' : ' overflows') : ''}${x.lines[1] > x.lines[0] ? ` ${x.lines[0]}->${x.lines[1]} lines` : ''}`) };
}

// In-page: add the set's faces from bytes (no URL, so no file:// or mixed-origin problem), size-adjusted to the
// x-height of the face they replace (both measured on a canvas in this page), then mark display-role elements.
async function pageFontSwap({ faces, oldStacks, roleStyle, ICON }) {
  const cv = document.createElement('canvas'); const cx = cv.getContext('2d');
  const xh = (stack, w) => { cx.font = `${w} 100px ${stack}`; return cx.measureText('x').actualBoundingBoxAscent; };
  const b64buf = b => { const s = atob(b); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u.buffer; };
  const report = {};
  for (const f of faces) {
    const r = report[f.role] = report[f.role] || { family: f.family, name: f.name, files: 0, loaded: 0, sizeAdjust: null, xHeight: null };
    const buf = b64buf(f.b64);
    const w = f.measureWeight;
    if (r.sizeAdjust == null && oldStacks[f.role]) {
      try {
        const probe = new FontFace(`__bi_probe_${f.role}`, buf.slice(0), { weight: f.weight });
        await probe.load(); document.fonts.add(probe);
        await document.fonts.load(`${w} 100px ${oldStacks[f.role]}`).catch(() => {});
        const xo = xh(oldStacks[f.role], w), xn = xh(`"__bi_probe_${f.role}"`, w);
        document.fonts.delete(probe);
        if (xo > 0 && xn > 0) { r.xHeight = { old: +(xo / 100).toFixed(3), new: +(xn / 100).toFixed(3) }; r.sizeAdjust = Math.min(1.4, Math.max(0.7, xo / xn)); }
      } catch (e) { r.error = String(e && e.message || e); }
    }
    try {
      const desc = { weight: f.weight, style: 'normal', display: 'block' };
      if (r.sizeAdjust) desc.sizeAdjust = (r.sizeAdjust * 100).toFixed(1) + '%';
      const face = new FontFace(f.name, buf, desc);
      await face.load(); document.fonts.add(face);
      r.files++; if (face.status === 'loaded') r.loaded++;
    } catch (e) { r.files++; r.error = String(e && e.message || e); }
  }
  // display role: elements already rewritten to the display family, h1-h3, and (one-family sites) large short text
  const icon = new RegExp(ICON, 'i');
  const first = s => String(s || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
  const dname = roleStyle.display && roleStyle.display.name;
  let marked = 0;
  if (dname) {
    const bodyFs = parseFloat(getComputedStyle(document.body).fontSize) || 16;
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('svg, code, pre, kbd, samp, [data-bi-logo], [data-bi-site-logo]')) continue;
      const cs = getComputedStyle(el); const fam = first(cs.fontFamily);
      if (icon.test(cs.fontFamily) || /^(i|em)$/i.test(el.tagName) && !el.textContent.trim()) continue;
      const direct = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      const tag = el.tagName.toLowerCase();
      let hit = fam === dname || /^h[1-3]$/.test(tag);
      if (!hit && roleStyle.oneFamily && direct) {
        const t = el.textContent.trim();
        hit = /^h[4-6]$/.test(tag) || (parseFloat(cs.fontSize) >= Math.max(24, bodyFs * 1.5) && t.length < 140);
      }
      if (/[-]/.test(el.textContent || '')) hit = false; // private-use glyphs = icon font
      if (hit) { el.setAttribute('data-bi-display', ''); marked++; }
    }
  }
  // mono role: code-like elements, mono-named classes, the old mono family, and figure-heavy / tabular-nums cells
  const mname = roleStyle.mono && roleStyle.mono.name;
  let monoMarked = 0;
  if (mname) {
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('svg, [data-bi-logo], [data-bi-site-logo]')) continue;
      const cs = getComputedStyle(el);
      if (icon.test(cs.fontFamily)) continue;
      let hit = el.matches('code, pre, kbd, samp, .mono, [class*="mono" i]') || first(cs.fontFamily) === mname;
      if (!hit && /^(td|th)$/i.test(el.tagName) || !hit && /tabular-nums/.test(cs.fontVariantNumeric) && el.closest('table')) {
        const t = el.textContent.replace(/\s+/g, '');
        hit = t.length >= 2 && /\d/.test(t) && (t.match(/[\d.,%+\-−]/g) || []).length / t.length >= 0.6;
      }
      if (hit) { el.setAttribute('data-bi-mono', ''); monoMarked++; }
    }
  }
  const css = [];
  const t = roleStyle.text, d = roleStyle.display, mo = roleStyle.mono;
  if (t && t.name) css.push(`html{font-optical-sizing:none${t.fvs ? `;font-variation-settings:${t.fvs}` : ''}}`);
  if (d && d.name) css.push(`[data-bi-display]{font-family:"${d.name}", ${d.fallback} !important;font-optical-sizing:none !important;font-variation-settings:${d.fvs || 'normal'} !important;font-synthesis:none${d.tracking != null ? `;letter-spacing:${d.tracking / 1000}em !important` : ''}${d.case === 'upper' ? ';text-transform:uppercase !important' : d.case === 'lower' ? ';text-transform:lowercase !important' : ''}${d.features ? `;font-feature-settings:${d.features}` : ''}}`);
  if (mo && mo.name) css.push(`[data-bi-mono]{font-family:"${mo.name}", ${mo.fallback} !important;font-optical-sizing:none !important;font-variation-settings:${mo.fvs || 'normal'} !important}`);
  const st = document.createElement('style'); st.setAttribute('data-bi-fonts', ''); st.textContent = css.join('\n'); document.head.appendChild(st);
  await document.fonts.ready;
  for (const role of Object.keys(report)) {
    const r = report[role];
    r.check = document.fonts.check(`16px "${r.name}"`);
    // does a real element of this role resolve to our face? (computed family starts with it)
    const probeEl = role === 'display' ? document.querySelector('[data-bi-display]') : role === 'mono' ? document.querySelector('[data-bi-mono]') : document.querySelector('main p, article p, p, li, body');
    r.computed = probeEl ? first(getComputedStyle(probeEl).fontFamily) : null;
    r.ok = r.loaded > 0 && r.loaded === r.files && r.check; // the face is really there (not a fallback)
    r.applied = r.computed === r.name; // and page text of this role uses it
  }
  return { roles: report, displayMarked: marked, monoMarked };
}

// In-page: replace the site's logo (found by logoFinder, all copies with the same source) with one of the set's
// SVG variants, fitted inside the original box +10% (aspect kept). Variants are data-URI images.
function pageLogoSwap({ LIB, GUARD, LOGO, variants: raw }) {
  const L = eval(LIB); const T = eval(GUARD)(L);
  // crop each variant to its ink (logolib versions carry clear-space padding, which would shrink the logo in a
  // header box), then encode as a data URI
  const host = document.createElement('div'); host.style.cssText = 'position:absolute;left:-10000px;top:0;width:1000px;height:1000px;visibility:hidden';
  document.body.appendChild(host);
  const variants = raw.map(v => {
    let svg = v.svg, w = v.w, h = v.h;
    try {
      host.innerHTML = v.svg; const el = host.querySelector('svg');
      const b = el && el.getBBox();
      if (b && b.width > 0 && b.height > 0) {
        el.setAttribute('viewBox', `${b.x} ${b.y} ${b.width} ${b.height}`); el.removeAttribute('width'); el.removeAttribute('height');
        if (!el.getAttribute('xmlns')) el.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        svg = new XMLSerializer().serializeToString(el); w = b.width; h = b.height;
      }
    } catch { /* keep the file's own viewBox */ }
    return { name: v.name, w, h, primary: v.primary, reversed: v.reversed, b64: btoa(unescape(encodeURIComponent(svg))) };
  });
  host.remove();
  const found = eval(LOGO)();
  if (!found) return { replaced: false, reason: 'no logo found on this page' };
  const el = found.el; const r = el.getBoundingClientRect();
  let box = { w: r.width, h: r.height };
  if (found.kind === 'text') { // text wordmark: the glyph box, not the padded link
    const rg = document.createRange(); rg.selectNodeContents(el); const rr = rg.getBoundingClientRect();
    if (rr.width > 0 && rr.height > 0) box = { w: rr.width, h: rr.height };
  }
  const bg = T.bgOf(el); const dark = bg ? L.toOklch(bg[0]).L < 0.5 : false;
  let pool = variants.filter(v => v.reversed === dark);
  if (!pool.length) pool = variants.filter(v => !v.reversed);
  if (!pool.length) pool = variants;
  // inside the old box +10% on both sides, never taller than the old box (aspect kept)
  const fit = (v, b = box) => { const s = Math.min(b.w * 1.1 / v.w, b.h * 1.1 / v.h, b.h / v.h); return { w: v.w * s, h: v.h * s }; };
  let best = null;
  for (const v of pool) {
    const f = fit(v); const score = f.w * f.h * (v.primary ? 1.25 : 1);
    if (!best || score > best.score) best = { v, f, score };
  }
  // A wide lockup in a small square icon slot is unreadable. When the icon sits in a home link next to the typed
  // site name, that pair is what a lockup replaces: fit into the link's box instead (the name moves to aria-label).
  let scope = 'logo';
  const link = el.closest('a[href]');
  if (best.f.w * best.f.h < 0.5 * box.w * box.h && (found.kind === 'img' || found.kind === 'svg') && link && link !== el) {
    const lr = link.getBoundingClientRect(); const lt = (link.innerText || '').trim();
    if (lt && lt.length < 80 && lr.height <= Math.max(box.h * 2.2, 48) && lr.width > box.w * 1.5) {
      const lbox = { w: lr.width, h: Math.max(box.h, Math.min(lr.height, box.h * 1.6)) };
      let lb = null;
      for (const v of pool) { const f = fit(v, lbox); const sc = f.w * f.h * (v.primary ? 1.25 : 1); if (!lb || sc > lb.score) lb = { v, f, score: sc }; }
      if (lb && lb.f.w * lb.f.h > best.f.w * best.f.h * 1.5) {
        const W2 = Math.round(lb.f.w * 10) / 10, H2 = Math.round(lb.f.h * 10) / 10;
        const img = document.createElement('img'); img.src = 'data:image/svg+xml;base64,' + lb.v.b64; img.alt = lt.split('\n')[0];
        img.setAttribute('data-bi-logo', lb.v.name); img.style.cssText = `width:${W2}px!important;height:${H2}px!important;max-width:none!important;max-height:none!important;display:inline-block;vertical-align:middle;object-fit:contain;flex:none`;
        if (!link.getAttribute('aria-label')) link.setAttribute('aria-label', lt.split('\n')[0]);
        link.replaceChildren(img); link.setAttribute('data-bi-logo-host', '');
        return { replaced: true, kind: found.kind, scope: 'home link (icon + typed name)', copies: 1, variant: lb.v.name, ground: dark ? 'dark' : 'light', oldBox: { w: +lbox.w.toFixed(1), h: +lbox.h.toFixed(1) }, newBox: { w: W2, h: H2 }, fill: +((W2 * H2) / (lbox.w * lbox.h)).toFixed(2), iconBox: { w: +box.w.toFixed(1), h: +box.h.toFixed(1) } };
      }
    }
  }
  const { v, f } = best;
  const W = Math.round(f.w * 10) / 10, H = Math.round(f.h * 10) / 10;
  const src = 'data:image/svg+xml;base64,' + v.b64;
  const make = t => { // each copy is fitted to its own box (footer logos are smaller than the header one)
    const tr = t.getBoundingClientRect(); const s = t === el || !(tr.width > 0 && tr.height > 0) ? null : fit(v, { w: tr.width, h: tr.height });
    const w = s ? Math.round(s.w * 10) / 10 : W, h = s ? Math.round(s.h * 10) / 10 : H;
    const img = document.createElement('img'); img.src = src; img.alt = el.getAttribute('alt') || el.getAttribute('aria-label') || (el.innerText || '').trim() || 'logo'; img.setAttribute('data-bi-logo', v.name); img.style.cssText = `width:${w}px!important;height:${h}px!important;max-width:none!important;max-height:none!important;display:inline-block;vertical-align:middle;object-fit:contain;flex:none`; return img;
  };
  const targets = [el];
  if (found.kind === 'img') { const s = el.currentSrc || el.src; for (const o of document.querySelectorAll('img')) if (o !== el && s && (o.currentSrc || o.src) === s) targets.push(o); }
  if (found.kind === 'svg') { const h = el.outerHTML; for (const o of document.querySelectorAll('svg')) if (o !== el && o.outerHTML === h) targets.push(o); }
  let n = 0;
  for (const t of targets) {
    if (found.kind === 'img' || found.kind === 'svg') {
      const pic = t.closest('picture');
      if (pic && found.kind === 'img') { for (const s of pic.querySelectorAll('source')) s.remove(); }
      t.replaceWith(make(t)); n++;
    } else if (found.kind === 'text') {
      const label = (t.innerText || '').trim();
      if (label && !t.getAttribute('aria-label')) t.setAttribute('aria-label', label);
      t.replaceChildren(make(t)); t.setAttribute('data-bi-logo-host', ''); n++;
    } else if (found.kind === 'bg') {
      t.style.setProperty('background-image', `url("${src}")`, 'important');
      t.style.setProperty('background-size', 'contain', 'important');
      t.style.setProperty('background-repeat', 'no-repeat', 'important');
      t.style.setProperty('background-position', 'left center', 'important');
      t.setAttribute('data-bi-logo', v.name); n++;
    }
  }
  return { replaced: n > 0, kind: found.kind, scope, copies: n, variant: v.name, ground: dark ? 'dark' : 'light', oldBox: { w: +box.w.toFixed(1), h: +box.h.toFixed(1) }, newBox: { w: W, h: H }, fill: +((W * H) / (box.w * box.h)).toFixed(2) };
}

// Header close-up box (CSS px, document coordinates): the logo's header / banner / nav, else the top band.
function pageHeaderBox() {
  const vw = innerWidth;
  const logo = document.querySelector('[data-bi-logo], [data-bi-site-logo]');
  let el = logo && (logo.closest('header, [role="banner"]') || logo.closest('nav'));
  if (!el) el = [...document.querySelectorAll('header, [role="banner"], nav, body > div *')].find(e => { const r = e.getBoundingClientRect(); const p = getComputedStyle(e).position; return r.top + scrollY < 200 && r.height > 30 && r.height < 240 && r.width > vw * 0.8 && (e.matches('header, [role="banner"], nav') || p === 'fixed' || p === 'sticky'); });
  const r = el ? el.getBoundingClientRect() : { top: 0, height: 120 };
  const y = Math.max(0, r.top + scrollY - 8), h = Math.min(Math.max(r.height + 16, 60), 260);
  return { x: 0, y, width: vw, height: h, found: Boolean(el) };
}

const ICON_FONT = 'icon|awesome|material|symbols|glyph|fontello|ionicons|dashicons|feather|phosphor|remixicon|bootstrap-icons|lucide';
const fvsOf = (loc, axes, dropWght) => Object.entries(loc || {}).filter(([k]) => !(dropWght && k === 'wght') && (!axes || axes[k])).map(([k, v]) => `"${k}" ${v}`).join(', ');
function svgSize(svg) {
  const vb = svg.match(/viewBox\s*=\s*["']\s*([-\d.e]+)[\s,]+([-\d.e]+)[\s,]+([\d.e]+)[\s,]+([\d.e]+)/i);
  if (vb) return { w: +vb[3], h: +vb[4] };
  const w = svg.match(/<svg[^>]*\swidth\s*=\s*["']([\d.]+)/i), h = svg.match(/<svg[^>]*\sheight\s*=\s*["']([\d.]+)/i);
  return w && h ? { w: +w[1], h: +h[1] } : null;
}

// Parked / for-sale domains render fine and would be measured as a competitor (a HugeDomains or Sedo template).
// Signals: the navigation ended on a domain marketplace / parking host, or the page says the domain is for sale.
const PARK_HOSTS = /(^|\.)(hugedomains\.com|sedo\.com|sedoparking\.com|dan\.com|afternic\.com|godaddy\.com|parkingcrew\.net|bodis\.com|above\.com|undeveloped\.com|sav\.com|atom\.com|squadhelp\.com|brandbucket\.com|domainmarket\.com|buydomains\.com|parklogic\.com|efty\.com|namecheap\.com|porkbun\.com|epik\.com|dynadot\.com|domainnamesales\.com|uniregistry\.com|perfectdomain\.com)$/i;
const PARK_TEXT = [/\b(this|the) domain( name)?( [\w.-]+)? (is|may be|might be) for sale\b/i, /\b[\w-]+\.[a-z]{2,}( is| may be)? for sale\b/i, /\bdomain( name)? (is )?for sale\b/i, /\bbuy this domain\b/i,
  /\bmake an offer\b.{0,40}\bdomain\b|\bdomain\b.{0,40}\bmake an offer\b/i, /\b(is|was) parked\b|\bparked (free|domain|page)\b|\bdomain parking\b/i,
  /\binquire about (this|the) domain\b/i, /\bhugedomains\b|\bsedo\b|\bafternic\b|\bdan\.com\b|\bbodis\b|\bparkingcrew\b/i,
  /\bthis domain (has expired|is available)\b|\bget this domain\b/i];
function parkedReason({ requested, final, title = '', text = '' }) {
  let rh = '', fh = '';
  try { rh = new URL(requested).hostname.replace(/^www\./, ''); fh = new URL(final || requested).hostname.replace(/^www\./, ''); } catch { /* keep empty */ }
  if (fh && fh !== rh && PARK_HOSTS.test(fh) && !PARK_HOSTS.test(rh)) return `redirected to ${fh}`;
  if (PARK_HOSTS.test(rh)) return null; // the marketplace itself is a real site
  // body text counts only on a short page (a parking page), the title always
  const t = String(text); const short = t.length < 20000;
  const hit = PARK_TEXT.find(p => p.test(title)) || (short ? PARK_TEXT.find(p => p.test(t)) : null);
  if (!hit) return null;
  const m = (String(title).match(hit) || t.match(hit) || [''])[0];
  return `page says "${m.trim().slice(0, 50)}"`;
}

// HTTP-level bot protection: a challenge header, or an access status. 404 / 500 are errors, not blocks.
function httpBlockReason(status, headers = {}) {
  const h = Object.fromEntries(Object.entries(headers || {}).map(([k, v]) => [k.toLowerCase(), String(v)]));
  if (/challenge/i.test(h['cf-mitigated'] || '')) return 'Cloudflare challenge (cf-mitigated)';
  if ([401, 403, 429].includes(status)) return `HTTP ${status}${/cloudflare|akamai|sucuri|imperva|incapsula/i.test(h.server || '') ? ` from ${h.server}` : ''}`;
  if (status === 503 && /cloudflare|akamai|sucuri|imperva|incapsula/i.test(h.server || '')) return `HTTP 503 from ${h.server}`;
  return null;
}

const FAMILIES = [[10, 'red'], [45, 'orange'], [75, 'yellow'], [115, 'green'], [165, 'teal'], [215, 'blue'], [270, 'violet'], [310, 'magenta'], [350, 'pink']];
function hueFamily(hex) {
  const c = hex && C.parse(hex); if (!c) return null;
  const o = C.toOklch(c); if (o.C < 0.04) return 'neutral';
  let name = 'pink'; for (const [start, n] of FAMILIES) if (o.h >= start) name = n;
  return name;
}

function slugify(s) { return String(s).toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/ı/g, 'i').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '') || 'palette'; }

// ---------------------------------------------------------------- page closures
// In site_palette.mjs these were passed inline to page.evaluate; the bodies are unchanged, only named here.
const pageHideOverlays = () => {
  const re = /cookie|consent|gdpr|çerez|privacy choices|datenschutz/i;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height > innerWidth * innerHeight * 0.9 && !re.test(el.innerText || '')) continue;
    if (re.test((el.innerText || '').slice(0, 2000)) || re.test(el.id + ' ' + el.className)) el.style.setProperty('display', 'none', 'important');
  }
  for (const el of [document.documentElement, document.body]) if (el && getComputedStyle(el).overflow === 'hidden') el.style.setProperty('overflow', 'auto', 'important');
};
const pageSettleScroll = async () => {
  const H = () => Math.min(document.documentElement.scrollHeight, 20000);
  for (let y = 0; y < H(); y += innerHeight * 0.8) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); }
  window.scrollTo(0, 0);
  await document.fonts.ready;
  await Promise.all([...document.images].filter(i => !i.complete).slice(0, 40).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 3000); })));
};
const pageImagePalette = async ({ b64, k, LIB }) => {
  const L = eval(LIB);
  const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
  const W = 240, H = Math.round(img.height * 240 / img.width); const cv = new OffscreenCanvas(W, H); const cx = cv.getContext('2d'); cx.drawImage(img, 0, 0, W, H);
  const d = cx.getImageData(0, 0, W, H).data; const px = [];
  for (let i = 0; i < d.length; i += 4) px.push(L.rgbToOklab({ r: d[i], g: d[i + 1], b: d[i + 2] }));
  let cent = Array.from({ length: k }, (_, i) => ({ ...px[Math.floor((i + 0.5) * px.length / k)] }));
  for (let it = 0; it < 12; it++) {
    const sum = cent.map(() => ({ L: 0, a: 0, b: 0, n: 0 }));
    px.forEach(p => { let bi = 0, bd = 1e9; cent.forEach((c, j) => { const dd = (p.L - c.L) ** 2 + (p.a - c.a) ** 2 + (p.b - c.b) ** 2; if (dd < bd) { bd = dd; bi = j; } }); const s = sum[bi]; s.L += p.L; s.a += p.a; s.b += p.b; s.n++; });
    cent = sum.map((s, j) => (s.n ? { L: s.L / s.n, a: s.a / s.n, b: s.b / s.n, n: s.n } : cent[j]));
  }
  return cent.filter(c => c.n).map(c => { const Cc = Math.hypot(c.a, c.b); let h = Math.atan2(c.b, c.a) * 180 / Math.PI; if (h < 0) h += 360; return { hex: L.toHex(L.oklchToRgb({ L: c.L, C: Cc, h })), share: +(c.n / px.length).toFixed(3) }; }).sort((a, b) => b.share - a.share);
};
const pageLogoBgSrc = () => { const e = document.querySelector('[data-bi-site-logo]'); const m = e && getComputedStyle(e).backgroundImage.match(/url\(["']?([^"')]+)/); return m ? new URL(m[1], location.href).href : null; };
const pageTextChars = () => (document.body ? document.body.innerText : '').replace(/\s+/g, '').length;
const pageSettleSwap = async () => { await document.fonts.ready; await Promise.all([...document.querySelectorAll('img[data-bi-logo]')].map(i => i.decode().catch(() => {}))); };
const pageInfo = () => ({ final: location.href, title: document.title || '', text: (document.body ? document.body.innerText : '').slice(0, 20001) });
const pageScrollHeight = () => document.documentElement.scrollHeight;

// ---------------------------------------------------------------- driver glue
// Expressions the Node driver evaluated in Node itself, wrapped as functions; the expressions are unchanged.
function rewriteStylesheet(body, oldRoles, newRoles, scales, plan) {
  const map = newRoles ? C.makeMapper(oldRoles, newRoles, { scales }) : null;
  const famFor = plan ? F.makeFamilyMapper(plan) : null;
  if (map) body = C.rewriteCss(body, map);
  if (famFor) body = F.rewriteFontCss(body, famFor);
  return body;
}
function competitorStats(b) {
  const chromatic = b.palette.filter(p => chromaOf(p.hex) >= 0.06);
  const hues = []; for (const p of chromatic) { const h = hueOf(p.hex); if (!hues.some(x => C.hueDist(x, h) < 30)) hues.push(h); }
  const Ls = b.palette.slice(0, 12).map(p => C.toOklch(C.parse(p.hex)).L);
  return { nHues: hues.length, hues: hues.map(h => Math.round(h)), lightnessRange: Ls.length ? [r2(Math.min(...Ls)), r2(Math.max(...Ls))] : null };
}
const isDarkHex = hex => C.toOklch(C.parse(hex)).L < 0.5;
const colorOk = s => Boolean(C.parse(s));
const darkCss = text => DARK_CSS.test(text);
function checkUrl(u) { try { const x = new URL(u); if (!/^(https?|file):$/.test(x.protocol)) throw new Error(); return x.href; } catch { return null; } }
function hostOf(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; } }
// loadIdentitySet's logo-variant steps (logo/build/*.svg): which files, in which order, cleaned how, reversed or not
function pickVariantNames(names) {
  names = names.filter(n => /\.svg$/i.test(n));
  names = names.filter(n => !/^(master-|on-|one-colou?r|favicon|small-|app-icon)|(^|[-_.])(black|mono|outline|sheet|contact|test)([-_.]|$)/i.test(n));
  const PRIMARY = ['full-color.svg', 'primary.svg'];
  const rank = n => (PRIMARY.includes(n) ? PRIMARY.indexOf(n) : PRIMARY.length);
  names.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return names.map(n => ({ name: n, key: n.replace(/\.svg$/i, ''), primary: PRIMARY.includes(n) }));
}
const cleanSvg = text => text.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<\?xml[^>]*\?>|<!DOCTYPE[^>]*>/gi, '').trim();
const stripScripts = text => text.replace(/<script[\s\S]*?<\/script>/gi, '');
const reversedKey = key => /(-dark|revers|invers|on-dark|knockout|negative)/i.test(key);

// ---------------------------------------------------------------- bridge
// Values cross the protocol as a tree that keeps what JSON drops (NaN, Infinity, -0, undefined), the way
// Playwright's serializer kept them: {s: 'NaN'|'Infinity'|'-Infinity'|'-0'|'undefined'}, {a: [...]}, {o: [[k, v]]}.
const SER = BRIDGE_SER;
const REV = BRIDGE_REV;
const API = { buildRoles, layoutStress, rewriteStylesheet, competitorStats, isDarkHex, colorOk, darkCss, checkUrl, hostOf,
  pickVariantNames, cleanSvg, stripScripts, reversedKey, parkedReason, httpBlockReason, emptyPageReason, correctedRoles, hueFamily, chromaOf, slugify, fvsOf,
  svgSize, firstFamily: F.first, namedTokenRoles, tokenCores, confidence, colorLib, fontLib };
const PAGE = { pageExtract, pageBlockCheck, pageLogoCopy, pageRecolor, pageGuardBaseline, pageContrastGuard, pageMarkLogo,
  pageRewriteFonts, pageLayoutSnapshot, pageFontSwap, pageLogoSwap, pageHeaderBox, pageHideOverlays, pageSettleScroll,
  pageImagePalette, pageLogoBgSrc, pageTextChars, pageSettleSwap, pageInfo, pageScrollHeight };
globalThis.SiteEngine = {
  call: (name, args) => SER(API[name](...REV(args))),
  sources: Object.assign(Object.fromEntries(Object.entries(PAGE).map(([k, f]) => [k, f.toString()])),
    { COLOR_LIB_SRC, LOGO_SRC, GUARD_SRC, FONT_LIB_SRC, FREEZE_CSS, CONSENT_SEL, ICON_FONT, ROLE_KEYS }),
  API, C, F,
};
})();
