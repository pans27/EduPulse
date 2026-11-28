// Send a same-origin API request and expose any validation message.
// GET without data, POST with data, or any method given explicitly (DELETE needs no body).
async function api(path, data, method = data === undefined ? "GET" : "POST") {
  const options = { method };
  if (data !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail.map((issue) => issue.msg).join(" ")
      : result.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return result;
}
