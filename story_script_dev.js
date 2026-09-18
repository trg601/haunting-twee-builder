setup.auth = (function () {
  var API_BASE = "https://api.tylergarman.net";
  var STORAGE_KEY = "tweebuilder.refreshToken";
  var TOKEN_TTL_MS = 30 * 24 * 60 * 60 * 1000;
  var pending = null;
  var accessToken = null;

  function readStored() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var stored = JSON.parse(raw);
      if (!stored || !stored.token || !stored.expiresAt) return null;
      if (Date.now() >= stored.expiresAt) {
        window.localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      return stored;
    } catch (err) {
      console.warn("Unable to read stored token:", err);
      return null;
    }
  }

  function store(token) {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ token: token, expiresAt: Date.now() + TOKEN_TTL_MS }),
      );
    } catch (err) {
      console.warn("Unable to persist token:", err);
    }
  }

  function clear() {
    accessToken = null;
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      console.warn("Unable to clear token:", err);
    }
  }

  function getToken() {
    var stored = readStored();
    return stored ? stored.token : null;
  }

  function acceptTokenPair(data) {
    if (!data || !data.access_token || !data.refresh_token) {
      throw new Error("Response did not include a token pair.");
    }
    accessToken = data.access_token;
    store(data.refresh_token);
    return accessToken;
  }

  function requestToken(username, password) {
    var body = new URLSearchParams();
    body.set("grant_type", "password");
    body.set("username", username);
    body.set("password", password);
    return fetch(API_BASE + "/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    })
      .then(function (response) {
        if (!response.ok) {
          return response.status === 401
            ? Promise.reject(new Error("Incorrect username or password."))
            : Promise.reject(
                new Error("Login failed (" + response.status + ")."),
              );
        }
        return response.json();
      })
      .then(acceptTokenPair);
  }

  // Rotates the stored refresh token for a fresh access token.
  function refreshAccessToken() {
    var refreshToken = getToken();
    if (!refreshToken) return Promise.reject(new Error("No stored session."));
    return fetch(API_BASE + "/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(function (response) {
        if (!response.ok) {
          clear();
          throw new Error("Session expired.");
        }
        return response.json();
      })
      .then(acceptTokenPair);
  }

  function login() {
    if (pending) return pending;
    var username = window.prompt("Username:");
    if (!username) return Promise.reject(new Error("Login cancelled."));
    var password = window.prompt("Password:");
    if (!password) return Promise.reject(new Error("Login cancelled."));

    pending = requestToken(username, password)
      .then(function (token) {
        pending = null;
        window.alert("Logged in as " + username + ".");
        return token;
      })
      .catch(function (err) {
        pending = null;
        window.alert("Login failed: " + err.message);
        throw err;
      });
    return pending;
  }

  function requireLogin() {
    if (accessToken) return Promise.resolve(accessToken);
    if (!getToken()) return login();
    return refreshAccessToken().catch(function () {
      return login();
    });
  }

  function authorizedFetch(path, options) {
    return requireLogin().then(function (token) {
      var config = Object.assign({}, options || {});
      config.headers = Object.assign({}, config.headers || {}, {
        Authorization: "Bearer " + token,
      });
      return fetch(API_BASE + path, config).then(function (response) {
        if (response.status !== 401) return response;
        // Access token expired; rotate the refresh token, falling back to a login prompt.
        accessToken = null;
        return requireLogin().then(function (newToken) {
          config.headers.Authorization = "Bearer " + newToken;
          return fetch(API_BASE + path, config);
        });
      });
    });
  }

  function reload() {
    return requireLogin()
      .then(function () {
        return authorizedFetch("/build", { method: "POST" });
      })
      .then(function (response) {
        if (!response.ok) {
          if (response.status == 409) {
            throw new Error("Build in progress... Hold your singular horse");
          }
          throw new Error("Build failed (" + response.status + ").");
        }
        window.location.reload();
      })
      .catch(function (err) {
        console.warn(err.message);
      });
  }

  return {
    apiBase: API_BASE,
    getToken: getToken,
    login: login,
    logout: clear,
    requireLogin: requireLogin,
    reload: reload,
    fetch: authorizedFetch,
  };
})();
