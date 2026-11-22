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
let accountSession;

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
  byId("register-panel").hidden = accountSession.courses[course.value] !== "staff";
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
    accountSession = await api("/api/auth/session");
    if (!accountSession.signed_in) {
      byId("sign-in-panel").hidden = false;
      return;
    }
    byId("sign-out").hidden = false;
    byId("dashboard").hidden = false;
    byId("admin-link").hidden = !accountSession.is_admin;
    const courses = await api("/api/courses");
    for (const entry of courses) course.add(new Option(entry.name, entry.id));
    if (!courses.length) {
      byId("connection").textContent = "No courses available";
      byId("empty").textContent = "An administrator can assign you to a course.";
      return;
    }
    course.disabled = byId("refresh").disabled = byId("register-fields").disabled = false;
    selectCourse();
  } catch (error) {
    byId("dashboard").hidden = false;
    byId("connection").textContent = "Dashboard unavailable";
    byId("empty").textContent = "Resources unavailable.";
    notice(error.message, true);
  }
}

// Submit credentials from either authentication form.
async function submitCredentials(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const credentials = Object.fromEntries(new FormData(form));
  const button = form.querySelector("button");
  const message = byId(`${form.id}-error`);
  button.disabled = true;
  message.hidden = true;
  try {
    if (form.id === "sign-up") await api("/api/auth/register", credentials);
    await api("/api/auth/login", credentials);
    location.reload();
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    button.disabled = false;
  }
}

byId("sign-in").addEventListener("submit", submitCredentials);
byId("sign-up").addEventListener("submit", submitCredentials);
byId("show-register").addEventListener("click", () => {
  byId("sign-in-panel").hidden = true;
  byId("sign-up-panel").hidden = false;
  byId("sign-up").elements.email.focus();
});
byId("show-login").addEventListener("click", () => {
  byId("sign-up-panel").hidden = true;
  byId("sign-in-panel").hidden = false;
  byId("sign-in").elements.email.focus();
});

byId("sign-out").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", {});
    location.reload();
  } catch (error) {
    notice(error.message, true);
  }
});
course.addEventListener("change", selectCourse);
byId("refresh").addEventListener("click", () => {
  notice("");
  void refreshResources();
});
register.addEventListener("submit", registerResource);
report.addEventListener("submit", submitReport);
byId("cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (event) => {
  if (byId("cancel").disabled) event.preventDefault();
});
void start();
