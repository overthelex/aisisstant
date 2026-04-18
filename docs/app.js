(async () => {
  const btn = document.getElementById("download-btn");
  const meta = document.getElementById("download-meta");
  const pill = document.getElementById("version-pill");
  const debName = document.getElementById("deb-name");

  const fallbackHref = "https://github.com/overthelex/aisisstant/releases/latest";

  try {
    const res = await fetch(
      "https://api.github.com/repos/overthelex/aisisstant/releases/latest",
      { headers: { Accept: "application/vnd.github+json" } }
    );
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();

    const deb = (data.assets || []).find(
      (a) => a.name.endsWith(".deb") && a.content_type === "application/x-debian-package"
    );

    const tag = data.tag_name || "";
    const size = deb ? (deb.size / 1024).toFixed(0) + " KB" : "";

    if (deb) {
      btn.href = deb.browser_download_url;
      meta.textContent = `Ubuntu .deb · ${tag}${size ? " · " + size : ""}`;
      if (debName) debName.textContent = deb.name;
    } else {
      btn.href = fallbackHref;
      meta.textContent = `${tag} · Ubuntu .deb`;
    }

    if (pill) {
      pill.textContent = tag ? `v${tag}` : "latest";
      pill.classList.remove("pill-muted");
    }
  } catch (err) {
    // Fall back to the latest-release page
    btn.href = fallbackHref;
    meta.textContent = "latest release · Ubuntu .deb";
    if (pill) {
      pill.textContent = "latest";
      pill.classList.add("pill-muted");
    }
  }
})();
