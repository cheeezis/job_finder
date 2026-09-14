  const form = document.getElementById("manual-import-form");
  const button = document.getElementById("manual-import-button");
  const status = document.getElementById("import-status");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    button.disabled = true;
    status.className = "";
    status.textContent = "Anzeige wird eingelesen und vorgefiltert …";
    try {
      const result = await JobFinder.postJson(
        "/api/manual-import", {url: document.getElementById("manual-url").value},
        "Die Stelle konnte nicht importiert werden"
      );
      window.location.href = `/review?job=${encodeURIComponent(result.job_id)}`;
    } catch (error) {
      status.className = "error";
      status.textContent = error.message;
      button.disabled = false;
    }
  });
