---
name: Publishing package installs
description: Environment-specific pip user-install conflict during publishing.
---

Replit's pip wrapper can load a platform-provided configuration with `user = yes`, while the publishing interpreter disables user site-packages. This fails before application startup even when the same requirements previously published successfully.

**Why:** Build logs showed “Can not perform a '--user' install. User site-packages are disabled for this Python.” The platform pip configuration enabled user installs; no project-defined pip user setting existed.

**How to apply:** Shared environment settings alone did not fix publishing: the next build still failed during the automatic package-install step. Disable automatic publishing installs with the documented `packager.features.enabledForHosting = false`, and perform the requirements install in an explicit build command with `PIP_USER=false` and a writable project prefix. Do not treat a shell dry run as proof that the automatic installer receives shared settings. Only a subsequent user-initiated publish confirms the production build.
