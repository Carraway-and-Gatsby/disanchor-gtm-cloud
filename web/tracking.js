/* No Google tag, cookies, storage, PII, or network analytics by default.
 * Integrator supplies a consent-aware emitter only after the privacy/CMP gate.
 */
(() => {
  "use strict";
  let consent = () => false;
  let emit = null;
  let contentId = null;
  let viewed = false;
  const allowedEvents = new Set(["cta_click", "lead_form_start", "view_research_form", "generate_lead"]);
  const form = document.getElementById("research-form");
  let approvedIds = [];
  try { approvedIds = JSON.parse(form?.dataset.contentIds || "[]"); } catch (_) { /* unconfigured static preview */ }
  const queryContent = new URLSearchParams(window.location.search).get("utm_content");
  if (Array.isArray(approvedIds) && approvedIds.includes(queryContent)) contentId = queryContent;

  function hasConsent() {
    try { return consent() === true; } catch (_) { return false; }
  }
  function event(name) {
    if (!hasConsent() || typeof emit !== "function" || !allowedEvents.has(name)) return false;
    const params = {
      method: "research_interest",
      page_location: window.location.origin + "/research-interest",
      page_referrer: "", page_title: "Disanchor research interest"
    };
    if (contentId) params.content_id = contentId;
    try { emit(name, params); return true; } catch (_) { return false; }
  }
  window.DisanchorTracking = Object.freeze({
    configure(options = {}) {
      consent = typeof options.hasAnalyticsConsent === "function" ? options.hasAnalyticsConsent : () => false;
      emit = typeof options.emit === "function" ? options.emit : null;
      if (!viewed && form) viewed = event("view_research_form");
    },
    // Public callers cannot emit generate_lead without a successful server result.
    event(name) { return name === "generate_lead" ? false : event(name); }
  });
  document.querySelectorAll("[data-gtm-cta]").forEach(link => link.addEventListener("click", () => event("cta_click")));
  if (!form) return;
  let started = false, submitting = false;
  form.addEventListener("focusin", () => {
    if (!started) started = event("lead_form_start");
  });
  form.addEventListener("submit", async (submission) => {
    submission.preventDefault();
    if (submitting || !form.reportValidity()) return;
    const status = document.getElementById("form-status");
    const button = form.querySelector("button[type=submit]");
    const fields = new FormData(form);
    const analyticsConsent = hasConsent();
    const payload = {
      email: fields.get("email"), interest: fields.get("interest"),
      consent: fields.get("consent") === "on", consent_version: form.dataset.noticeVersion,
      analytics_consent: analyticsConsent, website: fields.get("website") || "",
      content_id: analyticsConsent ? contentId : null
    };
    submitting = true; button.disabled = true; status.textContent = "Saving your interest…";
    try {
      const response = await fetch("/api/research-interest", {
        method: "POST", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || result.ok !== true) {
        status.textContent = response.status === 429 ? "Too many attempts. Please try again in 10 minutes." : "We couldn’t save this. Check your details and consent, then try again.";
        return;
      }
      // A click or validation success is never counted as a stored lead.
      if (result.new === true && analyticsConsent && hasConsent()) event("generate_lead");
      status.textContent = "Thank you. Your research interest has been received.";
      form.reset();
    } catch (_) {
      status.textContent = "We couldn’t confirm the save. Please try again; duplicate entries are not counted again.";
    } finally { submitting = false; button.disabled = false; }
  });
})();
