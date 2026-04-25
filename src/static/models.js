function selectedProviderOption(select) {
  return select.options[select.selectedIndex];
}

function formatBytes(bytes) {
  if (!bytes || bytes < 1024) {
    return "";
  }
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const precision = value >= 10 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

function setStatus(form, { text, tone }) {
  const status = form.querySelector("[data-ollama-status]");
  const pill = form.querySelector("[data-ollama-pill]");
  if (status) {
    status.textContent = text;
  }
  if (pill) {
    pill.classList.remove("pill--ok", "pill--err", "pill--warn");
    if (tone === "ok") {
      pill.classList.add("pill--ok");
    } else if (tone === "err") {
      pill.classList.add("pill--err");
    } else if (tone === "warn") {
      pill.classList.add("pill--warn");
    }
    pill.textContent = labelForTone(tone);
  }
}

function labelForTone(tone) {
  switch (tone) {
    case "ok":
      return "Connected";
    case "err":
      return "Unreachable";
    case "warn":
      return "No models";
    case "loading":
      return "Loading...";
    default:
      return "Idle";
  }
}

function resetOllamaSelect(select) {
  select.replaceChildren(new Option("Choose an installed local model...", ""));
}

function buildOptgroup(label, entries, { saved = false } = {}) {
  const group = document.createElement("optgroup");
  group.label = label;
  for (const entry of entries) {
    const sizeLabel = formatBytes(entry.size_bytes);
    const text = sizeLabel ? `${entry.name} \u00b7 ${sizeLabel}` : entry.name;
    const option = new Option(text, entry.name);
    if (saved) {
      option.disabled = true;
    }
    group.append(option);
  }
  return group;
}

async function loadOllamaModels(form) {
  const providerSelect = form.querySelector("[data-provider-select]");
  const picker = form.querySelector("[data-ollama-picker]");
  const ollamaSelect = form.querySelector("[data-ollama-select]");
  const retryButton = form.querySelector("[data-ollama-retry]");
  const syncButton = form.querySelector("[data-ollama-sync]");
  if (!providerSelect || !picker || !ollamaSelect) {
    return;
  }

  const provider = selectedProviderOption(providerSelect);
  const isOllama = provider?.dataset.endpointStyle === "ollama_generate";
  picker.hidden = !isOllama;
  resetOllamaSelect(ollamaSelect);
  if (retryButton) {
    retryButton.hidden = true;
  }
  if (syncButton) {
    syncButton.hidden = !isOllama;
    syncButton.disabled = false;
    syncButton.textContent = "Sync all installed";
  }
  if (!isOllama) {
    setStatus(form, { text: "Manual model entry is available for this provider.", tone: "idle" });
    return;
  }

  setStatus(form, { text: "Loading installed Ollama models...", tone: "loading" });
  try {
    const response = await fetch(`/api/providers/${provider.value}/ollama-models`);
    if (!response.ok) {
      throw new Error("Ollama is not reachable");
    }
    const payload = await response.json();
    const saved = new Set(payload.saved || []);
    const entries = payload.models || [];
    const available = entries.filter((entry) => !saved.has(entry.name));
    const alreadySaved = entries.filter((entry) => saved.has(entry.name));

    if (entries.length === 0) {
      setStatus(form, { text: "No installed Ollama models found.", tone: "warn" });
      if (syncButton) {
        syncButton.disabled = true;
      }
      return;
    }

    if (available.length > 0) {
      ollamaSelect.append(buildOptgroup("Available to add", available));
    }
    if (alreadySaved.length > 0) {
      ollamaSelect.append(buildOptgroup("Already saved", alreadySaved, { saved: true }));
    }

    const summary =
      available.length > 0
        ? `${entries.length} installed (${available.length} new).`
        : `${entries.length} installed, all already saved.`;
    setStatus(form, { text: summary, tone: "ok" });
    if (syncButton) {
      syncButton.disabled = available.length === 0;
    }
  } catch (_error) {
    setStatus(form, {
      text: "Ollama is not reachable. You can still type the model name manually.",
      tone: "err",
    });
    if (retryButton) {
      retryButton.hidden = false;
    }
    if (syncButton) {
      syncButton.disabled = true;
    }
  }
}

async function syncOllamaModels(form) {
  const providerSelect = form.querySelector("[data-provider-select]");
  const syncButton = form.querySelector("[data-ollama-sync]");
  if (!providerSelect || !syncButton) {
    return;
  }
  const provider = selectedProviderOption(providerSelect);
  if (provider?.dataset.endpointStyle !== "ollama_generate") {
    return;
  }
  syncButton.disabled = true;
  syncButton.textContent = "Syncing...";
  setStatus(form, { text: "Importing installed Ollama models...", tone: "loading" });
  try {
    const response = await fetch(`/api/providers/${provider.value}/ollama-sync`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error("Sync failed");
    }
    const payload = await response.json();
    const created = payload.created || [];
    if (created.length === 0) {
      setStatus(form, { text: "All installed models were already saved.", tone: "ok" });
      syncButton.textContent = "Sync all installed";
      syncButton.disabled = true;
      return;
    }
    setStatus(form, {
      text: `Imported ${created.length} model${created.length === 1 ? "" : "s"}. Refreshing...`,
      tone: "ok",
    });
    window.location.reload();
  } catch (_error) {
    setStatus(form, { text: "Sync failed. Try again or add manually.", tone: "err" });
    syncButton.disabled = false;
    syncButton.textContent = "Sync all installed";
  }
}

function syncSelectedModel(form) {
  const ollamaSelect = form.querySelector("[data-ollama-select]");
  const modelName = form.querySelector("[data-model-name]");
  const displayName = form.querySelector("[data-display-name]");
  if (!ollamaSelect?.value || !modelName || !displayName) {
    return;
  }
  modelName.value = ollamaSelect.value;
  displayName.value = ollamaSelect.value;
}

function setupModelForm(form) {
  const providerSelect = form.querySelector("[data-provider-select]");
  const ollamaSelect = form.querySelector("[data-ollama-select]");
  const retryButton = form.querySelector("[data-ollama-retry]");
  const syncButton = form.querySelector("[data-ollama-sync]");
  const details = form.closest("details");

  providerSelect?.addEventListener("change", () => loadOllamaModels(form));
  ollamaSelect?.addEventListener("change", () => syncSelectedModel(form));
  retryButton?.addEventListener("click", () => loadOllamaModels(form));
  syncButton?.addEventListener("click", () => syncOllamaModels(form));

  if (details && !details.open) {
    let loaded = false;
    details.addEventListener("toggle", () => {
      if (details.open && !loaded) {
        loaded = true;
        loadOllamaModels(form);
      }
    });
    return;
  }
  loadOllamaModels(form);
}

for (const form of document.querySelectorAll("[data-model-form]")) {
  setupModelForm(form);
}
