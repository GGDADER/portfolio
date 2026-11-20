import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// Exercise the actual code that the settings page copies to Apps Script.
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const merge = script.slice(script.indexOf("function mergeCore("), script.indexOf("function trDesc("));
const gasCode = script.slice(script.indexOf("function gasCode(){"), script.indexOf("function copyGAS("));
const gas = vm.runInNewContext(merge + gasCode + ";gasCode()");
const rows = [];
let locked = false;
const sheet = {
  getLastRow: () => rows.length,
  getRange(row, col, height, width) {
    const values = () => Array.from({ length: height }, (_, i) =>
      Array.from({ length: width }, (_, j) => rows[row - 1 + i]?.[col - 1 + j] ?? ""));
    const write = (matrix) => matrix.forEach((line, i) => {
      const target = rows[row - 1 + i] ??= [];
      line.forEach((v, j) => { target[col - 1 + j] = v; });
    });
    return { getValues: values, setValues: write, clearContent() { write(values().map(r => r.map(() => ""))); } };
  },
};
const services = {
  SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheetByName: () => sheet }), flush() {} },
  LockService: { getScriptLock: () => ({
    waitLock() { assert.equal(locked, false); locked = true; },
    releaseLock() { locked = false; },
  }) },
  ContentService: {
    MimeType: { JSON: "json" },
    createTextOutput: text => ({ setMimeType: () => JSON.parse(text) }),
  },
};
const server = vm.createContext(services);
vm.runInContext(gas, server);
function post(trades, del = {}) {
  return server.doPost({ postData: { contents: JSON.stringify({
    trades: JSON.stringify(trades), del: JSON.stringify(del), prices: "{}", history: "[]",
  }) } });
}
function read() { return server.doGet({ parameter: { action: "read" } }); }
const buy = { id: "original", symbol: "TEST", action: "buy", qty: 1, price: 10, date: "2026-01-01" };
assert.equal(server.doGet({ parameter: { action: "ping" } }).version, "v12");
assert.equal(post([buy]).ok, true);
const other = { ...buy, id: "other", price: 12 };
assert.equal(post([other]).n, 2);
assert.equal(post([{ ...buy, price: 15, u: 2 }]).n, 2);
assert.equal(JSON.parse(post([buy]).trades).find(t => t.id === buy.id).price, 15);
post([], { original: 3 });
post([buy]);  // A device still has the old copy.
assert.deepEqual(JSON.parse(read().trades).map(t => t.id), ["other"]);
post([{ ...buy, id: "original-r", u: 4 }]);
post([buy, other]);
assert.deepEqual(JSON.parse(read().trades).map(t => t.id), ["other", "original-r"]);
assert.deepEqual(JSON.parse(read().del), { original: 3 });
assert.equal(locked, false);
console.log("GAS v12 OK — read, edit, delete, stale device, restore and resync");
