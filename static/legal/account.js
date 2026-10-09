/* Privacy page: lets a signed-in visitor delete their account and saved rooms. */

(async () => {
  const box = document.getElementById("delete-box");
  if (!box) return;
  let user = null;
  try {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (response.ok) user = (await response.json()).user;
  } catch {
    return;
  }
  if (!user) return;

  const intro = document.createElement("p");
  intro.textContent = `You're signed in as ${user.name}. This removes your account record and every room you've saved.`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "danger";
  button.textContent = "Delete my account and data";
  const status = document.createElement("p");
  status.className = "notice";
  status.setAttribute("role", "status");
  box.replaceChildren(intro, button, status);

  button.addEventListener("click", async () => {
    if (!window.confirm("Delete your RoomRoller account and all saved rooms? This can't be undone.")) return;
    button.disabled = true;
    try {
      const response = await fetch("/api/account", { method: "DELETE", credentials: "same-origin" });
      if (!response.ok) throw new Error();
      box.replaceChildren(status);
      status.textContent = "Your account and saved rooms have been deleted, and you've been signed out.";
    } catch {
      button.disabled = false;
      status.textContent = "That didn't go through. Try again, or email privacy@roomroller.com.";
    }
  });
})();
