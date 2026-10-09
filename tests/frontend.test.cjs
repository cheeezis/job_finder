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

test("a card names applications at the same company and tells a waiting job when to decide", () => {
  assert.match(reviewHtml, /<button id="mark-waiting" class="waiting">Warteliste<\/button>/);
  const view = page("review");
  const hint = view.elements.get("company-applications");
  const shown = job => {
    view.context.testJob = job;
    view.run("renderCompanyApplications(testJob);");
    return hint.hidden ? null : hint.textContent;
  };
  const running = {title: "Cloud Engineer", workflow_status: "interview", open: true};
  const finished = {title: "DevOps Engineer", workflow_status: "rejected", open: false};

  assert.equal(shown({company: "Nordlicht", workflow_status: "new", company_applications: [running, finished]}),
    "Bei Nordlicht läuft schon deine Bewerbung als Cloud Engineer (Interview).");
  assert.equal(shown({company: "Nordlicht", workflow_status: "waiting", company_applications: [finished]}),
    "Deine Bewerbung bei Nordlicht ist abgeschlossen: DevOps Engineer (Absage) – jetzt entscheiden.");
  assert.equal(shown({company: "Nordlicht", workflow_status: "new", company_applications: [finished]}), null);
  assert.equal(shown({company: "Nordlicht", workflow_status: "waiting", company_applications: []}), null);
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

test("direct review links reveal the requested card across every optional filter and source alias", () => {
  const view = page("review");
  view.context.testJobs = [{id: "application:1", recommendation_id: "source:1", title: "Requested job",
    company: "Employer", workflow_status: "rejected", international: true, location_precheck: "Junior-Hybrid: Test"}];
  view.run("jobs = testJobs;");
  view.elements.get("role-filter").value = "other-role";
  view.elements.get("search-filter").value = "other-company";
  view.context.showRequestedJob("source:1");
  assert.equal(view.elements.get("title").textContent, "Requested job");
  assert.equal(view.elements.get("card").hidden, false);
  assert.equal(view.elements.get("status-filter").value, "");
  assert.equal(view.elements.get("role-filter").value, "");
  assert.equal(view.elements.get("search-filter").value, "");
  assert.equal(view.elements.get("international-filter").checked, true);
  assert.equal(view.elements.get("junior-hybrid-filter").checked, true);
});

test("manual submissions stay in the default review even when optional filters hide similar automatic listings", () => {
  const rows = ["manual", "automatic"].map(source => ({id: source, workflow_status: "new",
    international: true, location_precheck: "Junior-Hybrid: Test", source_links: [{source, url: "https://example.test/job"}]}));
  assert.deepEqual(filteredReviewIds(rows), ["manual"]);
});

test("linking a listing offers existing applications and redirects only after saving the selected one", async () => {
  const listing = {id: "source:1", title: "Junior Engineer", company: "Recruiter", workflow_status: "new"};
  const application = {id: "application:1", title: "Junior Engineer", company: "Employer", workflow_status: "applied"};
  const view = page("review", {async postJson(route, payload) {
    assert.equal(route, "/api/application-listing");
    assert.deepEqual(plain(payload), {job_id: "source:1", application_id: "application:1"});
    assert.equal(view.elements.get("link-application-save").disabled, true);
    return {job_id: "application:1"};
  }}, async path => ({ok: true, json: async () => path === "/api/applications"
    ? {applications: [application], completed_applications: []}
    : {recommendations: [listing], workflow_statuses: ["new", "applied"]}}));
  await new Promise(setImmediate);
  await view.elements.get("link-application").emit("click");
  assert.equal(view.elements.get("link-application-dialog").open, true);
  const select = view.elements.get("link-application-select");
  assert.equal(select.value, "");
  assert.match(select.options[1].textContent, /Employer.*Junior Engineer/);
  select.value = "application:1";
  await view.elements.get("link-application-form").emit("submit");
  assert.equal(view.context.window.location.href, "/applications?job=application%3A1");
});

test("a failed association leaves the selection and dialog available for retry", async () => {
  const view = page("review", {async postJson() { throw new Error("Speichern fehlgeschlagen"); }});
  view.run('linkingJobId = "source:1";');
  view.elements.get("link-application-dialog").showModal();
  view.elements.get("link-application-select").value = "application:1";
  await view.elements.get("link-application-form").emit("submit");
  assert.equal(view.elements.get("link-application-dialog").open, true);
  assert.equal(view.elements.get("link-application-save").disabled, false);
  assert.equal(view.elements.get("link-application-select").value, "application:1");
  assert.equal(view.elements.get("link-application-status").textContent, "Speichern fehlgeschlagen");
  assert.equal(view.context.window.location.href, undefined);
});

test("application cards gather the finder's listings and the agent's pages in one links section", async () => {
  const application = {id: "application:1", title: "Engineer", company: "Employer", active: true, workflow_status: "applied",
    workflow_history: [], source_links: [{source: "original", url: "https://employer.example/job"}],
    agent_sources: ["https://employer.example/about", "javascript:alert(1)"]};
  const view = page("applications", {}, async path => ({ok: true, json: async () => path === "/api/sources"
    ? {labels: {}}
    : {applications: [application], completed_applications: [], statistics: {total: 1},
      application_statuses: ["applied"], workflow_statuses: ["applied"]}}));
  await new Promise(setImmediate);
  const card = view.elements.get("applications").children[0];
  const links = card.children.find(child => child.className === "application-links");
  const [summary, ...rest] = links.children;
  assert.equal(summary.textContent, "Links (2)");
  const groups = rest.filter(child => child.className === "link-group").map(child => child.textContent);
  assert.deepEqual(groups, ["Vom Finder gefunden", "Vom Agenten genutzt"]);
  const anchors = rest.filter(child => child.tagName === "ul").flatMap(list => list.children.map(item => item.children[0]));
  assert.deepEqual(anchors.map(link => [link.textContent, link.href]),
    [["Originalanzeige", "https://employer.example/job"], ["employer.example", "https://employer.example/about"]]);
  assert.ok(anchors.every(link => link.rel === "noopener noreferrer"));
});

test("a direct application link opens the completed archive and locates the selected card", async () => {
  const application = {id: "application:1", title: "Engineer", company: "Employer", workflow_status: "rejected", workflow_history: [],
    source_links: [{source: "listing", url: "https://example.test/job"}], agent_sources: []};
  const view = page("applications", {}, async () => ({ok: true, json: async () => ({applications: [],
    completed_applications: [application], statistics: {total: 1}, application_statuses: ["applied"], workflow_statuses: ["rejected"]})}));
  view.context.window.location.search = "?job=application%3A1";
  await new Promise(setImmediate);
  assert.equal(view.elements.get("archive").hidden, false);
  const card = view.elements.get("completed-applications").children[0];
  assert.equal(card.className, "application selected-application");
  assert.equal(card.scrolledIntoView, true);
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

test("only interview cards offer cancelling, which records a self-cancelled status", async () => {
  const posts = [];
  const view = applicationsPage(posts);
  await new Promise(setImmediate);
  const card = status => ({id: `job:${status}`, title: "T", company: "C", active: true, workflow_status: status, workflow_history: []});
  view.context.renderApplications([card("interview"), card("applied")], "applications");
  const buttons = view.elements.get("applications").children
    .map(item => item.children.find(child => child.textContent === "Gespräch absagen"));
  assert.equal(buttons[1], undefined);
  await buttons[0].emit("click");
  assert.equal(posts.length, 1);
  const [route, payload] = plain(posts)[0];
  assert.equal(route, "/api/status");
  assert.equal(payload.job_id, "job:interview");
  assert.equal(payload.workflow_status, "withdrawn");
  assert.equal(payload.scheduled_for, null);
});

test("trial days have an appointment, a card line and their own cancel button", async () => {
  const posts = [];
  const view = applicationsPage(posts);
  await new Promise(setImmediate);
  const form = view.context.eventForm("job:1");
  const [statusLabel, , appointmentLabel] = form.children;
  const [select] = statusLabel.children;
  select.value = "trial_day";
  await select.emit("change");
  assert.equal(appointmentLabel.hidden, false);

  const trial = {id: "job:trial", title: "T", company: "C", active: true, workflow_status: "trial_day",
    workflow_history: [], next_appointment_at: "2099-10-14T09:00", next_appointment_status: "trial_day"};
  view.context.renderApplications([trial], "applications");
  const card = view.elements.get("applications").children[0];
  const meta = card.children.find(child => child.className === "meta");
  assert.ok(meta.children.some(item => item.textContent.startsWith("Nächste Hospitation/Probearbeiten: ")));
  const cancel = card.children.find(child => child.className === "withdraw");
  assert.equal(cancel.textContent, "Hospitation/Probearbeiten absagen");
  await cancel.emit("click");
  assert.equal(plain(posts)[0][1].workflow_status, "withdrawn");
});

test("the funnel shows how far the applications got, each against all applications", async () => {
  const view = page("applications", {}, async path => ({ok: true, json: async () => path === "/api/sources"
    ? {labels: {}}
    : {applications: [], completed_applications: [], application_statuses: [], workflow_statuses: [],
      statistics: {total: 20, responses: 8, interviews: 4, trial_days: 2, offers: 1, open: 5, completed: 15}}}));
  await new Promise(setImmediate);
  const stages = view.elements.get("funnel").children;
  assert.deepEqual(stages.map(stage => [stage.children[0].textContent, stage.children[1].textContent]),
    [["20", "Bewerbungen"], ["8", "Antworten"], ["4", "Gespräche"], ["2", "Hospitation/Probearbeiten"], ["1", "Zusagen"]]);
  assert.deepEqual(stages.map(stage => stage.children[2].style["--share"]), ["1", "0.4", "0.2", "0.1", "0.05"]);
  const labels = view.elements.get("stats").children.map(tile => tile.children[1].textContent);
  assert.ok(!labels.includes("Gespräche") && labels.includes("Offen"));
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

function reviewWith(job) {
  return page("review", {}, async () => ({
    ok: true, json: async () => ({recommendations: [job], workflow_statuses: ["new"]})
  }));
}

const factSheetJob = {
  id: "job:1", title: "Developer", company: "Example", workflow_status: "new",
  fact_sheet: {
    model: "gpt-5-mini", complete: true, note: null, cost_eur: 0.0512,
    created_at: "2026-09-25T18:00:00+00:00",
    sheet: {
      status: {ampel: "gruen", text: "offen und aktuell"},
      berufseinstieg: {ampel: "orange", text: "Stretch, aber bewerbbar"},
      fachlicher_fit: {ampel: "gruen", text: "passt"},
      luecken: {ampel: "rot", text: "Java fehlt"},
      homeoffice_standort: {ampel: "orange", text: "vorher klären"},
      reiseanteil: {ampel: "gelb", text: "gelegentlich"},
      gehalt: {ampel: "unbekannt", text: "keine Angabe"},
      zusatz: [{thema: "Bewerbung", ampel: "hinweis", text: "Portfolio verlangt"}],
      fazit: {stufe: "erst_klaeren", text: "Erst Homeoffice klären"},
      kurzgrund: "Fachlich solide, Remote offen.",
      quellen: ["https://example.com/jobs/1", "javascript:alert(1)"]
    }
  }
};

test("the agent's fact sheet renders as plain text lines with lights and safe links", async () => {
  const view = reviewWith(structuredClone(factSheetJob));
  await new Promise(setImmediate);

  assert.equal(view.elements.get("fact-sheet").hidden, false);
  assert.equal(view.elements.get("fact-sheet-aborted").hidden, true);
  const lines = view.elements.get("fact-sheet-lines").children.map(line => line.textContent);
  assert.equal(lines.length, 8);
  assert.equal(lines[0], "🟢 Status: offen und aktuell");
  assert.equal(lines[3], "🔴 Lücken: Java fehlt");
  assert.equal(lines[7], "⚠️ Bewerbung: Portfolio verlangt");
  const verdict = view.elements.get("fact-sheet-verdict");
  assert.equal(verdict.textContent, "Fazit: Erst Homeoffice klären");
  assert.equal(verdict.className, "fact-sheet-verdict verdict-erst_klaeren");
  const links = view.elements.get("fact-sheet-sources").children.filter(child => child.tagName === "a");
  assert.deepEqual(links.map(link => [link.textContent, link.href]),
    [["example.com", "https://example.com/jobs/1"]]);
  assert.match(view.elements.get("fact-sheet-meta").textContent, /^gpt-5-mini · 5,1 Cent · /);
});

test("an aborted fact sheet names its reason and a missing one stays hidden", async () => {
  const aborted = {...factSheetJob, fact_sheet: {
    ...factSheetJob.fact_sheet, complete: false, sheet: null,
    note: "Stelle abgebrochen: 8 Modellaufrufe erreicht"
  }};
  let view = reviewWith(aborted);
  await new Promise(setImmediate);
  assert.equal(view.elements.get("fact-sheet").hidden, false);
  assert.equal(view.elements.get("fact-sheet-aborted").textContent,
    "⚠️ Stelle abgebrochen: 8 Modellaufrufe erreicht");
  assert.equal(view.elements.get("fact-sheet-lines").children.length, 0);

  view = reviewWith({...factSheetJob, fact_sheet: undefined});
  await new Promise(setImmediate);
  assert.equal(view.elements.get("fact-sheet").hidden, true);
});

function reviewWithPosts(rows, posts, {failNote = false} = {}) {
  return page("review", {
    async postJson(route, payload) {
      posts.push([route, {...payload}]);
      if (route === "/api/review-note") {
        if (failNote) throw new Error("Notiz konnte nicht gespeichert werden");
        return {review_note: payload.review_note};
      }
      return {workflow_status: payload.workflow_status, application_tracked: false};
    },
    showError(error) { posts.push(["error", error.message]); }
  }, async () => ({ok: true, json: async () => ({
    recommendations: rows, workflow_statuses: ["new", "interesting", "ignored"]
  })}));
}

test("a changed note is saved before the decision and stays with the job", async () => {
  const posts = [];
  const job = {id: "job:1", title: "Developer", company: "Example", workflow_status: "new"};
  const view = reviewWithPosts([job], posts);
  await new Promise(setImmediate);
  assert.equal(view.elements.get("save-note").hidden, true);

  view.elements.get("review-note").value = "  Java-Pflicht, will ich nicht ";
  await view.elements.get("mark-ignored").emit("click");

  assert.deepEqual(posts.map(([route]) => route), ["/api/review-note", "/api/review-status"]);
  assert.deepEqual(posts[0][1], {job_id: "job:1", review_note: "Java-Pflicht, will ich nicht"});
  assert.equal(job.review_note, "Java-Pflicht, will ich nicht");
  assert.equal(job.workflow_status, "ignored");
});

test("paging saves a changed note and brings it back; an unchanged note sends nothing", async () => {
  const posts = [];
  const rows = [
    {id: "job:1", title: "One", company: "A", workflow_status: "new"},
    {id: "job:2", title: "Two", company: "B", workflow_status: "new", review_note: "Alt"}
  ];
  const view = reviewWithPosts(rows, posts);
  await new Promise(setImmediate);

  view.elements.get("review-note").value = "Gute Firma";
  await view.elements.get("next").emit("click");
  assert.equal(view.elements.get("title").textContent, "Two");
  assert.equal(view.elements.get("review-note").value, "Alt");
  await view.elements.get("previous").emit("click");

  assert.equal(view.elements.get("review-note").value, "Gute Firma");
  assert.deepEqual(posts.map(([route, payload]) => [route, payload.job_id]),
    [["/api/review-note", "job:1"]]);
});

test("decided cards save the note by button; a failed save keeps the card", async () => {
  const posts = [];
  const decided = {id: "job:1", title: "Developer", company: "Example", workflow_status: "interesting"};
  let view = reviewWithPosts([decided], posts);
  await new Promise(setImmediate);
  view.elements.get("status-filter").value = "";
  await view.elements.get("status-filter").emit("change");
  assert.equal(view.elements.get("save-note").hidden, false);

  view.elements.get("review-note").value = "Nachgefragt am 26.09.";
  await view.elements.get("save-note").emit("click");
  assert.equal(decided.review_note, "Nachgefragt am 26.09.");
  assert.equal(view.elements.get("note-status").textContent, "Notiz gespeichert");

  const failing = [];
  const fresh = {id: "job:2", title: "Tester", company: "Example", workflow_status: "new"};
  view = reviewWithPosts([fresh], failing, {failNote: true});
  await new Promise(setImmediate);
  view.elements.get("review-note").value = "geht verloren?";
  await view.elements.get("mark-ignored").emit("click");

  assert.deepEqual(failing.map(([route]) => route), ["/api/review-note", "error"]);
  assert.equal(fresh.workflow_status, "new");
  assert.equal(view.elements.get("review-note").value, "geht verloren?");
});

test("the review lists the agent's verdicts first and unjudged cards in the middle", async () => {
  const judged = stufe => ({complete: true, cost_eur: 0.04, sheet: {fazit: {stufe, text: stufe}}});
  const rows = [
    {id: "streichen", match_percent: 90, fact_sheet: judged("streichen")},
    {id: "ohne", match_percent: 80},
    {id: "abgebrochen", match_percent: 85, fact_sheet: {complete: false, sheet: null, note: "Limit"}},
    {id: "eher", match_percent: 70, fact_sheet: judged("eher_streichen")},
    {id: "klaeren-schwach", match_percent: 40, fact_sheet: judged("erst_klaeren")},
    {id: "klaeren-stark", match_percent: 60, fact_sheet: judged("erst_klaeren")},
    {id: "bewerben", match_percent: 30, fact_sheet: judged("bewerben")}
  ].map(job => ({title: job.id, company: "Example", workflow_status: "new", ...job}));
  const view = page("review", {}, async () => ({
    ok: true, json: async () => ({recommendations: rows, workflow_statuses: ["new"]})
  }));
  await new Promise(setImmediate);

  assert.deepEqual(Array.from(view.run("visibleJobs.map(job => job.id)")), [
    "bewerben", "klaeren-stark", "klaeren-schwach", "abgebrochen", "ohne", "eher", "streichen"
  ]);
});
