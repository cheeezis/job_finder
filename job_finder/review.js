  const statusLabels = JobFinder.statusLabels;
  const sourceLabels = {
    ...JobFinder.sourceLabels, original: "Originalanzeige",
    german_tech_jobs: "GermanTechJobs", manual: "Manuell hinzugefügt"
  };
  const reviewStatuses = new Set(["review", "interesting", "inquiry", "waiting", "ignored"]);
  let jobs = [];
  let visibleJobs = [];
  let currentIndex = 0;
  let routeOrigin = "";
  let undoDecision = null;
  // The job whose note is on screen; a change is saved before any way off its card.
  let noteJobId = null;
  let linkingJobId = null;
  // Cards of offline listings are most of the data and hidden by the default
  // filter; they load once "Alle Status" or "Nicht interessant" is chosen.
  let archiveRequest = null;
  let archiveLoading = false;

  const {element, make, addOptions, appendSourceLinks, postJson, safeUrl, showError} = JobFinder;
  // Traffic lights of the agent's fact sheet (job_finder/agent/fact_sheet.py).
  const factSheetLights = {
    gruen: "🟢", gelb: "🟡", orange: "🟠", rot: "🔴", unbekannt: "⚪", hinweis: "⚠️"
  };
  const factSheetLines = [
    ["status", "Status"], ["berufseinstieg", "Berufseinstieg"],
    ["fachlicher_fit", "Fachlicher Fit"], ["luecken", "Lücken"],
    ["homeoffice_standort", "Homeoffice / Standort"], ["reiseanteil", "Reiseanteil"],
    ["gehalt", "Gehalt"]
  ];
  // The review goes by the agent's verdict; a card it has not judged, or whose
  // fact sheet was aborted, sits in the middle so a good one is not buried.
  const verdictOrder = {bewerben: 0, erst_klaeren: 1, eher_streichen: 3, streichen: 4};
  // What changed since a fact sheet was written (job_finder/agent/basis.py).
  const basisLabels = {
    profile: "Profil", rules: "Regeln", ad: "Anzeige", model: "Modell", graph: "Ablauf des Agenten"
  };
  const withoutVerdict = 2;

  function verdictRank(job) {
    const entry = job.fact_sheet;
    const verdict = entry?.complete ? entry.sheet?.fazit?.stufe : null;
    return Object.hasOwn(verdictOrder, verdict ?? "") ? verdictOrder[verdict] : withoutVerdict;
  }

  function byRank(a, b) {
    return verdictRank(a) - verdictRank(b)
      || (b.match_percent ?? -1) - (a.match_percent ?? -1)
      || String(b.published_at || "").localeCompare(String(a.published_at || ""))
      || String(b.first_seen_at || "").localeCompare(String(a.first_seen_at || ""));
  }

  function setText(id, value) {
    element(id).textContent = value || "";
  }

  async function requestRerun() {
    const job = visibleJobs[currentIndex];
    element("fact-sheet-rerun").disabled = true;
    try {
      await postJson("/api/fact-sheet-rerun", {job_id: job.id}, "Neubewertung konnte nicht angefordert werden");
    } catch (error) {
      element("fact-sheet-rerun").disabled = false;
      throw error;
    }
    for (const item of jobs.filter(item => item.id === job.id)) item.fact_sheet_rerun = true;
    element("fact-sheet-rerun").textContent = "Neu bewerten angefordert";
    setText("fact-sheet-rerun-status", "Der nächste Agent-Lauf schreibt den Steckbrief neu.");
  }

  function renderFactSheet(job) {
    const entry = job.fact_sheet;
    const sheet = entry?.complete ? entry.sheet : null;
    element("fact-sheet").hidden = !entry;
    const aborted = element("fact-sheet-aborted");
    aborted.hidden = !entry || entry.complete;
    // Stored reasons explain themselves, e.g. "Stelle abgebrochen: 8 Modellaufrufe erreicht".
    aborted.textContent = entry && !entry.complete
      ? `⚠️ ${entry.note || "Steckbrief abgebrochen"}${entry.retryable ? " · wird im nächsten Lauf noch einmal versucht" : ""}`
      : "";
    const changed = (entry?.outdated || []).map(part => basisLabels[part] || part);
    element("fact-sheet-outdated").hidden = !changed.length;
    element("fact-sheet-outdated").textContent = changed.length
      ? `⚠️ Veraltet: ${changed.join(", ")} seit diesem Steckbrief geändert. Bei Bedarf neu bewerten lassen.`
      : "";
    const rerun = element("fact-sheet-rerun");
    rerun.disabled = Boolean(job.fact_sheet_rerun);
    rerun.textContent = job.fact_sheet_rerun ? "Neu bewerten angefordert" : "Neu bewerten";
    setText("fact-sheet-rerun-status", "");
    const rows = sheet ? [
      ...factSheetLines.map(([key, label]) => [label, sheet[key]]),
      ...(sheet.zusatz || []).map(line => [line.thema, line])
    ] : [];
    element("fact-sheet-lines").replaceChildren(...rows.filter(([, line]) => line).map(
      ([label, line]) => make("li", `${factSheetLights[line.ampel] || "⚪"} ${label}: ${line.text}`)
    ));
    const verdict = element("fact-sheet-verdict");
    verdict.className = sheet ? `fact-sheet-verdict verdict-${sheet.fazit.stufe}` : "fact-sheet-verdict";
    verdict.textContent = sheet ? `Fazit: ${sheet.fazit.text}` : "";
    setText("fact-sheet-reason", sheet ? `Kurzgrund: ${sheet.kurzgrund}` : "");
    const links = (sheet?.quellen || []).map(safeUrl).filter(Boolean)
      .map(url => JobFinder.externalLink(url, new URL(url).hostname));
    element("fact-sheet-sources").replaceChildren(...(links.length ? [make("span", "Quellen: "), ...links] : []));
    setText("fact-sheet-meta", entry
      ? `${entry.model} · ${(entry.cost_eur * 100).toFixed(1).replace(".", ",")} Cent · ${displayDate(entry.created_at)}`
      : "");
  }

  function routeDestination(job) {
    const ignored = new Set(["deutschland", "germany", "bundesweit", "deutschlandweit", "remote", "hybrid"]);
    return (job.locations || [])
      .flatMap(value => String(value).split(","))
      .map(value => value.trim().replace(/^u\.\s*a\.\s*/i, ""))
      .find(value => value && !ignored.has(value.toLocaleLowerCase("de"))) || "";
  }

  function renderRouteLink(job) {
    const link = element("route-link");
    const isJuniorHybridException = String(job.location_precheck || "")
      .startsWith("Junior-Hybrid");
    const isManualHybridConflict = job.prefilter_warning === "Ort/Remote passt nicht"
      && job.work_mode === "hybrid";
    const destination = routeDestination(job);
    link.hidden = !(routeOrigin && destination && (
      isJuniorHybridException || isManualHybridConflict
    ));
    if (link.hidden) {
      link.removeAttribute("href");
      return;
    }
    const parameters = new URLSearchParams({
      api: "1", origin: `${routeOrigin}, Deutschland`, destination: `${destination}, Deutschland`
    });
    link.href = `https://www.google.com/maps/dir/?${parameters}`;
  }

  function displayDate(value) {
    if (!value) return "nicht angegeben";
    const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("de-DE");
  }

  function renderNote(job) {
    noteJobId = job.id;
    element("review-note").value = job.review_note || "";
    element("note-status").textContent = "";
    // A new card saves its note with the decision; decided ones need a button of their own.
    element("save-note").hidden = !job.application_tracked
      && ["new", "review"].includes(job.workflow_status);
  }

  function noteJob() {
    return jobs.find(job => job.id === noteJobId);
  }

  function noteChanged() {
    const job = noteJob();
    return Boolean(job) && element("review-note").value.trim() !== (job.review_note || "");
  }

  async function saveNote() {
    if (!noteChanged()) return;
    const job = noteJob();
    const result = await postJson("/api/review-note", {
      job_id: job.id, review_note: element("review-note").value.trim()
    }, "Notiz konnte nicht gespeichert werden");
    for (const item of jobs) {
      if (item.id === job.id) item.review_note = result.review_note;
    }
    element("note-status").textContent = "Notiz gespeichert";
  }

  // Leaving the card by paging or filtering saves a changed note first;
  // if that fails, the card and the note stay.
  // Another position at the same company: name the applications still running,
  // or tell a waiting card that the one it waited for has finished.
  function renderCompanyApplications(job) {
    const applications = job.company_applications || [];
    const running = applications.filter(application => application.open);
    const named = list => list.map(application =>
      `${application.title} (${statusLabels[application.workflow_status] || application.workflow_status})`).join(", ");
    let text = "";
    if (running.length) {
      text = `Bei ${job.company} läuft schon deine Bewerbung als ${named(running)}.`;
    } else if (job.workflow_status === "waiting" && applications.length) {
      text = `Deine Bewerbung bei ${job.company} ist abgeschlossen: ${named(applications)} – jetzt entscheiden.`;
    }
    const hint = element("company-applications");
    hint.hidden = !text;
    hint.textContent = text;
  }

  function leaveCard(action) {
    return saveNote().then(action).catch(showError);
  }

  function applyFilters(resetPosition = true) {
    visibleJobs = filteredJobs();
    currentIndex = resetPosition
      ? 0
      : Math.min(currentIndex, Math.max(visibleJobs.length - 1, 0));
    render();
    if (["", "ignored"].includes(element("status-filter").value)) loadArchive().catch(showError);
  }

  function loadArchive() {
    archiveRequest ??= (async () => {
      archiveLoading = true;
      try {
        const response = await fetch("/api/recommendations?archived=1");
        if (!response.ok) throw new Error("Nicht mehr verfügbare Stellen konnten nicht geladen werden");
        const result = await response.json();
        const known = new Set(jobs.map(job => job.id));
        jobs = [...jobs, ...result.recommendations.filter(job => !known.has(job.id))].sort(byRank);
      } catch (error) {
        archiveRequest = null;
        throw error;
      } finally {
        archiveLoading = false;
      }
      // Keep the card on screen, and with it any note being typed.
      const current = visibleJobs[currentIndex];
      visibleJobs = filteredJobs();
      const index = visibleJobs.indexOf(current);
      if (index < 0) {
        currentIndex = 0;
        render();
        return;
      }
      currentIndex = index;
      setText("counter", `${currentIndex + 1} von ${visibleJobs.length}`);
      element("previous").disabled = currentIndex === 0;
      element("next").disabled = currentIndex === visibleJobs.length - 1;
    })();
    return archiveRequest;
  }

  function filteredJobs() {
    const status = element("status-filter").value;
    const role = element("role-filter").value;
    const query = element("search-filter").value.trim().toLocaleLowerCase("de");
    const showInternational = element("international-filter").checked;
    const showJuniorHybrid = element("junior-hybrid-filter").checked;
    return jobs.filter(job => {
      const statusMatches = !status || job.workflow_status === status;
      const manuallyAdded = (job.source_links || []).some(link => link.source === "manual");
      return statusMatches &&
      (!role || job.role_group === role) &&
      (manuallyAdded || showInternational || !job.international) &&
      (manuallyAdded || showJuniorHybrid || !String(job.location_precheck || "").startsWith("Junior-Hybrid")) &&
      (!query || `${job.title} ${job.company}`.toLocaleLowerCase("de").includes(query));
    });
  }

  function render() {
    element("undo-ignored").hidden = !undoDecision;
    const hasJobs = visibleJobs.length > 0;
    element("card").hidden = !hasJobs;
    element("navigation").hidden = !hasJobs;
    element("message").hidden = hasJobs;
    if (!hasJobs) {
      element("message").textContent = archiveLoading
        ? "Weitere Stellen werden geladen …"
        : jobs.length
          ? "Keine Stellen passen zu diesen Filtern."
          : "Noch keine Stellen vorhanden. Starte zuerst den Job Finder.";
      setText("counter", "0 Stellen");
      return;
    }

    const job = visibleJobs[currentIndex];
    setText("counter", `${currentIndex + 1} von ${visibleJobs.length}`);
    setText("score", job.current_snapshot_missing
      ? "Vorfilter: aktuell nicht verfügbar"
      : `Vorfilter: ${job.match_percent ?? 0}/100`);
    setText("role-badge", job.current_snapshot_missing
      ? "Vorgemerkt"
      : job.role_label || "Allgemeine IT");
    element("new-badge").hidden = job.workflow_status !== "new";
    setText("title", job.title);
    setText("company", job.company || "Arbeitgeber unbekannt");
    renderCompanyApplications(job);
    setText("location", `Ort: ${(job.locations || []).join(", ") || "unbekannt"}`);
    const remote = job.remote_percentage != null
      ? `${job.remote_percentage}%`
      : job.work_mode === "hybrid" ? "Homeoffice" : "nicht angegeben";
    setText("remote", `Remote: ${remote}`);
    setText("published", `Veröffentlicht: ${displayDate(job.published_at)}`);
    const freshness = element("freshness");
    freshness.hidden = !job.cache_stale;
    freshness.textContent = job.cache_stale ? `Cache-Fallback · Stand: ${displayDate(job.fetched_at)}` : "";
    setText("current-status", `Status: ${statusLabels[job.workflow_status] || job.workflow_status}`);
    setText("role-group", job.current_snapshot_missing
      ? "aktuell nicht verfügbar"
      : job.role_label || "Allgemeine IT");
    setText("experience-level", job.current_snapshot_missing
      ? "aktuell nicht verfügbar"
      : job.experience_level || "keine Jahresanforderung erkannt");
    setText("location-precheck", job.current_snapshot_missing
      ? "Quelle hat die Stelle im aktuellen Lauf nicht geliefert"
      : job.location_precheck || "passt zur Standortregel");
    renderFactSheet(job);
    renderNote(job);
    const applicationTracked = Boolean(job.application_tracked);
    for (const id of ["mark-interesting", "mark-inquiry", "mark-waiting", "mark-ignored", "mark-applied"]) {
      element(id).hidden = applicationTracked;
    }
    element("application-link").hidden = !applicationTracked;
    element("application-link").href = `/applications?job=${encodeURIComponent(job.id)}`;
    element("link-application").hidden = applicationTracked;
    const warning = element("prefilter-warning");
    warning.hidden = !job.prefilter_warning;
    warning.textContent = job.prefilter_warning ? `Hinweis aus dem Vorfilter: ${job.prefilter_warning}` : "";
    const links = element("job-links");
    links.replaceChildren();
    appendSourceLinks(links, job, sourceLabels, true);
    renderRouteLink(job);
    element("previous").disabled = currentIndex === 0;
    element("next").disabled = currentIndex === visibleJobs.length - 1;
  }

  function applyWorkflowResult(job, result) {
    job.workflow_status = result.workflow_status;
    job.is_new = false;
    if (result.application_tracked != null) {
      job.application_tracked = result.application_tracked;
    }
    // Several source cards can resolve to the same persisted job ID.
    const original = jobs.find(item => item.id === job.id);
    if (original) {
      original.workflow_status = result.workflow_status;
      original.application_tracked = job.application_tracked;
      original.is_new = false;
    }
    if (element("status-filter").value && element("status-filter").value !== result.workflow_status) {
      applyFilters(false);
      return;
    }
    if (currentIndex < visibleJobs.length - 1) currentIndex += 1;
    render();
  }

  async function changeStatus(workflowStatus) {
    const job = visibleJobs[currentIndex];
    await saveNote();
    const result = await postJson("/api/review-status", {
      job_id: job.id, workflow_status: workflowStatus
    }, "Status konnte nicht gespeichert werden");
    undoDecision = workflowStatus === "ignored"
      ? {jobId: job.id, expectedStatus: result.workflow_status} : null;
    applyWorkflowResult(job, result);
  }

  async function undoIgnored() {
    if (!undoDecision) return;
    await saveNote();
    const decision = undoDecision;
    const result = await postJson("/api/review-undo", {
      job_id: decision.jobId, expected_status: decision.expectedStatus
    }, "Entscheidung konnte nicht rückgängig gemacht werden");
    const job = jobs.find(item => item.id === decision.jobId);
    if (job) job.workflow_status = result.workflow_status;
    undoDecision = null;
    const known = [...element("status-filter").options].some(option => option.value === result.workflow_status);
    element("status-filter").value = known ? result.workflow_status : "";
    applyFilters();
    const restoredIndex = visibleJobs.findIndex(item => item.id === decision.jobId);
    if (restoredIndex >= 0) currentIndex = restoredIndex;
    render();
  }

  async function filePayload(kind, file) {
    if (!file) return null;
    if (file.size > 15 * 1024 * 1024) throw new Error("Eine Datei darf höchstens 15 MB groß sein");
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1] || ""));
      reader.addEventListener("error", () => reject(new Error("Datei konnte nicht gelesen werden")));
      reader.readAsDataURL(file);
    });
    return {kind, name: file.name, content};
  }

  async function selectedDocuments() {
    const documents = await Promise.all([
      filePayload("cover_letter", element("cover-letter-file").files[0]),
      filePayload("resume", element("resume-file").files[0])
    ]);
    return documents.filter(Boolean);
  }

  async function startApplication(documents) {
    const job = visibleJobs[currentIndex];
    const button = element("mark-applied");
    const salaryValue = element("salary-expectation").value;
    button.disabled = true;
    try {
      await saveNote();
      const result = await postJson("/api/applications", {
        job_id: job.id, documents,
        salary_expectation_eur: salaryValue ? Number(salaryValue) : null,
        salary_period: element("salary-period").value
      }, "Bewerbung konnte nicht gespeichert werden");
      applyWorkflowResult(job, result);
      element("application-dialog").close();
    } finally {
      button.disabled = false;
    }
  }

  async function load() {
    try {
      const response = await fetch("/api/recommendations");
      if (!response.ok) throw new Error("Empfehlungen konnten nicht geladen werden");
      const result = await response.json();
      routeOrigin = result.route_origin || "";
      jobs = result.recommendations.sort(byRank);
      addOptions(
        element("status-filter"),
        result.workflow_statuses.filter(status => reviewStatuses.has(status)),
        statusLabels
      );
      // The server names each role; the filter reuses those names.
      const roleLabels = Object.fromEntries(
        jobs.filter(job => job.role_label).map(job => [job.role_group, job.role_label]));
      addOptions(
        element("role-filter"),
        [...new Set(jobs.map(job => job.role_group).filter(Boolean))].sort(),
        roleLabels
      );
      const requestedJob = new URLSearchParams(window.location.search).get("job");
      if (requestedJob) await showRequestedJob(requestedJob);
      else applyFilters();
    } catch (error) {
      element("message").className = "error";
      element("message").textContent = error.message;
    }
  }

  async function showRequestedJob(jobId) {
    const find = () => jobs.find(job => job.id === jobId || job.recommendation_id === jobId);
    // A link can point to a listing that has gone offline since.
    if (!find()) await loadArchive();
    const requested = find();
    element("status-filter").value = "";
    element("role-filter").value = "";
    element("search-filter").value = "";
    if (requested?.international) element("international-filter").checked = true;
    if (String(requested?.location_precheck || "").startsWith("Junior-Hybrid")) {
      element("junior-hybrid-filter").checked = true;
    }
    applyFilters();
    const index = visibleJobs.findIndex(job => job === requested);
    if (index >= 0) {
      currentIndex = index;
      render();
    } else {
      JobFinder.showFeedback("Die angeforderte Stelle wurde nicht gefunden. Bitte die Anzeige erneut hinzufügen.");
    }
  }

  async function openApplicationLinker() {
    await saveNote();
    const job = visibleJobs[currentIndex];
    linkingJobId = job.id;
    const dialog = element("link-application-dialog");
    const select = element("link-application-select");
    const status = element("link-application-status");
    element("link-application-source").textContent = `${job.title} · ${job.company || "Arbeitgeber unbekannt"}`;
    select.replaceChildren();
    addOptions(select, [""], {"": "Bitte auswählen"}, "");
    element("link-application-save").disabled = true;
    status.textContent = "Bewerbungen werden geladen …";
    dialog.showModal();
    try {
      const response = await fetch("/api/applications");
      if (!response.ok) throw new Error("Bewerbungen konnten nicht geladen werden");
      const result = await response.json();
      if (!dialog.open || linkingJobId !== job.id) return;
      const applications = [...result.applications, ...result.completed_applications]
        .filter(application => application.id !== job.id);
      const labels = Object.fromEntries(applications.map(application => [application.id,
        `${application.company} · ${application.title} (${statusLabels[application.workflow_status] || application.workflow_status})`]));
      addOptions(select, applications.map(application => application.id), labels);
      status.textContent = applications.length ? "" : "Noch keine bestehende Bewerbung vorhanden.";
      element("link-application-save").disabled = !applications.length;
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function linkApplication(event) {
    event.preventDefault();
    const button = element("link-application-save");
    button.disabled = true;
    try {
      const result = await postJson("/api/application-listing", {
        job_id: linkingJobId, application_id: element("link-application-select").value
      }, "Anzeige konnte nicht zugeordnet werden");
      window.location.href = `/applications?job=${encodeURIComponent(result.job_id)}`;
    } catch (error) {
      element("link-application-status").textContent = error.message;
      button.disabled = false;
    }
  }

  for (const id of ["status-filter", "role-filter", "international-filter", "junior-hybrid-filter"]) {
    element(id).addEventListener("change", () => leaveCard(() => applyFilters()));
  }
  element("search-filter").addEventListener("input", () => leaveCard(() => applyFilters()));
  element("previous").addEventListener("click", () => leaveCard(() => { currentIndex -= 1; render(); }));
  element("next").addEventListener("click", () => leaveCard(() => { currentIndex += 1; render(); }));
  element("save-note").addEventListener("click", () => saveNote().catch(showError));
  element("review-note").addEventListener("input", () => { element("note-status").textContent = ""; });
  // Closing or reloading the page is the one way off a card that cannot wait for a save.
  window.addEventListener("beforeunload", event => {
    if (noteChanged()) event.preventDefault();
  });
  for (const status of ["interesting", "inquiry", "waiting", "ignored"]) {
    element(`mark-${status}`).addEventListener("click", () => changeStatus(status).catch(showError));
  }
  element("undo-ignored").addEventListener("click", () => undoIgnored().catch(showError));
  element("fact-sheet-rerun").addEventListener("click", () => requestRerun().catch(showError));
  element("mark-applied").addEventListener("click", () => element("application-dialog").showModal());
  element("link-application").addEventListener("click", () => openApplicationLinker().catch(showError));
  element("link-application-cancel").addEventListener("click", () => element("link-application-dialog").close());
  element("link-application-form").addEventListener("submit", linkApplication);
  element("application-cancel").addEventListener("click", () => element("application-dialog").close());
  element("application-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = element("application-save");
    button.disabled = true;
    try {
      await startApplication(await selectedDocuments());
      event.target.reset();
      element("salary-period").dispatchEvent(new Event("change"));
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
  JobFinder.bindSalaryInputs(element("salary-expectation"), element("salary-period"), element("salary-hint"));
  load();
