---
name: Publishing package installs
description: Environment-specific pip user-install conflict during publishing.
---

Replit's pip wrapper can load a platform-provided configuration with `user = yes`, while the publishing interpreter disables user site-packages. This fails before application startup even when the same requirements previously published successfully.

**Why:** Build logs showed “Can not perform a '--user' install. User site-packages are disabled for this Python.” The platform pip configuration enabled user installs; no project-defined pip user setting existed.

**How to apply:** Keep user-install mode disabled and use the writable project package prefix rather than the immutable Nix Python installation. A dependency dry run succeeded with this approach, but only a subsequent user-initiated publish confirms the production build.
