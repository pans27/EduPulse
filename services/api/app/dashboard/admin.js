const byId = (id) => document.getElementById(id);
const assignmentForm = byId("assign-role");
const coursePicker = byId("assignment-course");
const rolePicker = byId("assignment-role");
const assignButton = byId("assign-button");
const membersCourse = byId("members-course");
let assigning = false;
let courseRequest = 0;
let membersRequest = 0;

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
    const previousMembersCourse = membersCourse.value;
    membersCourse.replaceChildren(new Option(placeholder, ""));
    for (const course of courses) {
      coursePicker.add(new Option(`${course.name} (${course.id})`, course.id));
      membersCourse.add(new Option(course.name, course.id));
    }
    if (courses.some((course) => course.id === preferredCourse)) coursePicker.value = preferredCourse;
    membersCourse.value = courses.some((course) => course.id === previousMembersCourse) ? previousMembersCourse : coursePicker.value;
    membersCourse.disabled = !courses.length;
    void loadMembers();
  } catch (error) {
    if (request !== courseRequest) return;
    coursePicker.replaceChildren(new Option("Courses unavailable", ""));
    membersCourse.replaceChildren(new Option("Courses unavailable", ""));
    membersCourse.disabled = true;
    ++membersRequest;
    byId("course-members").replaceChildren();
    feedback("members-notice", error.message, true);
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
    if (fields.course_id && fields.course_id === membersCourse.value) void loadMembers();
  } catch (error) {
    feedback("assignment-notice", error.message, true);
  } finally {
    assigning = false;
    updateAssignmentForm();
  }
}

// Fetch memberships for the selected course and discard stale responses.
async function loadMembers(message = "") {
  const request = ++membersRequest;
  const list = byId("course-members");
  const courseId = membersCourse.value;
  list.replaceChildren();
  feedback("members-notice", courseId ? "Loading members…" : "");
  if (!courseId) return;
  const path = `/api/admin/courses/${encodeURIComponent(courseId)}/members`;
  try {
    const members = await api(path);
    if (request !== membersRequest) return;
    for (const member of members) list.append(memberRow(member, path, courseId));
    feedback("members-notice", message || (members.length ? "" : "No members assigned."));
  } catch (error) {
    if (request === membersRequest) feedback("members-notice", error.message, true);
  }
}

// Build role and removal controls for an existing membership.
function memberRow(member, path, courseId) {
  const row = document.createElement("li");
  const email = document.createElement("strong");
  email.textContent = member.email;
  const actions = document.createElement("div");
  actions.className = "resource-actions";
  const role = document.createElement("select");
  role.setAttribute("aria-label", `Course role for ${member.email}`);
  role.add(new Option("Student", "student"));
  role.add(new Option("Course staff", "staff"));
  role.value = member.role;
  const save = document.createElement("button");
  save.textContent = "Save role";
  save.disabled = true;
  role.addEventListener("change", () => { save.disabled = role.value === member.role; });
  const remove = document.createElement("button");
  remove.textContent = "Remove";
  remove.className = "danger";
  remove.setAttribute("aria-label", `Remove ${member.email} from the course`);
  const memberPath = `${path}/${member.user_id}`;
  save.addEventListener("click", () => updateMember("PATCH", { role: role.value }));
  remove.addEventListener("click", () => {
    if (window.confirm(`Remove ${member.email} from this course?`)) void updateMember("DELETE");
  });
  actions.append(role, save, remove);
  row.append(email, actions);
  return row;

  // Apply the change to the captured membership and refresh the current list.
  async function updateMember(method, data) {
    role.disabled = save.disabled = remove.disabled = true;
    try {
      await api(memberPath, data, method);
      if (courseId === membersCourse.value) {
        await loadMembers(method === "DELETE" ? "Member removed." : "Course role updated.");
      }
    } catch (error) {
      if (courseId === membersCourse.value) feedback("members-notice", error.message, true);
      role.disabled = remove.disabled = false;
      save.disabled = role.value === member.role;
    }
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
membersCourse.addEventListener("change", () => void loadMembers());
assignmentForm.addEventListener("submit", assignRole);
byId("create-course").addEventListener("submit", createCourse);
void loadCourses();
void loadMetrics();
