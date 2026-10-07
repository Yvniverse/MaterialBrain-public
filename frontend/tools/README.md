# Runtime gallery capture

These helpers capture the running public application through normal browser interactions. They do not substitute API results, change application state through JavaScript, or compose images. Keep the authentication storage state, capture plan and breakpoint reports outside the repository.

Install the frontend dependencies and a Playwright Chromium browser, start an isolated public Compose installation, then save an authenticated Playwright `storageState` file. Use a neutral example account and synthetic inventory. The application source must be committed and both running services must report that commit in `/build-info.json` and `/api/v1/runtime-info` before a formal gallery capture.

Set `MB_SHOWCASE_PLAN` to an external JSON file. The plan provides `base_url`, `auth_state`, `output_dir`, `report_dir` and per-image `shots` settings. The base URL must be an explicit loopback public installation. Each shot may provide:

- `route`: its actual route, including a selected location or registered mission goals.
- `state`: a description of the state the UI interactions will produce.
- `steps`: normal UI interactions used for the formal image.
- `responsive_steps`: normal UI interactions used for breakpoint inspection.
- `cleanup_steps`: normal UI recovery actions after the image is captured, including return to HOME.
- `evidence`: selectors identifying the actual Agent result, summary values, open equipment, selected compartment, or recovery events.

Supported steps are `click`, `fill`, `select`, `check`, `scroll-visible`, `wait-visible`, `wait-text`, `wait-diagnostic`, `handoff-current`, `handoff-server-current` and `finish-server-mission`. A diagnostic wait reads the existing Three.js scene interface; it cannot set the scene or mission state. `handoff-current` reads the registered slot of the current local simulation station and enters it in the real scan dialog. `handoff-server-current` enters the actual Nav2 station code in the server mission's scan form. `finish-server-mission` confirms later observed Nav2 arrivals through the normal scan form and waits for return to HOME.

The recovery gallery image requires server execution at the `ros2_nav2_simulation` boundary, a preserved verified handoff, a registered obstacle and observed recovery events. Open the actual mission replay; the helper records critical events from that server episode response, with its response hash and mission identity. Cleanup then closes the replay, removes the selected obstacle through the UI, completes later handoffs and verifies HOME completion with unchanged inventory totals. Local browser simulation events do not satisfy that gallery requirement.

For example, a step can select an actual equipment card:

```json
{"action": "click", "selector": ".g-asset-item:has(.drawer_rack_100) >> nth=0"}
```

Run from `frontend/`:

```bash
node tools/capture_showcase.mjs responsive
node tools/capture_showcase.mjs gallery
```

`responsive` captures the requested 390, 768, 1024, 1440, 1728 and 1920 widths, checks overflow and stage size, and records real scene diagnostics. Set `responsive_kinds` in the plan to select warehouse, laboratory, Agent, dashboard and storage pages. `gallery` requires all six product states and writes the images plus `docs/screenshots/manifest.json` only after successful capture.

Inspect every screenshot visually. The manifest initially records `visual_review.status` as `pending`; after actual inspection, record `status: "passed"`, `reviewer` and a `reviewed_at_utc` timestamp ending in `Z`. Keep the capture hashes and runtime metadata unchanged.

From the repository root, validate the integration and final gallery:

```bash
python tools/check_glacier_ui.py
python tools/validate_screenshots.py --app-sha APPLICATION_COMMIT
```

The gallery gate checks all six files, PNG dimensions, duplicate and near-blank frames, byte hashes, matching runtime revisions, route and state evidence, visual review metadata, and local image links in both READMEs. It reports errors and never generates replacement images or missing capture metadata. `--images-only` is for inspection before the bilingual README is integrated; final release validation must use the complete gate.
