  const roleLabels = {
    general_it: "Allgemeine IT", software_development: "Softwareentwicklung",
    python_ai_data: "Python / KI / Daten", technical_consulting: "Technisches Consulting",
    infrastructure: "Infrastruktur", junior_sap: "SAP-Einstieg",
    testing: "Testing / QA", junior_administration: "IT-Administration",
    rpa_automation: "RPA / Automatisierung", trainee: "Trainee",
    junior_modern_workplace: "Modern Workplace",
    infrastructure_automation: "Infrastruktur-Automatisierung",
    manual_review: "Manuell hinzugefügt"
  };
  const statusLabels = JobFinder.statusLabels;
  const sourceLabels = {
    ...JobFinder.sourceLabels, original: "Originalanzeige",
    german_tech_jobs: "GermanTechJobs", manual: "Manuell hinzugefügt"
  };
  const reviewStatuses = new Set(["review", "interesting", "inquiry", "ignored"]);
  let jobs = [];
  let visibleJobs = [];
  let currentIndex = 0;
  let routeOrigin = "";
  let undoDecision = null;

  const {element, addOptions, appendSourceLinks, postJson, showError} = JobFinder;

  function setText(id, value) {
    element(id).textContent = value || "";
  }

  function routeDestination(job) {
    const ignored = new Set([
      "deutschland", "germany", "bundesweit", "deutschlandweit",
      "remote", "hybrid"
    ]);
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
      api: "1",
      origin: `${routeOrigin}, Deutschland`,
      destination: `${destination}, Deutschland`
    });
    link.href = `https://www.google.com/maps/dir/?${parameters}`;
  }

  function displayDate(value) {
    if (!value) return "nicht angegeben";
    const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    return Number.isNaN(date.getTime())
      ? String(value)
      : date.toLocaleDateString("de-DE");
  }

  function applyFilters(resetPosition = true) {
    const status = element("status-filter").value;
    const role = element("role-filter").value;
    const query = element("search-filter").value.trim().toLocaleLowerCase("de");
    const showInternational = element("international-filter").checked;
    const showJuniorHybrid = element("junior-hybrid-filter").checked;
    visibleJobs = jobs.filter(job => {
      const statusMatches = !status || job.workflow_status === status;
      return statusMatches &&
      (!role || job.role_group === role) &&
      (showInternational || !job.international) &&
      (showJuniorHybrid || !String(job.location_precheck || "").startsWith("Junior-Hybrid")) &&
      (!query || `${job.title} ${job.company}`.toLocaleLowerCase("de").includes(query));
    });
    currentIndex = resetPosition
      ? 0
      : Math.min(currentIndex, Math.max(visibleJobs.length - 1, 0));
    render();
  }

  function render() {
    element("undo-ignored").hidden = !undoDecision;
    const hasJobs = visibleJobs.length > 0;
    element("card").hidden = !hasJobs;
    element("navigation").hidden = !hasJobs;
    element("message").hidden = hasJobs;
    if (!hasJobs) {
      element("message").textContent = jobs.length
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
      : roleLabels[job.role_group] || "Allgemeine IT");
    const newBadge = element("new-badge");
    newBadge.hidden = job.workflow_status !== "new";
    newBadge.className = "badge";
    newBadge.textContent = "Neu";
    setText("title", job.title);
    setText("company", job.company);
    setText("location", `Ort: ${(job.locations || []).join(", ") || "unbekannt"}`);
    const remote = job.remote_percentage != null
      ? `${job.remote_percentage}%`
      : job.work_mode === "hybrid" ? "Homeoffice" : "nicht angegeben";
    setText("remote", `Remote: ${remote}`);
    setText("published", `Veröffentlicht: ${displayDate(job.published_at)}`);
    const freshness = element("freshness");
    freshness.hidden = !job.cache_stale;
    freshness.textContent = job.cache_stale
      ? `Cache-Fallback · Stand: ${displayDate(job.fetched_at)}`
      : "";
    setText("current-status", `Status: ${statusLabels[job.workflow_status] || job.workflow_status}`);
    setText("role-group", job.current_snapshot_missing
      ? "aktuell nicht verfügbar"
      : roleLabels[job.role_group] || "Allgemeine IT");
    setText("experience-level", job.current_snapshot_missing
      ? "aktuell nicht verfügbar"
      : job.experience_level || "keine Jahresanforderung erkannt");
    setText("location-precheck", job.current_snapshot_missing
      ? "Quelle hat die Stelle im aktuellen Lauf nicht geliefert"
      : job.location_precheck || "passt zur Standortregel");
    const applicationTracked = Boolean(job.application_tracked);
    for (const id of ["mark-interesting", "mark-inquiry", "mark-ignored", "mark-applied"]) {
      element(id).hidden = applicationTracked;
    }
    element("application-link").hidden = !applicationTracked;
    const warning = element("prefilter-warning");
    warning.hidden = !job.prefilter_warning;
    warning.textContent = job.prefilter_warning
      ? `Hinweis aus dem Vorfilter: ${job.prefilter_warning}`
      : "";
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
    const result = await postJson("/api/review-status", {
      job_id: job.id, workflow_status: workflowStatus
    }, "Status konnte nicht gespeichert werden");
    undoDecision = workflowStatus === "ignored"
      ? {jobId: job.id, expectedStatus: result.workflow_status} : null;
    applyWorkflowResult(job, result);
  }

  async function undoIgnored() {
    if (!undoDecision) return;
    const decision = undoDecision;
    const result = await postJson("/api/review-undo", {
      job_id: decision.jobId, expected_status: decision.expectedStatus
    }, "Entscheidung konnte nicht rückgängig gemacht werden");
    const job = jobs.find(item => item.id === decision.jobId);
    if (job) job.workflow_status = result.workflow_status;
    undoDecision = null;
    if ([...element("status-filter").options].some(option => option.value === result.workflow_status)) {
      element("status-filter").value = result.workflow_status;
    } else {
      element("status-filter").value = "";
    }
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
      jobs = result.recommendations.sort((a, b) => {
        const score = (b.match_percent ?? -1) - (a.match_percent ?? -1);
        if (score) return score;
        const published = String(b.published_at || "").localeCompare(String(a.published_at || ""));
        if (published) return published;
        const firstSeen = String(b.first_seen_at || "").localeCompare(String(a.first_seen_at || ""));
        return firstSeen;
      });
      addOptions(
        element("status-filter"),
        result.workflow_statuses.filter(status => reviewStatuses.has(status)),
        statusLabels
      );
      addOptions(
        element("role-filter"),
        [...new Set(jobs.map(job => job.role_group).filter(Boolean))].sort(),
        roleLabels
      );
      const requestedJob = new URLSearchParams(window.location.search).get("job");
      if (requestedJob) element("status-filter").value = "";
      applyFilters();
      if (requestedJob) {
        const requestedIndex = visibleJobs.findIndex(job => job.id === requestedJob);
        if (requestedIndex >= 0) {
          currentIndex = requestedIndex;
          render();
        }
      }
    } catch (error) {
      element("message").className = "error";
      element("message").textContent = error.message;
    }
  }

  for (const id of ["status-filter", "role-filter", "international-filter", "junior-hybrid-filter"]) {
    element(id).addEventListener("change", () => applyFilters());
  }
  element("search-filter").addEventListener("input", () => applyFilters());
  element("previous").addEventListener("click", () => { currentIndex -= 1; render(); });
  element("next").addEventListener("click", () => { currentIndex += 1; render(); });
  for (const status of ["interesting", "inquiry", "ignored"]) {
    element(`mark-${status}`).addEventListener("click", () => changeStatus(status).catch(showError));
  }
  element("undo-ignored").addEventListener("click", () =>
    undoIgnored().catch(showError)
  );
  element("mark-applied").addEventListener("click", () =>
    element("application-dialog").showModal()
  );
  element("application-cancel").addEventListener("click", () =>
    element("application-dialog").close()
  );
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
