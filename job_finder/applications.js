  const statusLabels = {...JobFinder.statusLabels, interview: "Gespräch", offer: "Zusage"};
  const terminalStatuses = new Set(["rejected", "no_response", "offer"]);

  const {element, make, addOptions, appendSourceLinks, postJson, showError} = JobFinder;

  function formatDate(value) {
    if (!value) return "unbekannt";
    return new Date(`${value}T00:00:00`).toLocaleDateString("de-DE");
  }

  function formatDateTime(value) {
    if (!value) return "";
    return new Date(value).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short"
    });
  }

  function formatSalaryExpectation(value) {
    if (!Number.isInteger(value) || value <= 0) return "";
    return `${new Intl.NumberFormat("de-DE").format(value)} € Brutto/Jahr`;
  }

  function localIsoDate() {
    const now = new Date();
    const parts = [now.getFullYear(), now.getMonth() + 1, now.getDate()];
    return parts.map((part, index) => String(part).padStart(index ? 2 : 4, "0")).join("-");
  }

  function appendDocuments(parent, application) {
    const documents = application.documents || [];
    if (!documents.length) return;
    const container = make("div", null, "documents");
    container.append(make("strong", "Bewerbungsunterlagen:"));
    documents.forEach(document => {
      const link = make(
        "a",
        document.kind === "cover_letter" ? "Anschreiben" : "Lebenslauf"
      );
      const query = new URLSearchParams({
        job_id: application.id,
        document_id: document.id
      });
      link.href = `/api/application-document?${query}`;
      link.title = document.name;
      container.append(link);
    });
    parent.append(container);
  }

  function renderStats(stats) {
    const definitions = [
      ["total", "Bewerbungen"],
      ["open", "Offen"],
      ["completed", "Abgeschlossen"],
      ["responses", "Antworten"],
      ["interviews", "Gespräche"],
      ["offers", "Zusagen"],
      ["rejections", "Absagen"],
      ["no_responses", "Ohne Rückmeldung"],
      ["response_rate_percent", `Antwortquote · ${stats.completed} abgeschlossen`, "%"],
      [
        "average_response_days",
        `Ø Tage bis Antwort (${stats.response_time_samples} Fälle)`,
        ""
      ]
    ];
    const container = element("stats");
    container.replaceChildren();
    definitions.forEach(([key, label, suffix = ""]) => {
      const card = make("div", null, "stat");
      const value = stats[key] == null ? "–" : `${stats[key]}${suffix}`;
      card.append(make("span", value, "stat-value"), make("span", label, "stat-label"));
      container.append(card);
    });
  }

  function renderTimeline(application, parent) {
    const displayHistory = [...application.workflow_history];
    if (application.automatic_no_response) {
      displayHistory.push({status: "no_response", occurred_on: null});
    }
    const details = document.createElement("details");
    details.open = displayHistory.length === 0;
    details.append(make("summary", `Verlauf (${displayHistory.length})`));

    const readOnly = document.createElement("div");
    if (displayHistory.length) {
      const list = make("ol", null, "timeline");
      displayHistory.forEach(event => {
        const item = document.createElement("li");
        if (event.occurred_on && event.status !== "no_response") {
          item.append(`${formatDate(event.occurred_on)} · `);
        } else if (event.status !== "no_response") {
          item.append("Datum unbekannt · ");
        }
        const label = statusLabels[event.status] || event.status;
        item.append(
          terminalStatuses.has(event.status)
            ? make("strong", label)
            : document.createTextNode(label)
        );
        if (event.reason === "listing_unavailable") item.append(" · Anzeige nicht mehr verfügbar (automatisch)");
        if (event.scheduled_for) {
          item.append(` · Termin: ${formatDateTime(event.scheduled_for)}`);
        }
        list.append(item);
      });
      readOnly.append(list);
    } else {
      readOnly.append(make("p", "Für diese ältere Bewerbung ist noch kein Datum gespeichert.", "empty-history"));
    }

    const editor = make("div", null, "timeline-editor");
    editor.hidden = true;
    if (application.workflow_history.length) {
      const editList = make("ol", null, "timeline");
      application.workflow_history.forEach(event => {
        editList.append(historyEventForm(application.id, event));
      });
      editor.append(editList);
    }
    editor.append(make("p", "Neues Ereignis", "new-event-title"));
    editor.append(eventForm(application.id));

    const editButton = make("button", "Verlauf bearbeiten", "secondary timeline-edit-toggle");
    editButton.type = "button";
    editButton.addEventListener("click", () => {
      const editing = editor.hidden;
      editor.hidden = !editing;
      readOnly.hidden = editing;
      editButton.textContent = editing
        ? "Bearbeitung beenden"
        : "Verlauf bearbeiten";
    });

    details.append(readOnly, editButton, editor);
    parent.append(details);
  }

  function statusSelect(statuses, selectedStatus) {
    const select = document.createElement("select");
    addOptions(select, statuses, statusLabels, selectedStatus);
    return select;
  }

  function appointmentField(select, initialValue = "") {
    const label = make("label", "Gesprächstermin");
    const input = document.createElement("input");
    input.type = "datetime-local";
    input.value = initialValue || "";
    label.append(input);
    const updateVisibility = () => {
      label.hidden = select.value !== "interview";
    };
    select.addEventListener("change", updateVisibility);
    updateVisibility();
    return {label, input};
  }

  // Status, date and interview appointment shared by the new-event and edit forms.
  function eventFields(title, statuses, event = {}) {
    const statusLabel = make("label", title);
    const select = statusSelect(statuses, event.status);
    statusLabel.append(select);
    const dateLabel = make("label", "Status geändert am");
    const date = document.createElement("input");
    date.type = "date";
    date.value = event.occurred_on || "";
    dateLabel.append(date);
    const appointment = appointmentField(select, event.scheduled_for);
    const scheduledFor = () => select.value === "interview" ? appointment.input.value || null : null;
    return {select, date, scheduledFor, labels: [statusLabel, dateLabel, appointment.label]};
  }

  async function saveChange(buttons, path, payload, failureMessage) {
    buttons.forEach(button => { button.disabled = true; });
    try {
      await postJson(path, payload, failureMessage);
      await load();
    } catch (error) {
      showError(error);
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  }

  function eventForm(jobId) {
    const form = make("form", null, "event-form");
    const {select, date, scheduledFor, labels} = eventFields("Ereignis", window.applicationStatuses);
    select.name = "workflow_status";
    date.name = "occurred_on";
    date.required = true;
    date.value = localIsoDate();
    const button = make("button", "Speichern");
    button.type = "submit";
    form.append(...labels, button);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await saveChange([button], "/api/status",
        {job_id: jobId, workflow_status: select.value, occurred_on: date.value, scheduled_for: scheduledFor()},
        "Ereignis konnte nicht gespeichert werden");
    });
    return form;
  }

  function historyEventForm(jobId, historyEvent) {
    const item = make("li", null, "timeline-item");
    const form = make("form", null, "history-form");
    const {select, date, scheduledFor, labels} = eventFields("Status", window.workflowStatuses, historyEvent);
    const actions = make("div", null, "form-actions");
    const saveButton = make("button", "Ändern");
    saveButton.type = "submit";
    const deleteButton = make("button", "Löschen", "danger");
    deleteButton.type = "button";
    actions.append(saveButton, deleteButton);
    form.append(...labels, actions);

    const previousEvent = {
      job_id: jobId,
      event_index: historyEvent.event_index,
      previous_status: historyEvent.status,
      previous_occurred_on: historyEvent.occurred_on,
      previous_scheduled_for: historyEvent.scheduled_for || null
    };

    form.addEventListener("submit", async event => {
      event.preventDefault();
      await saveChange([saveButton, deleteButton], "/api/history",
        {...previousEvent, workflow_status: select.value, occurred_on: date.value || null, scheduled_for: scheduledFor()},
        "Verlaufsereignis konnte nicht geändert werden");
    });

    deleteButton.addEventListener("click", async () => {
      if (!window.confirm("Dieses Verlaufsereignis wirklich löschen?")) return;
      await saveChange([saveButton, deleteButton], "/api/history/delete",
        previousEvent,
        "Verlaufsereignis konnte nicht gelöscht werden");
    });

    item.append(form);
    return item;
  }

  function salaryEditor(application) {
    const details = make("details");
    details.append(make("summary", "Gehaltsvorstellung bearbeiten"));
    const form = make("form", null, "history-form");
    const label = make("label", "Gehaltsvorstellung (optional)");
    const input = make("input");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.value = application.salary_expectation_eur || "";
    label.append(input);
    const periodLabel = make("label", "Gehaltszeitraum");
    const period = make("select");
    for (const [value, text] of [["year", "€ Brutto / Jahr"], ["month", "€ Brutto / Monat"]]) {
      const option = make("option", text);
      option.value = value;
      period.append(option);
    }
    periodLabel.append(period);
    const hint = make("p", null, "field-hint");
    hint.setAttribute("aria-live", "polite");
    JobFinder.bindSalaryInputs(input, period, hint);
    const save = make("button", "Gehalt speichern");
    save.type = "submit";
    form.append(label, periodLabel, hint, save);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await saveChange([save], "/api/application-salary", {
        job_id: application.id,
        salary_expectation_eur: input.value ? Number(input.value) : null,
        salary_period: period.value
      }, "Gehaltsvorstellung konnte nicht gespeichert werden");
    });
    details.append(form);
    return details;
  }

  function renderApplications(applications, containerId) {
    const container = element(containerId);
    container.replaceChildren();
    applications.forEach(application => {
      const card = make("article", null, "application");
      const header = make("div", null, "application-header");
      const heading = document.createElement("div");
      heading.append(make("h2", application.title), make("p", application.company, "company"));
      const badges = make("div", null, "badges");
      badges.append(make("span", statusLabels[application.workflow_status] || application.workflow_status, "badge"));
      badges.append(make("span", application.active ? "Anzeige aktiv" : "Anzeige nicht mehr aktiv", `badge${application.active ? "" : " inactive"}`));
      header.append(heading, badges);
      card.append(header);

      const meta = make("div", null, "meta");
      meta.append(make("span", `Beworben: ${formatDate(application.applied_on)}`));
      if (application.response_on) meta.append(make("span", `Erste Antwort: ${formatDate(application.response_on)}`));
      if (application.days_to_response != null) meta.append(make("span", `${application.days_to_response} Tag(e) bis zur Antwort`));
      if (application.next_interview_at) meta.append(make("span", `Nächstes Gespräch: ${formatDateTime(application.next_interview_at)}`, "appointment"));
      appendSourceLinks(meta, application);
      card.append(meta);
      if (application.review_note) card.append(make("p", application.review_note, "note"));
      const salaryExpectation = formatSalaryExpectation(application.salary_expectation_eur);
      if (salaryExpectation) {
        const salary = make("p", null, "salary-note");
        salary.append(
          make("span", "Gehaltsvorstellung", "salary-note-label"),
          make("span", salaryExpectation)
        );
        card.append(salary);
      }
      card.append(salaryEditor(application));
      appendDocuments(card, application);
      renderTimeline(application, card);
      container.append(card);
    });
  }

  function renderArchive(completedApplications) {
    const archive = element("archive");
    const toggle = element("archive-toggle");
    renderApplications(completedApplications, "completed-applications");
    toggle.hidden = completedApplications.length === 0;
    if (!completedApplications.length) {
      archive.hidden = true;
      return;
    }
    toggle.textContent = archiveLabel(completedApplications.length);
  }

  function archiveLabel(count) {
    return element("archive").hidden ? `Abgeschlossene bearbeiten (${count})` : "Abgeschlossene ausblenden";
  }

  element("archive-toggle").addEventListener("click", () => {
    element("archive").hidden = !element("archive").hidden;
    element("archive-toggle").textContent = archiveLabel(element("completed-applications").children.length);
  });

  async function load() {
    try {
      const response = await fetch("/api/applications");
      if (!response.ok) throw new Error("Bewerbungen konnten nicht geladen werden");
      const result = await response.json();
      window.applicationStatuses = result.application_statuses;
      window.workflowStatuses = result.workflow_statuses;
      renderStats(result.statistics);
      renderApplications(result.applications, "applications");
      renderArchive(result.completed_applications);
      element("message").hidden = result.applications.length > 0;
      if (!result.applications.length) {
        element("message").textContent = result.statistics.total
          ? "Keine laufenden Bewerbungen."
          : "Noch keine Bewerbungen gespeichert.";
      }
    } catch (error) {
      element("message").hidden = false;
      element("message").className = "error";
      element("message").textContent = error.message;
    }
  }

  load();
