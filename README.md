# BirdMaps Ottawa

A Python 3.9+ minimal-web-server prototype with Bird GBFS feed integration, autocomplete, and avoidance routing over OpenStreetMap streets. Requires no pip packages.

## Deploy to Render

1. Push the contents of this folder to a GitHub repository.
2. In Render, choose **New → Blueprint**, connect the repository, and select `render.yaml`.
3. Open the generated Render URL; `/api/health` should return JSON `{"ok": true, ...}`.

The server uses Render's `PORT` when set and binds to `0.0.0.0`; locally it binds to loopback and opens the browser automatically. Third-party feed availability is outside this project's control. The Ottawa GBFS endpoint may block or restrict access. No valid route is returned unless Bird polygon and parking data can be loaded. This prototype is **not** verified for real-life riding.