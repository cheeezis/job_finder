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


const reviewHtml = fs.readFileSync(path.join(__dirname, "../job_finder/review.html"), "utf8");
const filterStart = reviewHtml.indexOf("  function applyFilters(");
const filterEnd = reviewHtml.indexOf("\n  function ", filterStart + 1);
const applyReviewFilters = reviewHtml.slice(filterStart, filterEnd);

function pageFunction(html, name) {
  const start = html.search(new RegExp(`  (?:async )?function ${name}\\(`));
  assert.notEqual(start, -1, `Missing function ${name}`);
  const tail = html.slice(start + 1);
  const end = tail.search(/\n  (?:async )?function /);
  assert.notEqual(end, -1, `Missing function boundary after ${name}`);
  return html.slice(start, start + 1 + end);
}

test("shared options retain labels, selection and plain text rendering", () => {
  const select = node("select");
  helpers().addOptions(select, ["new", "interview", "custom"], {
    new: "Neu", interview: "<b>Gespräch</b>"
  }, "interview");
  assert.deepEqual(select.children.map(option => [option.value, option.textContent, option.selected]), [
    ["new", "Neu", false], ["interview", "<b>Gespräch</b>", true], ["custom", "custom", false]
  ]);
});

test("review decisions update the backing jobs and preserve navigation", () => {
  for (const filtered of [false, true]) {
    const jobs = [{id: "one", workflow_status: "new", is_new: true}, {id: "two"}];
    let renders = 0;
    let filterCalls = 0;
    const context = vm.createContext({
      jobs, visibleJobs: jobs.filter(() => true), currentIndex: 0,
      element: () => ({value: filtered ? "new" : ""}),
      render() { renders += 1; },
      applyFilters(reset) { assert.equal(reset, false); filterCalls += 1; }
    });
    vm.runInContext(pageFunction(reviewHtml, "applyWorkflowResult") +
      '\napplyWorkflowResult(visibleJobs[0], {workflow_status: "interesting", application_tracked: false});', context);
    assert.equal(jobs[0].workflow_status, "interesting");
    assert.equal(jobs[0].is_new, false);
    assert.equal(jobs[0].application_tracked, false);
    assert.equal(filterCalls, filtered ? 1 : 0);
    assert.equal(renders, filtered ? 0 : 1);
    assert.equal(context.currentIndex, filtered ? 0 : 1);
  }
});

test("review decisions retain updates for cards sharing a persisted job ID", () => {
  const jobs = [
    {id: "merged:1", workflow_status: "new", is_new: true},
    {id: "merged:1", workflow_status: "new", is_new: true}
  ];
  vm.runInNewContext(pageFunction(reviewHtml, "applyWorkflowResult") +
    '\napplyWorkflowResult(visibleJobs[0], {workflow_status: "applied", application_tracked: true});', {
      jobs, visibleJobs: [jobs[1]], currentIndex: 0,
      element: () => ({value: ""}), render() {}
    });
  for (const job of jobs) {
    assert.equal(job.workflow_status, "applied");
    assert.equal(job.application_tracked, true);
    assert.equal(job.is_new, false);
  }
});

const applicationsHtml = fs.readFileSync(path.join(__dirname, "../job_finder/applications.html"), "utf8");

test("application saves disable all action buttons through the reload", async () => {
  const buttons = [{disabled: false}, {disabled: false}];
  const payload = {job_id: "job:1", previous_status: "applied"};
  let reloaded = false;
  const save = vm.runInNewContext(pageFunction(applicationsHtml, "saveChange") + "\nsaveChange;", {
    async postJson(path, actual, message) {
      assert.ok(buttons.every(button => button.disabled));
      assert.equal(path, "/api/history");
      assert.equal(actual, payload);
      assert.equal(message, "fallback");
    },
    async load() { assert.ok(buttons.every(button => button.disabled)); reloaded = true; },
    showError(error) { throw error; }
  });
  await save(buttons, "/api/history", payload, "fallback");
  assert.equal(reloaded, true);
  assert.ok(buttons.every(button => !button.disabled));
});

test("failed application saves restore buttons and keep the current view", async () => {
  const buttons = [{disabled: false}, {disabled: false}];
  const failure = new Error("Verlauf wurde zwischenzeitlich geändert");
  const errors = [];
  const save = vm.runInNewContext(pageFunction(applicationsHtml, "saveChange") + "\nsaveChange;", {
    async postJson() { throw failure; },
    async load() { assert.fail("Failed saves must not reload"); },
    showError(error) { errors.push(error); }
  });
  await save(buttons, "/api/history", {}, "fallback");
  assert.deepEqual(errors, [failure]);
  assert.ok(buttons.every(button => !button.disabled));
});

test("every page's inline JavaScript parses with the shared helpers", () => {
  for (const filename of ["landing.html", "review.html", "applications.html"]) {
    const html = fs.readFileSync(path.join(__dirname, "../job_finder", filename), "utf8");
    for (const [, script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
      assert.doesNotThrow(() => new vm.Script(source + "\n" + script, {filename}));
    }
  }
});

function filteredReviewIds(rows, status = "new") {
  const controls = {
    "status-filter": {value: status}, "role-filter": {value: ""},
    "search-filter": {value: ""}, "international-filter": {checked: false},
    "junior-hybrid-filter": {checked: false}
  };
  return Array.from(vm.runInNewContext(applyReviewFilters +
    "\napplyFilters(); visibleJobs.map(job => job.id);", {
      jobs: rows, visibleJobs: [], currentIndex: 0,
      element: id => controls[id], render() {}
    }));
}

test("Neu retains unprocessed jobs across later runs and excludes every decided status", () => {
  assert.match(reviewHtml, /<option value="new">Neu<\/option>/);
  const rows = [
    {id: "fresh", workflow_status: "new", is_new: true},
    {id: "previous-run", workflow_status: "new", is_new: false},
    ...["review", "interesting", "inquiry", "ignored", "applied", "response",
        "interview", "rejected", "no_response", "offer", "closed"].map(status =>
      ({id: status, workflow_status: status, is_new: true}))
  ];
  assert.deepEqual(filteredReviewIds(rows), ["fresh", "previous-run"]);
  rows[0].workflow_status = "interesting";
  assert.deepEqual(filteredReviewIds(rows), ["previous-run"]);
  rows[0].workflow_status = "new";
  rows[0].is_new = false;
  assert.deepEqual(filteredReviewIds(rows), ["fresh", "previous-run"]);
});

test("review optional filters and explicit statuses remain effective", () => {
  const rows = [
    {id: "pending", workflow_status: "new", is_new: false},
    {id: "international", workflow_status: "new", international: true},
    {id: "hybrid", workflow_status: "new", location_precheck: "Junior-Hybrid: Test"},
    {id: "saved", workflow_status: "interesting", is_new: false}
  ];
  assert.deepEqual(filteredReviewIds(rows), ["pending"]);
  assert.deepEqual(filteredReviewIds(rows, "interesting"), ["saved"]);
  assert.deepEqual(filteredReviewIds(rows, ""), ["pending", "saved"]);
});
