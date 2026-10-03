export class ApiError extends Error {
  constructor(message, { status = 0, detail = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status; // 0 = never reached the server
    this.detail = detail; // raw text from the backend, for a "Details" toggle
  }
}

const GENERIC = {
  401: "GeoGuessr didn't accept the saved cookie. Add a fresh one in Settings.",
  404: "We couldn't find that.",
  409: "That's already running.",
  410: "That upload expired. Add the file again.",
  422: "Some of the information isn't valid. Check the fields and try again.",
};

// Backend messages written for people (short, one line) pass through; anything technical doesn't.
const readable = (d) => d && d.length <= 160 && !d.includes("\n") && !/traceback|file "|exception|0x[0-9a-f]+/i.test(d);

function messageFor(status, detail) {
  if (readable(detail)) return detail;
  if (status >= 500) return "Something went wrong on the server. Try again, and check the server log if it keeps happening.";
  return GENERIC[status] ?? "Something went wrong. Try again.";
}

export const api = async (path, opts = {}) => {
  let r;
  try {
    r = await fetch("/api" + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body && JSON.stringify(opts.body),
    });
  } catch {
    throw new ApiError("Can't reach the app server. Make sure it's running, then retry.", { detail: "Network error" });
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "";
    throw new ApiError(messageFor(r.status, detail), { status: r.status, detail });
  }
  return r.json();
};

export const errorText = (e) => (e instanceof ApiError ? e.message : e?.message || "Something went wrong.");
