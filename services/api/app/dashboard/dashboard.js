const byId = (id) => document.getElementById(id);
const course = byId("course");
const emailOptIn = byId("email-opt-in");
const register = byId("register");
const report = byId("report");
const dialog = byId("report-dialog");
const editForm = byId("edit-resource");
const editDialog = byId("edit-dialog");
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
let socket;
let retry;
let selection = 0;
let requestVersion = 0;
let reportPath;
let accountSession;
let editingResource;

// Build a course-scoped path for operations on one resource.
function resourcePath(resource) {
  return `/api/courses/${encodeURIComponent(resource.course_id)}/resources/${encodeURIComponent(resource.id)}`;
}

// Create a row action with a descriptive accessible name.
function resourceAction(label, resource, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.setAttribute("aria-label", `${label} for ${resource.name}`);
  button.addEventListener("click", () => handler(resource, button));
  return button;
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
    details.textContent = `Every ${resource.check_interval_seconds}s`;
    if (resource.release_at) {
      details.textContent += ` · Release ${dateFormat.format(new Date(resource.release_at))}`;
    }

    const actions = document.createElement("div");
    actions.className = "resource-actions";
    actions.append(resourceAction("Report issue", resource, openReport));
    if (accountSession.courses[resource.course_id] === "staff") {
      const remove = resourceAction("Delete", resource, deleteResource);
      remove.className = "danger";
      actions.append(resourceAction("Edit", resource, openEdit), remove);
    }
    row.append(heading, details, actions);
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
  void loadEmailPreference(course.value, currentSelection);
  void refreshResources();
  connect(course.value, currentSelection);
}

// Show the selected course's saved preference for students.
async function loadEmailPreference(id, currentSelection) {
  const isStudent = accountSession.courses[id] === "student";
  byId("course-email").hidden = !isStudent;
  emailOptIn.disabled = true;
  emailOptIn.checked = false;
  const status = byId("email-status");
  status.textContent = "";
  if (!isStudent) return;
  try {
    const preference = await api(`/api/courses/${encodeURIComponent(id)}/email-preference`);
    if (selection !== currentSelection) return;
    emailOptIn.checked = preference.email_opt_in;
    emailOptIn.disabled = false;
  } catch (error) {
    if (selection === currentSelection) status.textContent = error.message;
  }
}

// Save the preference for the course selected when the checkbox changed.
async function saveEmailPreference() {
  const currentSelection = selection;
  const status = byId("email-status");
  const optedIn = emailOptIn.checked;
  emailOptIn.disabled = true;
  status.textContent = "Saving…";
  try {
    await api(`/api/courses/${encodeURIComponent(course.value)}/email-preference`, {
      email_opt_in: optedIn,
    }, "PUT");
    if (selection === currentSelection) status.textContent = "Email preference saved.";
  } catch (error) {
    if (selection === currentSelection) {
      emailOptIn.checked = !optedIn;
      status.textContent = error.message;
    }
  } finally {
    if (selection === currentSelection) emailOptIn.disabled = false;
  }
}

// Open the report form for the resource selected in the list.
function openReport(resource) {
  reportPath = `${resourcePath(resource)}/reports`;
  report.reset();
  byId("report-error").hidden = true;
  byId("report-resource").textContent = resource.name;
  dialog.showModal();
}

// Prefill the editor and keep the displayed values for a field-by-field PATCH.
function openEdit(resource) {
  const release = resource.release_at ? new Date(resource.release_at) : null;
  const localRelease = release
    ? new Date(release.getTime() - release.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
    : "";
  const initial = {
    name: resource.name, url: resource.url,
    interval: String(resource.check_interval_seconds), release: localRelease,
  };
  for (const [name, value] of Object.entries(initial)) editForm.elements.namedItem(name).value = value;
  editForm.elements.namedItem("api_token").value = "";
  editForm.elements.namedItem("remove_api_token").checked = false;
  editingResource = { resource, initial };
  byId("edit-error").hidden = true;
  editDialog.showModal();
}

// Save only changed fields so an edit cannot overwrite unrelated changes.
async function editResource(event) {
  event.preventDefault();
  const { resource, initial } = editingResource;
  const fields = Object.fromEntries(new FormData(editForm));
  const controls = byId("edit-fields");
  const errorMessage = byId("edit-error");
  controls.disabled = true;
  errorMessage.hidden = true;
  try {
    const changes = {};
    if (fields.api_token && fields.remove_api_token) {
      throw new Error("Enter a replacement token or select removal.");
    }
    if (fields.api_token) changes.api_token = fields.api_token;
    if (fields.remove_api_token) changes.api_token = null;
    if (fields.name !== initial.name) changes.name = fields.name;
    if (fields.url !== initial.url) changes.url = fields.url;
    if (fields.interval !== initial.interval) changes.check_interval_seconds = Number(fields.interval);
    if (fields.release !== initial.release) {
      changes.release_at = fields.release ? new Date(fields.release).toISOString() : null;
    }
    if (Object.keys(changes).length) {
      await api(resourcePath(resource), changes, "PATCH");
      notice("Resource updated.");
      if (resource.course_id === course.value) void refreshResources();
    }
    editDialog.close();
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
  } finally {
    controls.disabled = false;
  }
}

// Confirm a permanent deletion before sending it to the API.
async function deleteResource(resource, button) {
  if (!window.confirm(`Delete "${resource.name}" and its check history, incidents, reports, and queued alerts? This cannot be undone.`)) return;
  button.disabled = true;
  try {
    await api(resourcePath(resource), undefined, "DELETE");
    notice("Resource deleted.");
    if (resource.course_id === course.value) void refreshResources();
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
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
      ...(fields.get("api_token") ? { api_token: fields.get("api_token") } : {}),
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
emailOptIn.addEventListener("change", saveEmailPreference);
byId("refresh").addEventListener("click", () => {
  notice("");
  void refreshResources();
});
register.addEventListener("submit", registerResource);
editForm.addEventListener("submit", editResource);
byId("cancel-edit").addEventListener("click", () => editDialog.close());
editDialog.addEventListener("close", () => {
  editForm.elements.namedItem("api_token").value = "";
});
editDialog.addEventListener("cancel", (event) => {
  if (byId("edit-fields").disabled) event.preventDefault();
});
report.addEventListener("submit", submitReport);
byId("cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (event) => {
  if (byId("cancel").disabled) event.preventDefault();
});
void start();
