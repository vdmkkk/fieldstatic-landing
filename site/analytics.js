/* Fieldstatic — first-party, self-hosted analytics (PostHog).
 *
 * Loaded SYNCHRONOUSLY in <head> on every page so the A/B price helper
 * (window.fsOnPrice / window.fsPrice) exists before the cart's inline script
 * runs. This file itself sets no cookies and sends nothing on its own — the
 * PostHog bundle, pageviews, autocapture, heatmaps and session replay only
 * load AFTER the visitor accepts analytics in the cookie banner
 * (the shared `fs_consent` flag + `fs-consent-granted` event from index.html).
 *
 * It provides:
 *   - window.fsTrack(event, props)      safe no-op until PostHog is live
 *   - window.fsPrice / window.fsOnPrice the resolved A/B price variant
 *   - per-section dwell timing           (uses [data-screen-label])
 *   - order-link click tracking          (order_clicked)
 */
(function () {
  'use strict';

  /* =====================================================================
   * CONFIG — fill these in once your PostHog VPS is up (see
   * deploy/posthog-vps.md). While POSTHOG_KEY is empty, nothing loads and
   * everyone simply sees the `control` price — the site keeps working.
   * ===================================================================== */
  var POSTHOG_KEY  = 'phc_vEcryDPNTsJT5EWgsXPg4LockExjmr9zrNeYaXLB2QXc';  // project API key (public, write-only)
  var POSTHOG_HOST = 'https://ph.fieldstatic.shop';   // your self-hosted PostHog URL

  var EXPERIMENT = 'price_test';                      // PostHog feature-flag / experiment key
  // Variant keys MUST match the ones you create in the PostHog experiment.
  // Edit the numbers to your real test prices (USD).
  var PRICE_VARIANTS = {
    control: { unit: 14.99 },
    mid:     { unit: 19.99 },
    high:    { unit: 24.99 }
  };
  /* ===================================================================== */

  var HAS_KEY = !!POSTHOG_KEY;

  function consentGranted() {
    try { return localStorage.getItem('fs_consent') === 'granted'; } catch (e) { return false; }
  }

  /* ---- safe event helper (no-op until PostHog has booted) -------------- */
  window.fsTrack = function (event, props) {
    try { if (window.posthog && booted) window.posthog.capture(event, props || {}); } catch (e) {}
  };

  /* ---- A/B price helper ----------------------------------------------- *
   * Resolves exactly once to a variant + unit price. Consumers (the cart)
   * call window.fsOnPrice(cb); cb always fires — synchronously if already
   * resolved, otherwise when flags arrive (or on the safety timeout). */
  var fsPrice = window.fsPrice = {
    variant: 'control',
    unit: PRICE_VARIANTS.control.unit,
    resolved: false,
    _cbs: [],
    onReady: function (cb) {
      if (this.resolved) { try { cb(this); } catch (e) {} }
      else { this._cbs.push(cb); }
    },
    _resolve: function (v) {
      if (this.resolved) return;
      if (!v || !PRICE_VARIANTS[v]) v = 'control';
      this.variant = v;
      this.unit = PRICE_VARIANTS[v].unit;
      this.resolved = true;
      var cbs = this._cbs; this._cbs = [];
      for (var i = 0; i < cbs.length; i++) { try { cbs[i](this); } catch (e) {} }
    }
  };
  window.fsOnPrice = function (cb) { fsPrice.onReady(cb); };

  function resolvePriceFromFlags() {
    var v = 'control';
    try {
      if (window.posthog && posthog.getFeatureFlag) v = posthog.getFeatureFlag(EXPERIMENT) || 'control';
    } catch (e) {}
    fsPrice._resolve(v);
  }

  /* ---- PostHog boot (consent-gated) ----------------------------------- */
  var booted = false;
  function bootPostHog() {
    if (booted || !HAS_KEY) return;
    booted = true;

    // official PostHog loader snippet (loads array.js from POSTHOG_HOST/static)
    !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);

    posthog.init(POSTHOG_KEY, {
      api_host: POSTHOG_HOST,
      person_profiles: 'identified_only',  // anonymous funnels; no PII profile unless we identify
      capture_pageview: true,
      capture_pageleave: true,
      autocapture: true,                   // clickmaps + heatmaps, zero instrumentation
      session_recording: { maskAllInputs: true }, // never record typed name/email
      loaded: function () { resolvePriceFromFlags(); }
    });
    posthog.onFeatureFlags(function () { resolvePriceFromFlags(); });
  }

  /* ---- decide what to do on this page load ---------------------------- */
  if (consentGranted() && HAS_KEY) {
    bootPostHog();
    // safety net: if flags never arrive, fall back to control pricing
    setTimeout(function () { fsPrice._resolve('control'); }, 1500);
  } else {
    // No experiment without consent — everyone sees the control price.
    fsPrice._resolve('control');
    // If they accept later this session (on index.html), start tracking then.
    document.addEventListener('fs-consent-granted', bootPostHog, { once: true });
  }

  /* ---- per-section dwell timing --------------------------------------- *
   * Accumulates visible time per [data-screen-label] and flushes the delta
   * on tab-hide / page-leave. In PostHog, SUM the `seconds` property grouped
   * by `section` to get total dwell. Approximate by nature. */
  function initSections() {
    var secs = document.querySelectorAll('[data-screen-label]');
    if (!secs.length || !('IntersectionObserver' in window)) return;
    var state = {};   // label -> { acc, enter }
    var sent = {};    // label -> seconds already reported

    var io = new IntersectionObserver(function (entries) {
      var t = Date.now();
      entries.forEach(function (en) {
        var label = en.target.getAttribute('data-screen-label');
        var s = state[label] || (state[label] = { acc: 0, enter: 0 });
        if (en.isIntersecting) { if (!s.enter) s.enter = t; }
        else if (s.enter) { s.acc += t - s.enter; s.enter = 0; }
      });
    }, { threshold: 0.35 });
    secs.forEach(function (el) { io.observe(el); });

    function flush() {
      var t = Date.now();
      Object.keys(state).forEach(function (label) {
        var s = state[label];
        if (s.enter) { s.acc += t - s.enter; s.enter = t; }  // keep counting if still visible
        var total = Math.round(s.acc / 1000);
        var delta = total - (sent[label] || 0);
        if (delta > 0) {
          sent[label] = total;
          window.fsTrack('section_view', { section: label, seconds: delta, page: location.pathname });
        }
      });
    }
    document.addEventListener('visibilitychange', function () { if (document.hidden) flush(); });
    window.addEventListener('pagehide', flush);
  }

  /* ---- order-link click tracking -------------------------------------- */
  function initClicks() {
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var a = t.closest('a[href="/cart"], a[href^="/cart"]');
      if (!a) return;
      window.fsTrack('order_clicked', {
        cta: a.getAttribute('data-fs-cta') || 'link',
        text: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40),
        page: location.pathname
      });
    }, true);
  }

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(function () { initSections(); initClicks(); });
})();
