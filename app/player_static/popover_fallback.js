export function bindPopoverFallbackDismiss(dialog, onHide) {
  const isOpen = () => dialog.hasAttribute("open");
  document.addEventListener("click", (e) => {
    if (isOpen() && !dialog.contains(e.target)) onHide();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) onHide();
  });
}
