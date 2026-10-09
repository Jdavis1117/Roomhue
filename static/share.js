/* Before/after slider on shared room pages. */

(() => {
  const box = document.getElementById("compare");
  const slider = document.getElementById("slider");
  const set = (value) => box.style.setProperty("--cut", `${value}%`);
  slider.addEventListener("input", () => set(slider.value));
  const drag = (event) => {
    const rect = box.getBoundingClientRect();
    const value = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100));
    slider.value = String(Math.round(value));
    set(value);
  };
  box.addEventListener("pointerdown", (event) => {
    box.setPointerCapture(event.pointerId);
    drag(event);
    box.addEventListener("pointermove", drag);
  });
  box.addEventListener("pointerup", () => box.removeEventListener("pointermove", drag));
  box.addEventListener("pointercancel", () => box.removeEventListener("pointermove", drag));
})();
