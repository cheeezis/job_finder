// Minimal DOM and browser services for running complete page scripts in Node.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function node(tagName) {
  return {
    tagName, children: [], value: "", textContent: "", checked: false, hidden: false,
    files: [], listeners: {},
    get options() { return this.children; },
    append(...children) {
      this.children.push(...children);
      if (this.tagName === "select") {
        for (const child of children) {
          if (child.selected || this.children[0] === child) this.value = child.value;
        }
      }
    },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    async emit(name, event = {}) {
      for (const callback of this.listeners[name] || []) {
        await callback({preventDefault() {}, target: this, ...event});
      }
    },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    showModal() { this.open = true; },
    close() { this.open = false; },
    reset() {},
  };
}

function page(name, helpers = {}, fetch = () => new Promise(() => {})) {
  const directory = path.join(__dirname, "../job_finder");
  const html = fs.readFileSync(path.join(directory, `${name}.html`), "utf8");
  const elements = new Map();
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = node(match[1]);
    element.hidden = /\bhidden\b/.test(match[2]);
    element.value = /\bvalue="([^"]*)"/.exec(match[2])?.[1] || "";
    if (match[1] === "select") {
      const content = html.slice(match.index + match[0].length).split("</select>")[0];
      for (const option of content.matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)) {
        const item = node("option");
        item.value = /value="([^"]*)"/.exec(option[1])?.[1] ?? option[2];
        item.selected = /\bselected\b/.test(option[1]);
        element.append(item);
      }
    }
    elements.set(match[3], element);
  }
  const context = vm.createContext({
    URL, URLSearchParams, fetch, injectedHelpers: helpers,
    window: {location: {search: ""}, confirm: () => true},
    document: {
      createElement: node, createTextNode: text => ({textContent: text}),
      getElementById(id) {
        if (!elements.has(id)) throw new Error(`Unknown element: ${id}`);
        return elements.get(id);
      }
    }
  });
  for (const [, script] of html.matchAll(/<script src="\/([^"]+)"><\/script>/g)) {
    vm.runInContext(fs.readFileSync(path.join(directory, script), "utf8"), context, {filename: script});
    if (script === "app.js") vm.runInContext("Object.assign(JobFinder, injectedHelpers)", context);
  }
  return {context, elements, run: code => vm.runInContext(code, context)};
}

module.exports = {node, page};
