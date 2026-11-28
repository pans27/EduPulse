// Send a same-origin API request and expose any validation message.
async function api(path, data, method = data === undefined ? "GET" : "POST") {
  const response = await fetch(path, data === undefined ? { method } : {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail.map((issue) => issue.msg).join(" ")
      : result.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return result;
}
