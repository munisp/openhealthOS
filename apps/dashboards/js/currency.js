/* Currency registry — country-aware money display.
 *
 * Doctrine: a currency is an entry, never an if-branch. Adding a
 * country adds an entry to CURRENCIES; no view ever names a symbol or
 * a minor unit directly. Every money figure on the boards flows
 * through fmtMoney (major units) or fmtMinor (minor units).
 *
 * The active currency resolves from ?ccy=<CODE>, then localStorage
 * ("oh.ccy"), then the deployment default. Unknown codes fall back to
 * the default with a console note — absence is not a value, and a
 * typo'd currency is not a new currency.
 */

export const CURRENCIES = {
  NGN: { code: "NGN", symbol: "₦", minor: "kobo",     locale: "en-NG" },
  EUR: { code: "EUR", symbol: "€", minor: "cents",    locale: "en-IE" },
  CVE: { code: "CVE", symbol: "$", minor: "centavos", locale: "pt-CV" },
  USD: { code: "USD", symbol: "$", minor: "cents",    locale: "en-US" },
};

export const DEFAULT_CCY = "NGN";

export function resolveCurrency(code) {
  return CURRENCIES[code] || null;
}

export function activeCurrency() {
  let code = null;
  if (typeof location !== "undefined" && location.search) {
    code = new URLSearchParams(location.search).get("ccy");
  }
  if (!code && typeof localStorage !== "undefined") {
    code = localStorage.getItem("oh.ccy");
  }
  const hit = resolveCurrency(code);
  if (hit) return hit;
  if (code && typeof console !== "undefined") {
    console.warn(`unknown currency "${code}" — falling back to ${DEFAULT_CCY}`);
  }
  return CURRENCIES[DEFAULT_CCY];
}

export function setCurrency(code) {
  if (!resolveCurrency(code)) return false;
  if (typeof localStorage !== "undefined") localStorage.setItem("oh.ccy", code);
  return true;
}

/* major units → "₦41,100" / "€1,234.50" in the currency's own locale */
export function fmtMoney(major, ccy) {
  const c = ccy ? resolveCurrency(ccy) || activeCurrency() : activeCurrency();
  const n = major || 0;
  const frac = Math.round(Math.abs(n) % 1 * 100);
  return c.symbol + n.toLocaleString(c.locale, {
    minimumFractionDigits: frac ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

/* minor units → "4,110,000 kobo" — denominators visible, always */
export function fmtMinor(minor, ccy) {
  const c = ccy ? resolveCurrency(ccy) || activeCurrency() : activeCurrency();
  return (minor || 0).toLocaleString(c.locale) + " " + c.minor;
}
