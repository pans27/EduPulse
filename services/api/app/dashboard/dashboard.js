const byId = (id) => document.getElementById(id);
const course = byId("course");
const register = byId("register");
const report = byId("report");
const dialog = byId("report-dialog");
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
let socket;
let retry;
let selection = 0;
let requestVersion = 0;
let reportPath;

// Send a same-origin API request and expose any validation message.
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail.map((issue) => issue.msg).join(" ")
      : result.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return result;
}

// Show feedback after a dashboard action.
function notice(message, error = false) {
  const element = byId("notice");
  element.textContent = message;
  element.hidden = !message;
  element.toggleAttribute("data-error", error);
}

// Build the resource list using text nodes for user-provided fields.
function renderResources(resources) {
  const list = byId("resources");
  byId("empty").hidden = resources.length > 0;
  byId("empty").textContent = "No resources registered for this course.";
  list.replaceChildren();

  for (const resource of resources) {
    const row = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "resource-heading";
    const name = document.createElement("a");
    name.href = resource.url;
    name.textContent = resource.name;
    const state = document.createElement("span");
    state.className = "resource-state";
    state.textContent = resource.state?.replaceAll("_", " ") ?? "Awaiting first check";
    heading.append(name, state);

    const details = document.createElement("p");
    details.className = "resource-details muted";
    const nextCheck = dateFormat.format(new Date(resource.next_check_at));
    details.textContent = `Next check ${nextCheck} · Every ${resource.check_interval_seconds}s`;
    if (resource.release_at) {
      details.textContent += ` · Release ${dateFormat.format(new Date(resource.release_at))}`;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Report issue";
    button.setAttribute("aria-label", `Report an issue with ${resource.name}`);
    button.addEventListener("click", () => openReport(resource));
    row.append(heading, details, button);
    list.append(row);
  }
}

// Load the selected course without overwriting newer snapshots or selections.
async function refreshResources() {
  const version = ++requestVersion;
  try {
    const resources = await api(`/api/courses/${encodeURIComponent(course.value)}/resources`);
    if (version === requestVersion) renderResources(resources);
  } catch (error) {
    if (version === requestVersion) {
      notice(error.message, true);
      if (!byId("resources").children.length) {
        byId("empty").textContent = "Resources unavailable.";
      }
    }
  }
}

// Load the preview totals across courses.
async function refreshMetrics() {
  try {
    const metrics = await api("/api/metrics");
    byId("metrics").textContent =
      `Across all courses · ${metrics.registered_resources} resources · ${metrics.student_reports} reports`;
  } catch {
    byId("metrics").textContent = "Metrics unavailable.";
  }
}

// Subscribe to the selected course and reconnect after a dropped connection.
function connect(id, currentSelection) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const connection = new WebSocket(`${protocol}//${location.host}/api/courses/${encodeURIComponent(id)}/ws`);
  socket = connection;
  connection.onopen = () => {
    if (selection === currentSelection) byId("connection").textContent = "Live updates connected";
  };
  connection.onmessage = ({ data }) => {
    const snapshot = JSON.parse(data);
    if (selection === currentSelection && snapshot.type === "course.snapshot" && snapshot.course.id === id) {
      requestVersion++;
      renderResources(snapshot.resources);
      void refreshMetrics();
    }
  };
  connection.onclose = () => {
    if (selection !== currentSelection) return;
    byId("connection").textContent = "Live updates disconnected. Reconnecting…";
    retry = setTimeout(() => connect(id, currentSelection), 3000);
  };
}

// Replace the previous course subscription and load the new selection.
function selectCourse() {
  const currentSelection = ++selection;
  clearTimeout(retry);
  socket?.close();
  notice("");
  byId("resources").replaceChildren();
  byId("empty").hidden = false;
  byId("empty").textContent = "Loading resources…";
  byId("connection").textContent = "Connecting…";
  void refreshResources();
  connect(course.value, currentSelection);
}

// Open the report form for the resource selected in the list.
function openReport(resource) {
  reportPath = `/api/courses/${encodeURIComponent(resource.course_id)}/resources/${encodeURIComponent(resource.id)}/reports`;
  report.reset();
  byId("report-error").hidden = true;
  byId("report-resource").textContent = resource.name;
  dialog.showModal();
}

// Register the resource with an explicit timezone for the optional release.
async function registerResource(event) {
  event.preventDefault();
  const selectedCourse = course.value;
  const fields = new FormData(register);
  const release = fields.get("release");
  const fieldset = byId("register-fields");
  fieldset.disabled = true;
  try {
    await api(`/api/courses/${encodeURIComponent(selectedCourse)}/resources`, {
      name: fields.get("name"),
      url: fields.get("url"),
      check_interval_seconds: Number(fields.get("interval")),
      release_at: release ? new Date(release).toISOString() : null,
    });
    register.reset();
    if (selectedCourse === course.value) void refreshResources();
    void refreshMetrics();
    notice("Resource registered.");
  } catch (error) {
    notice(error.message, true);
  } finally {
    fieldset.disabled = false;
  }
}

// Submit a student report and refresh the resource schedule and totals.
async function submitReport(event) {
  event.preventDefault();
  const button = report.querySelector("[type=submit]");
  const cancel = byId("cancel");
  const errorMessage = byId("report-error");
  button.disabled = cancel.disabled = true;
  errorMessage.hidden = true;
  try {
    await api(reportPath, { description: new FormData(report).get("description") });
    dialog.close();
    notice("Report submitted. Recheck requested.");
    void refreshResources();
    void refreshMetrics();
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
  } finally {
    button.disabled = cancel.disabled = false;
  }
}

// Populate the course picker and enable the dashboard controls.
async function start() {
  try {
    const courses = await api("/api/courses");
    for (const entry of courses) course.add(new Option(entry.name, entry.id));
    if (!courses.length) {
      byId("connection").textContent = "No courses available";
      byId("empty").textContent = "Resources appear when a course is available.";
      return;
    }
    course.disabled = byId("refresh").disabled = byId("register-fields").disabled = false;
    selectCourse();
    void refreshMetrics();
  } catch (error) {
    byId("connection").textContent = "Dashboard unavailable";
    byId("empty").textContent = "Resources unavailable.";
    notice(error.message, true);
  }
}

course.addEventListener("change", selectCourse);
byId("refresh").addEventListener("click", () => {
  notice("");
  void refreshResources();
  void refreshMetrics();
});
register.addEventListener("submit", registerResource);
report.addEventListener("submit", submitReport);
byId("cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (event) => {
  if (byId("cancel").disabled) event.preventDefault();
});
void start();
