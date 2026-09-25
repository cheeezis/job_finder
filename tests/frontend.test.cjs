const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {node, page} = require("./frontend_environment.cjs");

const source = fs.readFileSync(path.join(__dirname, "../job_finder/app.js"), "utf8");

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
    const view = page("review");
    const jobs = [{id: "one", workflow_status: "new", is_new: true}, {id: "two"}];
    view.context.testJobs = jobs;
    view.run("jobs = testJobs; visibleJobs = jobs.filter(() => true); currentIndex = 0;");
    view.elements.get("status-filter").value = filtered ? "new" : "";
    let renders = 0;
    let filterCalls = 0;
    view.context.render = () => { renders += 1; };
    view.context.applyFilters = reset => { assert.equal(reset, false); filterCalls += 1; };
    view.context.applyWorkflowResult(jobs[0], {workflow_status: "interesting", application_tracked: false});
    assert.equal(jobs[0].workflow_status, "interesting");
    assert.equal(jobs[0].is_new, false);
    assert.equal(jobs[0].application_tracked, false);
    assert.equal(filterCalls, filtered ? 1 : 0);
    assert.equal(renders, filtered ? 0 : 1);
    assert.equal(view.run("currentIndex"), filtered ? 0 : 1);
  }
});

test("review decisions retain updates for cards sharing a persisted job ID", () => {
  const view = page("review");
  const jobs = [0, 1].map(() => ({id: "merged:1", workflow_status: "new", is_new: true}));
  view.context.testJobs = jobs;
  view.run("jobs = testJobs; visibleJobs = [jobs[1]];");
  view.elements.get("status-filter").value = "";
  view.context.render = () => {};
  view.context.applyWorkflowResult(jobs[1], {workflow_status: "applied", application_tracked: true});
  for (const job of jobs) {
    assert.equal(job.workflow_status, "applied");
    assert.equal(job.application_tracked, true);
    assert.equal(job.is_new, false);
  }
});

test("application saves disable all action buttons through the reload", async () => {
  const buttons = [{disabled: false}, {disabled: false}];
  const payload = {job_id: "job:1", previous_status: "applied"};
  let reloaded = false;
  const view = page("applications", {
    async postJson(path, actual, message) {
      assert.ok(buttons.every(button => button.disabled));
      assert.equal(path, "/api/history");
      assert.equal(actual, payload);
      assert.equal(message, "fallback");
    }, showError(error) { throw error; }
  });
  view.context.load = async () => { assert.ok(buttons.every(button => button.disabled)); reloaded = true; };
  await view.context.saveChange(buttons, "/api/history", payload, "fallback");
  assert.equal(reloaded, true);
  assert.ok(buttons.every(button => !button.disabled));
});

test("failed application saves restore buttons and keep the current view", async () => {
  const buttons = [{disabled: false}, {disabled: false}];
  const failure = new Error("Verlauf wurde zwischenzeitlich geändert");
  const errors = [];
  const view = page("applications", {
    async postJson() { throw failure; }, showError(error) { errors.push(error); }
  });
  view.context.load = async () => { assert.fail("Failed saves must not reload"); };
  await view.context.saveChange(buttons, "/api/history", {}, "fallback");
  assert.deepEqual(errors, [failure]);
  assert.ok(buttons.every(button => !button.disabled));
});

function filteredReviewIds(rows, status = "new") {
  const view = page("review");
  view.context.testJobs = rows;
  view.run("jobs = testJobs;");
  view.elements.get("status-filter").value = status;
  view.context.applyFilters();
  return Array.from(view.run("visibleJobs.map(job => job.id)"));
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

test("all pages execute their external scripts and register actions", () => {
  for (const [name, id, event] of [["landing", "manual-import-form", "submit"],
    ["review", "mark-interesting", "click"], ["applications", "archive-toggle", "click"]]) {
    const view = page(name);
    assert.equal(view.elements.get(id).listeners[event].length, 1);
    const html = fs.readFileSync(path.join(__dirname, `../job_finder/${name}.html`), "utf8");
    assert.ok(!html.includes("<script>"));
    assert.ok(html.includes(`src="/${name}.js"`));
  }
});

test("landing submits only the entered URL and navigates to the imported job", async () => {
  const view = page("landing", {async postJson(route, payload) {
    assert.equal(route, "/api/manual-import");
    assert.equal(payload.url, "https://example.test/job");
    return {job_id: "manual:1"};
  }});
  view.elements.get("manual-url").value = "https://example.test/job";
  await view.elements.get("manual-import-form").emit("submit");
  assert.equal(view.context.window.location.href, "/review?job=manual%3A1");
});

test("complete review loading renders a card and applies a decision", async () => {
  const job = {id: "job:1", title: "Developer", company: "Example", workflow_status: "new",
    role_group: "ai_business_analysis", role_label: "Business Analyst (KI)"};
  const view = page("review", {async postJson() { return {workflow_status: "interesting"}; }},
    async () => ({ok: true, json: async () => ({recommendations: [job], workflow_statuses: ["new", "interesting"]})}));
  await new Promise(setImmediate);
  assert.equal(view.elements.get("title").textContent, "Developer");
  assert.equal(view.elements.get("role-badge").textContent, "Business Analyst (KI)");
  assert.equal(view.elements.get("card").hidden, false);
  await view.elements.get("mark-interesting").emit("click");
  assert.equal(job.workflow_status, "interesting");
  assert.equal(view.elements.get("card").hidden, true);
});

test("complete application loading renders history and keeps event identity on edit/delete", async () => {
  const posts = [];
  const event = {status: "applied", occurred_on: "2026-09-01", event_index: 0};
  const job = {id: "job:1", title: "Developer", company: "Example", active: true, workflow_status: "applied", workflow_history: [event]};
  const view = page("applications", {async postJson(route, payload) { posts.push([route, payload]); }},
    async () => ({ok: true, json: async () => ({applications: [job], completed_applications: [], statistics: {total: 1}, application_statuses: ["applied", "interview"], workflow_statuses: ["new", "applied", "interview"]})}));
  await new Promise(setImmediate);
  assert.equal(view.elements.get("applications").children.length, 1);
  view.context.load = async () => {};
  const item = view.context.historyEventForm(job.id, event);
  const form = item.children[0];
  await form.emit("submit");
  await form.children[3].children[1].emit("click");
  assert.deepEqual(posts.map(([route]) => route), ["/api/history", "/api/history/delete"]);
  for (const [, payload] of posts) {
    assert.equal(payload.job_id, job.id);
    assert.equal(payload.previous_status, "applied");
    assert.equal(payload.event_index, 0);
    assert.equal(payload.previous_occurred_on, "2026-09-01");
  }
});

// Payloads come from the page's VM realm; compare them as plain JSON values.
const plain = value => JSON.parse(JSON.stringify(value));

function applicationsPage(posts) {
  const view = page("applications", {async postJson(route, payload) { posts.push([route, payload]); }},
    async () => ({ok: true, json: async () => ({applications: [], completed_applications: [], statistics: {total: 0}, application_statuses: ["applied", "interview"], workflow_statuses: ["new", "applied", "interview"]})}));
  view.context.load = async () => {};
  return view;
}

test("new application events default to a required local today and send named fields", async () => {
  const posts = [];
  const view = applicationsPage(posts);
  await new Promise(setImmediate);
  const form = view.context.eventForm("job:1");
  const [statusLabel, dateLabel, appointmentLabel] = form.children;
  const [select] = statusLabel.children;
  const [date] = dateLabel.children;
  const now = new Date();
  const today = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((part, index) => String(part).padStart(index ? 2 : 4, "0")).join("-");
  assert.equal(select.name, "workflow_status");
  assert.equal(date.name, "occurred_on");
  assert.equal(date.type, "date");
  assert.equal(date.required, true);
  assert.equal(date.value, today);
  assert.equal(appointmentLabel.hidden, true);
  await form.emit("submit");
  assert.deepEqual(plain(posts), [["/api/status", {job_id: "job:1", workflow_status: "applied", occurred_on: today, scheduled_for: null}]]);
});

test("editing an event with an unknown date keeps it unknown and sends the previous values", async () => {
  const posts = [];
  const view = applicationsPage(posts);
  await new Promise(setImmediate);
  const event = {status: "interview", occurred_on: null, event_index: 2, scheduled_for: "2026-10-01T10:00"};
  const form = view.context.historyEventForm("job:1", event).children[0];
  const [statusLabel, dateLabel, appointmentLabel] = form.children;
  assert.equal(statusLabel.children[0].name, undefined);
  assert.equal(dateLabel.children[0].required, undefined);
  assert.equal(dateLabel.children[0].value, "");
  assert.equal(appointmentLabel.hidden, false);
  assert.equal(appointmentLabel.children[0].value, "2026-10-01T10:00");
  await form.emit("submit");
  assert.deepEqual(plain(posts), [["/api/history", {
    job_id: "job:1", event_index: 2, previous_status: "interview", previous_occurred_on: null,
    previous_scheduled_for: "2026-10-01T10:00", workflow_status: "interview", occurred_on: null,
    scheduled_for: "2026-10-01T10:00"
  }]]);
});

test("appointments are offered and sent only for interviews", async () => {
  const posts = [];
  const view = applicationsPage(posts);
  await new Promise(setImmediate);
  const forms = [view.context.eventForm("job:1"),
    view.context.historyEventForm("job:1", {status: "applied", occurred_on: "2026-09-01", event_index: 0}).children[0]];
  for (const form of forms) {
    const [statusLabel, , appointmentLabel] = form.children;
    const [select] = statusLabel.children;
    appointmentLabel.children[0].value = "2026-10-02T09:30";
    select.value = "interview";
    await select.emit("change");
    assert.equal(appointmentLabel.hidden, false);
    await form.emit("submit");
    select.value = "applied";
    await select.emit("change");
    assert.equal(appointmentLabel.hidden, true);
    await form.emit("submit");
  }
  assert.deepEqual(posts.map(([, payload]) => payload.scheduled_for), ["2026-10-02T09:30", null, "2026-10-02T09:30", null]);
});
