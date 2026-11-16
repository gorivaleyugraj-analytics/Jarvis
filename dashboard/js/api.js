/* JARVIS X API client — bearer-token fetch wrapper. */
const API = {
  token: localStorage.getItem("jarvis_token") || "dev-token",

  setToken(value) {
    this.token = value.trim() || "dev-token";
    localStorage.setItem("jarvis_token", this.token);
  },

  async request(path, { method = "GET", body } = {}) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: {
          "Authorization": `Bearer ${this.token}`,
          "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new Error("Backend unreachable — is the server running?");
    }
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      /* non-JSON response */
    }
    if (!res.ok) {
      const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `${res.status} ${res.statusText}`;
      const error = new Error(detail);
      error.status = res.status;
      throw error;
    }
    return data;
  },

  get(path) {
    return this.request(path);
  },
  post(path, body = {}) {
    return this.request(path, { method: "POST", body });
  },
  del(path) {
    return this.request(path, { method: "DELETE" });
  },
};
