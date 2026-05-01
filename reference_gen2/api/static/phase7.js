(() => {
  // Total budget ~= 15 minutes. We use gentle backoff so short jobs feel
  // instant but long PDFs keep the spinner alive until the job actually
  // reaches a terminal state.
  const JOB_POLL_MAX_ELAPSED_MS = 15 * 60 * 1000;
  const JOB_POLL_INITIAL_DELAY_MS = 500;
  const JOB_POLL_MAX_DELAY_MS = 2000;
  const JOB_POLL_BACKOFF_FACTOR = 1.25;
  const CHALLENGE_SOLVE_YIELD_EVERY = 100;
  const form = document.getElementById("upload-form");
  const textForm = document.getElementById("text-form");
  const uploadTab = document.getElementById("upload-tab");
  const textTab = document.getElementById("text-tab");
  const uploadPanel = document.getElementById("upload-panel");
  const textPanel = document.getElementById("text-panel");
  const fileInput = document.getElementById("file");
  const fileName = document.getElementById("file-name");
  const dropzone = document.getElementById("dropzone");
  const submit = document.getElementById("submit");
  const textSubmit = document.getElementById("text-submit");
  const textInput = document.getElementById("reference-list-text");
  const textStyleHint = document.getElementById("text-style-hint");
  const charCount = document.getElementById("char-count");
  const status = document.getElementById("status");
  const panel = document.querySelector(".panel");
  const result = document.getElementById("result");
  const openReport = document.getElementById("open-report");
  const cookieNotice = document.getElementById("cookie-notice");
  const cookieDismiss = document.getElementById("cookie-dismiss");
  const cookieDetailsToggle = document.getElementById("cookie-details-toggle");
  const cookieDetails = document.getElementById("cookie-details");

  const DEFAULT_LABEL = "Rapport genereren";
  const BUSY_LABEL = "Rapport wordt gemaakt...";
  const COOKIE_NOTICE_STORAGE_KEY = "reference_gen2_cookie_notice_seen";

  let activeButton = null;

  const safeMessages = {
    empty_reference_text: "Plak eerst een referentielijst.",
    invalid_reference_text: "De geplakte tekst kon niet worden verwerkt.",
    invalid_text_report_payload: "De tekstaanvraag kon niet worden verwerkt.",
    invalid_signature: "Het bestand lijkt geen geldige PDF of DOCX te zijn.",
    invalid_pdf_container: "De PDF kon niet veilig worden gecontroleerd.",
    invalid_docx_container: "Het DOCX-bestand kon niet veilig worden gecontroleerd.",
    unsupported_extension: "Alleen PDF- en DOCX-bestanden worden ondersteund.",
    mime_mismatch: "Het bestandstype komt niet overeen met het opgegeven contenttype.",
    suspicious_pdf_structure: "De PDF is te complex om veilig te controleren.",
    suspicious_docx_content: "Het DOCX-bestand bevat inhoud die niet veilig verwerkt kan worden.",
    page_limit_exceeded: "De PDF heeft te veel pagina's. Upload een kortere versie of plak de referentielijst als tekst.",
    text_too_large: "De tekst die uit het document is gehaald is te lang om in een keer te verwerken.",
    no_extractable_text: "Er kon geen leesbare tekst uit het document worden gehaald. Gebruik OCR of plak de referentielijst als tekst.",
    extraction_timeout: "Tekst uit het document halen duurde te lang. Probeer een kleiner bestand of plak de referentielijst als tekst.",
    extraction_failed: "Tekst uit het document halen is mislukt. Exporteer het document opnieuw of plak de referentielijst als tekst.",
    bibliography_heading_not_found: "Er is geen kop voor een literatuurlijst of referentielijst gevonden.",
    bibliography_detection_failed: "De literatuurlijst kon niet worden herkend.",
    empty_bibliography_section: "Er is een kop voor de literatuurlijst gevonden, maar daarna geen bruikbare referenties.",
    bibliography_section_too_short: "De gevonden literatuurlijst is te kort om te verwerken.",
    bibliography_section_too_large: "De gevonden literatuurlijst is te lang om te verwerken.",
    segmentation_no_references: "Er konden geen losse referenties in de literatuurlijst worden herkend.",
    reference_text_invalid_characters: "De geplakte tekst bevat niet-ondersteunde tekens.",
    reference_text_too_large: "De geplakte tekst is te lang om in een keer te verwerken.",
    request_too_large: "Het bestand is te groot om te verwerken.",
    too_many_inflight_jobs: "Er worden nu te veel rapporten tegelijk gemaakt. Probeer het zo opnieuw.",
    too_many_queued_jobs: "Er wachten nu te veel rapporten. Probeer het zo opnieuw.",
    rate_limited: "Er zijn te veel aanvragen in korte tijd verstuurd. Wacht even en probeer het opnieuw.",
    challenge_required: "Er is een korte verificatie nodig voordat het rapport kan worden gemaakt.",
    invalid_challenge: "De verificatie is verlopen. Probeer het opnieuw.",
    report_generation_not_configured: "De rapportservice is nog niet geconfigureerd.",
    invalid_style_hint: "De geselecteerde stijl wordt niet ondersteund.",
    invalid_job_or_session: "De rapportsessie is ongeldig of verlopen.",
    job_not_found_or_expired: "De rapportaanvraag is niet meer beschikbaar.",
    invalid_request: "De aanvraag kon niet worden verwerkt.",
    internal_server_error: "Er ging iets mis tijdens het maken van het rapport."
  };

  function setStatus(message, kind) {
    status.textContent = message || "";
    status.dataset.kind = kind || "";
  }

  function setButtonLabel(button, text, withSpinner) {
    // Safely rebuild the button contents without innerHTML.
    while (button.firstChild) {
      button.removeChild(button.firstChild);
    }
    if (withSpinner) {
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.appendChild(spinner);
    }
    const label = document.createElement("span");
    label.className = "btn-label";
    label.textContent = text;
    button.appendChild(label);
  }

  function resetButton(button) {
    if (!button) return;
    button.type = "submit";
    setButtonLabel(button, DEFAULT_LABEL, false);
  }

  function resetAllButtons() {
    resetButton(submit);
    resetButton(textSubmit);
  }

  function setBusy(isBusy) {
    submit.disabled = isBusy;
    textSubmit.disabled = isBusy;
    if (panel) {
      panel.classList.toggle("is-busy", !!isBusy);
    }
    if (isBusy) {
      const target = activeButton || submit;
      setButtonLabel(target, BUSY_LABEL, true);
      // Other button keeps default label but stays disabled.
      const other = target === submit ? textSubmit : submit;
      setButtonLabel(other, DEFAULT_LABEL, false);
    } else {
      const target = activeButton || submit;
      setButtonLabel(target, DEFAULT_LABEL, false);
    }
  }

  function hideResult() {
    if (openReport) {
      openReport.removeAttribute("href");
    }
    if (result) {
      result.hidden = true;
      result.classList.remove("is-visible");
    }
  }

  function showResult(url) {
    if (!url || !openReport || !result) return;
    openReport.href = url;
    openReport.target = "_blank";
    openReport.rel = "noopener noreferrer";
    result.hidden = false;
    result.classList.add("is-visible");
  }

  async function pollJob(jobId) {
    const startedAt = Date.now();
    let delay = JOB_POLL_INITIAL_DELAY_MS;

    while (Date.now() - startedAt < JOB_POLL_MAX_ELAPSED_MS) {
      let response;
      try {
        response = await fetch(`/jobs/${encodeURIComponent(jobId)}`);
      } catch (_networkError) {
        // Transient network hiccup: keep trying until the overall budget is up.
        await new Promise((resolve) => window.setTimeout(resolve, delay));
        delay = Math.min(Math.round(delay * JOB_POLL_BACKOFF_FACTOR), JOB_POLL_MAX_DELAY_MS);
        continue;
      }
      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        const code = payload && payload.error && payload.error.code;
        throw new Error(safeMessages[code] || "De rapportstatus kon niet worden opgehaald.");
      }

      if (payload.report_url) {
        return payload;
      }

      if (payload.status === "gone") {
        throw new Error(safeMessages["job_not_found_or_expired"] || "Het rapport is niet meer beschikbaar.");
      }

      if (payload.status === "failed" && payload.error) {
        const code = payload.error.code;
        throw new Error(safeMessages[code] || payload.error.message || "Het rapport kon niet worden gemaakt.");
      }

      // Still pending/processing/running — update a soft status so the user
      // knows we are still waiting, keep the spinner + panel lock on.
      const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
      setStatus(
        `Het rapport wordt gemaakt. Dit kan even duren (${elapsedSeconds}s).`,
        ""
      );

      await new Promise((resolve) => window.setTimeout(resolve, delay));
      delay = Math.min(Math.round(delay * JOB_POLL_BACKOFF_FACTOR), JOB_POLL_MAX_DELAY_MS);
    }

    throw new Error("Het rapport wordt nog verwerkt. Probeer het zo opnieuw of vernieuw de pagina.");
  }

  async function finalizeJob(payload) {
    let current = payload || {};
    if (!current.report_url && current.job_id) {
      setStatus("De aanvraag is ontvangen. Het rapport wordt klaargezet.", "");
      current = await pollJob(current.job_id);
    }

    if (!current.report_url) {
      throw new Error("Het rapport kon niet worden geopend.");
    }

    resetButton(activeButton || submit);
    showResult(current.report_url);
    setStatus("", "");
  }

  async function requestChallengeProof() {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("De browser ondersteunt de verificatie niet.");
    }
    setStatus("Korte verificatie wordt uitgevoerd.", "");
    const response = await fetch("/challenge", {
      method: "GET",
      headers: { "Accept": "application/json" }
    });
    const challenge = await response.json().catch(() => ({}));
    if (!response.ok || challenge.algorithm !== "SHA-256") {
      throw new Error("De verificatie kon niet worden gestart.");
    }
    const number = await solveChallenge(challenge);
    return encodeChallengePayload({
      algorithm: challenge.algorithm,
      challenge: challenge.challenge,
      number,
      salt: challenge.salt,
      signature: challenge.signature
    });
  }

  async function solveChallenge(challenge) {
    const maxNumber = Number(challenge.maxnumber);
    if (!Number.isSafeInteger(maxNumber) || maxNumber < 0 || maxNumber > 500000) {
      throw new Error("De verificatie kon niet worden verwerkt.");
    }
    const encoder = new TextEncoder();
    const startedAt = Date.now();
    for (let number = 0; number <= maxNumber; number += 1) {
      const digest = await window.crypto.subtle.digest(
        "SHA-256",
        encoder.encode(`${challenge.salt}${number}`)
      );
      if (hexDigest(digest) === challenge.challenge) {
        return number;
      }
      if (number > 0 && number % CHALLENGE_SOLVE_YIELD_EVERY === 0) {
        const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
        setStatus(`Korte verificatie wordt uitgevoerd (${elapsedSeconds}s).`, "");
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      }
    }
    throw new Error("De verificatie kon niet worden afgerond.");
  }

  function hexDigest(buffer) {
    const bytes = new Uint8Array(buffer);
    let output = "";
    for (const byte of bytes) {
      output += byte.toString(16).padStart(2, "0");
    }
    return output;
  }

  function encodeChallengePayload(payload) {
    return window.btoa(JSON.stringify(payload));
  }

  async function parseJson(response) {
    return response.json().catch(() => ({}));
  }

  async function retryWithChallenge(sendRequest) {
    let response = await sendRequest(null);
    let payload = await parseJson(response);
    const code = payload && payload.error && payload.error.code;
    if (response.ok || code !== "challenge_required") {
      return { response, payload };
    }
    const proof = await requestChallengeProof();
    response = await sendRequest(proof);
    payload = await parseJson(response);
    return { response, payload };
  }

  function selectedFile() {
    return fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
  }

  function setMode(mode) {
    const isText = mode === "text";
    uploadTab.setAttribute("aria-selected", isText ? "false" : "true");
    textTab.setAttribute("aria-selected", isText ? "true" : "false");
    uploadPanel.hidden = isText;
    textPanel.hidden = !isText;
    resetAllButtons();
    hideResult();
    setStatus("", "");
  }

  function updateCharCount() {
    const length = textInput.value.length;
    charCount.textContent = `${length} tekens`;
    hideResult();
    setStatus("", "");
  }

  function updateFileName() {
    const file = selectedFile();
    fileName.textContent = file ? file.name : "";
    hideResult();
    setStatus("", "");
  }

  function initCookieNotice() {
    if (!cookieNotice || !cookieDismiss || !cookieDetailsToggle || !cookieDetails) {
      return;
    }

    try {
      if (window.localStorage.getItem(COOKIE_NOTICE_STORAGE_KEY) === "1") {
        cookieNotice.hidden = true;
        return;
      }
    } catch (_storageError) {
      // If browser storage is unavailable, keep showing the notice.
    }

    cookieDetailsToggle.addEventListener("click", () => {
      const isExpanded = cookieDetailsToggle.getAttribute("aria-expanded") === "true";
      cookieDetails.hidden = isExpanded;
      cookieDetailsToggle.setAttribute("aria-expanded", isExpanded ? "false" : "true");
      cookieDetailsToggle.textContent = isExpanded ? "Meer informatie" : "Minder informatie";
    });

    cookieDismiss.addEventListener("click", () => {
      try {
        window.localStorage.setItem(COOKIE_NOTICE_STORAGE_KEY, "1");
      } catch (_storageError) {
        // Closing the notice should still work when storage is blocked.
      }
      cookieNotice.hidden = true;
    });
  }

  uploadTab.addEventListener("click", () => setMode("upload"));
  textTab.addEventListener("click", () => setMode("text"));
  textInput.addEventListener("input", updateCharCount);
  fileInput.addEventListener("change", updateFileName);

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      // When the panel is busy, block drag interactions too.
      if (panel && panel.classList.contains("is-busy")) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, () => {
      dropzone.classList.remove("is-dragging");
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!selectedFile()) {
      setStatus("Kies eerst een PDF- of DOCX-bestand.", "error");
      return;
    }

    activeButton = submit;
    hideResult();
    setBusy(true);
    setStatus("Het rapport wordt gemaakt. Dit kan even duren.", "");

    try {
      const { response, payload } = await retryWithChallenge((proof) => {
        const body = new FormData(form);
        if (proof) {
          body.append("altcha", proof);
        }
        return fetch("/reports/upload", {
          method: "POST",
          body
        });
      });

      if (!response.ok) {
        const code = payload && payload.error && payload.error.code;
        setStatus(safeMessages[code] || "Het rapport kon niet worden gemaakt.", "error");
        return;
      }

      await finalizeJob(payload);
    } catch (_error) {
      setStatus(_error && _error.message ? _error.message : "Er is geen verbinding met de rapportservice.", "error");
    } finally {
      setBusy(false);
    }
  });

  textForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const referenceListText = textInput.value.trim();
    if (!referenceListText) {
      setStatus("Plak eerst een referentielijst.", "error");
      return;
    }

    activeButton = textSubmit;
    hideResult();
    setBusy(true);
    setStatus("Het rapport wordt gemaakt. Dit kan even duren.", "");

    try {
      const { response, payload } = await retryWithChallenge((proof) => {
        const body = {
          reference_list_text: referenceListText,
          style_hint: textStyleHint.value
        };
        if (proof) {
          body.altcha = proof;
        }
        return fetch("/reports/text", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      });

      if (!response.ok) {
        const code = payload && payload.error && payload.error.code;
        setStatus(safeMessages[code] || "Het rapport kon niet worden gemaakt.", "error");
        return;
      }

      await finalizeJob(payload);
    } catch (_error) {
      setStatus(_error && _error.message ? _error.message : "Er is geen verbinding met de rapportservice.", "error");
    } finally {
      setBusy(false);
    }
  });

  initCookieNotice();
})();
