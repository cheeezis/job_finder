// @ts-check
  const form = document.getElementById("manual-import-form");
  const button = /** @type {HTMLButtonElement} */ (document.getElementById("manual-import-button"));
  // Not "status": that name is already window.status.
  const importStatus = document.getElementById("import-status");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    button.disabled = true;
    importStatus.className = "";
    importStatus.textContent = "Anzeige wird eingelesen und vorgefiltert …";
    try {
      const result = await JobFinder.postJson(
        "/api/manual-import", {url: /** @type {HTMLInputElement} */ (document.getElementById("manual-url")).value},
        "Die Stelle konnte nicht importiert werden"
      );
      window.location.href = `/review?job=${encodeURIComponent(result.job_id)}`;
    } catch (error) {
      importStatus.className = "error";
      importStatus.textContent = error.message;
      button.disabled = false;
    }
  });

  // The newest run of each runner: where, when, how it ended and what it found.
  const runnerLabels = {cloud: "Cloud", hybrid: "Hybrid (StepStone/Remotely)", local: "Lokal"};

  function runTime(value) {
    const moment = new Date(value);
    const zone = {timeZone: "Europe/Berlin"};
    const day = new Intl.DateTimeFormat("de-DE", {...zone, dateStyle: "short"});
    const clock = new Intl.DateTimeFormat("de-DE", {...zone, hour: "2-digit", minute: "2-digit"}).format(moment);
    const today = day.format(new Date());
    const yesterday = day.format(new Date(Date.now() - 86400000));
    const date = day.format(moment);
    return `${date === today ? "heute" : date === yesterday ? "gestern" : date} ${clock}`;
  }

  function runText(run) {
    if (run.outcome === "running") return `läuft seit ${runTime(run.started_at)}`;
    if (run.outcome === "failed") return `${runTime(run.started_at)} · abgebrochen`;
    const parts = [`${run.jobs_new ?? 0} neue Stellen`, `${run.review_new ?? 0} neu in der Review`];
    if (run.sources_failed) parts.push(`${run.sources_failed} Quelle(n) fehlgeschlagen`);
    return `${runTime(run.finished_at || run.started_at)} · ${parts.join(" · ")}`;
  }

  async function showRuns() {
    const list = document.getElementById("recent-runs");
    try {
      const response = await fetch("/api/runs");
      if (!response.ok) throw new Error();
      const {runs} = /** @type {RunsResponse} */ (await response.json());
      list.replaceChildren(...runs.map(run => {
        const item = document.createElement("li");
        const label = document.createElement("strong");
        label.textContent = `${runnerLabels[run.runner] || run.runner}: `;
        item.append(label, runText(run));
        return item;
      }));
      if (!runs.length) list.replaceChildren(Object.assign(document.createElement("li"), {textContent: "Noch kein Lauf verzeichnet."}));
    } catch {
      list.replaceChildren(Object.assign(document.createElement("li"), {textContent: "Läufe konnten nicht geladen werden."}));
    }
  }

  showRuns();

