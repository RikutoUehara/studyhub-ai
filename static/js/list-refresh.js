(() => {
  const key = "studyhub-list-version";
  let version;
  try {
    version = localStorage.getItem(key);
  } catch {
    return;
  }

  window.addEventListener("pageshow", (event) => {
    if (event.persisted && localStorage.getItem(key) !== version) {
      window.location.reload();
    }
  });
})();
