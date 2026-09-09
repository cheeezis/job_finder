const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../job_finder/app.js"), "utf8");

function node(tagName) {
  return {tagName, children: [], append(...children) { this.children.push(...children); }};
}

function helpers(fetch) {
  return vm.runInNewContext(source + "\nJobFinder;", {
    URL, fetch, document: {createElement: node}
  });
}

test("source URLs allow HTTP(S) and reject executable or malformed links", () => {
  const {safeUrl} = helpers();
  for (const value of ["javascript:alert(1)", "data:text/html,test", "file:///test", "bad URL"]) {
    assert.equal(safeUrl(value), "");
  }
  assert.equal(safeUrl("https://example.test/job"), "https://example.test/job");
});

test("source menus deduplicate links and render labels as text", () => {
  const parent = node("div");
  helpers().appendSourceLinks(parent, {source_links: [
    {source: "first", url: "https://example.test/a"},
    {source: "duplicate", url: "https://example.test/a"},
    {source: "second", url: "https://example.test/b"},
    {source: "unsafe", url: "javascript:alert(1)"}
  ]}, {first: "<img src=x onerror=alert(1)>"}, true);
  const [menu] = parent.children;
  assert.equal(menu.tagName, "details");
  const [summary, options] = menu.children;
  assert.equal(summary.textContent, "Anzeigen öffnen (2)");
  assert.equal(summary.className, "button");
  assert.equal(options.children[0].textContent, "<img src=x onerror=alert(1)>");
  for (const link of options.children) {
    assert.equal(link.target, "_blank");
    assert.equal(link.rel, "noopener noreferrer");
  }
});

test("single fallback links retain page-specific button styling", () => {
  for (const asButtons of [false, true]) {
    const parent = node("div");
    helpers().appendSourceLinks(parent, {url: "https://example.test/job"}, {}, asButtons);
    assert.equal(parent.children.length, 1);
    assert.equal(parent.children[0].textContent, "Anzeige öffnen");
    assert.equal(parent.children[0].className || "", asButtons ? "button" : "");
  }
});

test("JSON requests preserve payload and server errors", async () => {
  const calls = [];
  const {postJson} = helpers(async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({workflow_status: "applied"})};
  });
  const result = await postJson("/api/applications", {job_id: "job:1"}, "fallback");
  assert.equal(result.workflow_status, "applied");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {job_id: "job:1"});
  for (const error of ["changed concurrently", ""]) {
    const api = helpers(async () => ({ok: false, json: async () => ({error})}));
    await assert.rejects(api.postJson("/api/history", {}, "fallback"),
      {message: error || "fallback"});
  }
});

test("monthly salary preview converts twelve payments without changing annual input", () => {
  const {salaryYearAmount} = helpers();
  assert.equal(salaryYearAmount("4500", "month"), 54000);
  assert.equal(salaryYearAmount("54000", "year"), 54000);
  assert.equal(salaryYearAmount("", "month"), null);
  assert.equal(salaryYearAmount("invalid", "year"), null);
});
