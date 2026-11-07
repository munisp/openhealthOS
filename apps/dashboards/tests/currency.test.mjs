/* Node tests for the currency registry — money display is country-aware
 * by construction: a currency is an entry, never an if-branch.
 * Run: node --test tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { CURRENCIES, DEFAULT_CCY, resolveCurrency, activeCurrency,
         fmtMoney, fmtMinor, setCurrency } from "../js/currency.js";

test("registry carries the deployment currencies as entries", () => {
  assert.equal(CURRENCIES.NGN.minor, "kobo");
  assert.equal(CURRENCIES.EUR.symbol, "€");
  assert.equal(CURRENCIES.CVE.minor, "centavos");
  assert.ok(CURRENCIES.USD);
});

test("unknown currency resolves to null, not a guess", () => {
  assert.equal(resolveCurrency("XXX"), null);
  assert.equal(resolveCurrency(undefined), null);
});

test("activeCurrency falls back to the deployment default off-browser", () => {
  assert.equal(activeCurrency().code, DEFAULT_CCY);
});

test("fmtMoney formats major units per currency, never hardcoded", () => {
  assert.equal(fmtMoney(41100, "NGN"), "₦41,100");
  assert.equal(fmtMoney(1234.5, "EUR"), "€1,234.50");
  assert.notEqual(fmtMoney(100, "NGN"), fmtMoney(100, "EUR"));
});

test("fmtMinor names the minor unit from the entry", () => {
  assert.equal(fmtMinor(4110000, "NGN"), "4,110,000 kobo");
  assert.equal(fmtMinor(2500, "EUR"), "2,500 cents");
});

test("setCurrency refuses unknown codes", () => {
  assert.equal(setCurrency("XXX"), false);
});
