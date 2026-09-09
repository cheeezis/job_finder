/* Shared browser helpers. Job data is rendered as text, never as HTML. */
const JobFinder = (() => {
  const element = id => document.getElementById(id);
  const statusLabels = {
    new: "Neu", review: "Prüfen", interesting: "Interessant", inquiry: "Rückfrage offen",
    ignored: "Nicht interessant", applied: "Beworben", response: "Antwort erhalten",
    interview: "Interview", rejected: "Absage", no_response: "Keine Rückmeldung",
    offer: "Angebot", closed: "Abgeschlossen"
  };
  const sourceLabels = {
    stepstone: "StepStone", get_in_it: "get-in-IT", studysmarter: "StudySmarter",
    arbeitnow: "Arbeitnow", arbeitsagentur: "Arbeitsagentur", remotely: "Remotely"
  };

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function appendSourceLinks(parent, job, labels = sourceLabels, asButtons = false) {
    const candidates = (job.source_links || []).length
      ? job.source_links : [{source: "listing", url: job.url}];
    const seen = new Set();
    const links = candidates.flatMap((item, index) => {
      const url = safeUrl(item.url);
      if (!url || seen.has(url)) return [];
      seen.add(url);
      return [{url, label: labels[item.source] || `Anzeige ${index + 1}`}];
    });
    if (!links.length) return;
    const linkFor = (item, text, className) => {
      const link = make("a", text, className);
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return link;
    };
    if (links.length === 1) {
      parent.append(linkFor(links[0], "Anzeige öffnen", asButtons ? "button" : ""));
      return;
    }
    const menu = make("details", null, "source-menu");
    const summary = make("summary", `Anzeigen öffnen (${links.length})`, asButtons ? "button" : "");
    const options = make("div", null, "source-options");
    links.forEach(item => options.append(linkFor(item, item.label)));
    menu.append(summary, options);
    parent.append(menu);
  }

  async function postJson(path, payload, fallbackMessage) {
    const response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || fallbackMessage);
    return result;
  }

  function showFeedback(text) {
    const message = element("action-message");
    message.textContent = text;
    message.hidden = false;
  }

  const showError = error => showFeedback(error.message);
  return {element, make, safeUrl, appendSourceLinks, postJson, showError, showFeedback,
    statusLabels, sourceLabels};
})();
