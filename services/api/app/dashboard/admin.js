const byId = (id) => document.getElementById(id);
const assignmentForm = byId("assign-role");
const coursePicker = byId("assignment-course");
const rolePicker = byId("assignment-role");
const assignButton = byId("assign-button");
let assigning = false;
let courseRequest = 0;

// Keep course selection required only for staff and student assignments.
function updateAssignmentForm() {
  const isAdmin = rolePicker.value === "admin";
  byId("assignment-title").textContent = isAdmin ? "Grant administrator access" : "Assign user to a course";
  byId("assignment-course-label").hidden = isAdmin;
  coursePicker.disabled = isAdmin;
  assignButton.textContent = isAdmin ? "Grant admin access" : "Assign to course";
  assignButton.disabled = assigning || (!isAdmin && !coursePicker.value);
}

// Load every course available for administration, including unassigned courses.
async function loadCourses(preferredCourse = coursePicker.value) {
  const request = ++courseRequest;
  try {
    const courses = await api("/api/admin/courses");
    if (request !== courseRequest) return;
    const placeholder = courses.length ? "Select a course" : "Create a course first";
    coursePicker.replaceChildren(new Option(placeholder, ""));
    for (const course of courses) {
      coursePicker.add(new Option(`${course.name} (${course.id})`, course.id));
    }
    if (courses.some((course) => course.id === preferredCourse)) coursePicker.value = preferredCourse;
  } catch (error) {
    if (request !== courseRequest) return;
    coursePicker.replaceChildren(new Option("Courses unavailable", ""));
    feedback("assignment-notice", error.message, true);
  }
  byId("assignment-fields").disabled = false;
  updateAssignmentForm();
}

// Display feedback beneath the form that initiated the action.
function feedback(id, message, error = false) {
  const element = byId(id);
  element.textContent = message;
  element.hidden = !message;
  element.toggleAttribute("data-error", error);
}

// Create a course and make it immediately selectable for user assignments.
async function createCourse(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  feedback("course-notice", "");
  try {
    const course = await api("/api/admin/courses", Object.fromEntries(new FormData(form)));
    form.reset();
    feedback("course-notice", "Course created.");
    await loadCourses(course.id);
  } catch (error) {
    feedback("course-notice", error.message, true);
  } finally {
    button.disabled = false;
  }
}

// Assign an existing account by email and selected course or grant global admin access.
async function assignRole(event) {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(assignmentForm));
  assigning = true;
  updateAssignmentForm();
  feedback("assignment-notice", "");
  try {
    await api("/api/admin/users/role", fields, "PUT");
    feedback("assignment-notice", fields.role === "admin" ? "Administrator access granted." : "User assigned to course.");
  } catch (error) {
    feedback("assignment-notice", error.message, true);
  } finally {
    assigning = false;
    updateAssignmentForm();
  }
}

// Show global totals in Administration.
async function loadMetrics() {
  const element = byId("metrics");
  try {
    const counts = await api("/api/metrics");
    element.textContent = `Across all courses · ${counts.registered_resources} resources · ${counts.student_reports} reports`;
  } catch {
    element.textContent = "Metrics unavailable.";
  }
  element.hidden = false;
}

rolePicker.addEventListener("change", updateAssignmentForm);
coursePicker.addEventListener("change", updateAssignmentForm);
assignmentForm.addEventListener("submit", assignRole);
byId("create-course").addEventListener("submit", createCourse);
void loadCourses();
void loadMetrics();
