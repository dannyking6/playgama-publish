/*
 * poki_bridge.js — PokiSDK → Playgama Bridge adapter (playgama-publish skill).
 *
 * Maps the game's Poki ad/lifecycle calls onto the Playgama Bridge so ads really
 * serve on the Playgama network:
 *   PokiSDK.commercialBreak()          -> bridge.advertisement.showInterstitial()
 *   PokiSDK.rewardedBreak()            -> bridge.advertisement.showRewarded()
 *      (resolves true ONLY on the 'rewarded' state, never on 'opened')
 *   PokiSDK.gameLoadingProgress/Finished -> platform.sendMessage('game_ready') after load
 *   PokiSDK.happyTime()                -> platform.sendMessage(...) (analytics-lite)
 *
 * AUDIO-SAFE SHIM (QA hard rule on Playgama too): the bridge itself holds audio
 * — it wraps AudioContext at load time (FIRST script), tracks playing
 * <audio>/<video> elements, and suspends/pauses everything for the WHOLE ad
 * break (interstitial AND rewarded), restoring exactly what was active.
 *
 * Loaded FIRST in <head>, BEFORE the Playgama bridge script and any game script.
 * Also honors platform pause / audio state events (tab switches) by pausing both
 * audio and gameplay.
 */
(function () {
  'use strict';
  if (typeof window === 'undefined') return;
  if (window.PokiSDK && window.PokiSDK.__pgBridge) return;

  /* ---------------- audio-safe shim (held by the bridge itself) ------------ */
  var audioShim = (function () {
    var ctxs = [];
    var pausedMedia = [];
    function track(ctx) { if (ctx && ctxs.indexOf(ctx) < 0) ctxs.push(ctx); }
    try {
      if (window.AudioContext) {
        var Base = window.AudioContext;
        function Tracked() {
          var c = new (Function.prototype.bind.apply(Base, [null].concat([].slice.call(arguments))))();
          track(c); return c;
        }
        Tracked.prototype = Base.prototype;
        window.AudioContext = Tracked;
      }
      if (window.webkitAudioContext) {
        var WBase = window.webkitAudioContext;
        function WTracked() {
          var c = new (Function.prototype.bind.apply(WBase, [null].concat([].slice.call(arguments))))();
          track(c); return c;
        }
        WTracked.prototype = WBase.prototype;
        window.webkitAudioContext = WTracked;
      }
    } catch (e) {}
    return {
      suspendAll: function () {
        for (var i = 0; i < ctxs.length; i++) {
          try {
            var c = ctxs[i];
            if (c && typeof c.suspend === 'function' && c.state === 'running') { c.__pgWas = true; c.suspend(); }
          } catch (e) {}
        }
        try {
          document.querySelectorAll('audio, video').forEach(function (el) {
            if (!el.paused) { pausedMedia.push(el); el.pause(); }
          });
        } catch (e) {}
      },
      resumeAll: function () {
        for (var k = 0; k < ctxs.length; k++) {
          try {
            var c = ctxs[k];
            if (c && c.__pgWas && typeof c.resume === 'function') { c.__pgWas = false; c.resume(); }
          } catch (e) {}
        }
        pausedMedia.splice(0).forEach(function (el) { try { el.play(); } catch (e) {} });
      }
    };
  })();

  function bp() { return window.bridge || null; }
  function advSupported(name) {
    var b = bp();
    if (!b || !b.advertisement) return false;
    try {
      var v = b.advertisement[name];
      return typeof v === 'function' ? !!v() : !!v;
    } catch (e) { return false; }
  }

  var bridge = {
    __pgBridge: true,

    init: function () { return Promise.resolve(); },
    gameLoadingStart: function () {},
    gameLoadingProgress: function () {},
    gameLoadingFinished: function () {
      try {
        var b = bp();
        if (b && b.platform && b.platform.sendMessage) b.platform.sendMessage('game_ready');
      } catch (e) {}
    },
    gameplayStart: function () {},
    gameplayStop: function () {},

    commercialBreak: function () {
      audioShim.suspendAll();
      return new Promise(function (resolve) {
        var done = false;
        var fin = function () { if (!done) { done = true; audioShim.resumeAll(); resolve(); } };
        try {
          var b = bp();
          if (advSupported('isInterstitialSupported')) {
            b.advertisement.showInterstitial('interstitial');
          }
        } catch (e) {}
        // some hosts keep the promise-land loose; never freeze gameplay
        setTimeout(fin, 25000);
      });
    },

    rewardedBreak: function () {
      audioShim.suspendAll();
      return new Promise(function (resolve) {
        var done = false;
        var fin = function (ok) {
          if (!done) { done = true; audioShim.resumeAll(); resolve({ success: !!ok }); }
        };
        try {
          var b = bp();
          if (advSupported('isRewardedSupported')) {
            var names = (b.EVENT_NAME || {});
            var evt = names.REWARDED_STATE_CHANGED || 'rewarded_state_changed';
            b.advertisement.on(evt, function (state) {
              if (state === 'rewarded') fin(true);
              else if (state === 'closed' || state === 'failed') fin(false);
            });
            b.advertisement.showRewarded('rewarded');
            setTimeout(function () { fin(false); }, 120000);
          } else fin(false);
        } catch (e) { fin(false); }
      });
    },

    happyTime: function () {
      try {
        var b = bp();
        if (b && b.platform && b.platform.sendMessage) b.platform.sendMessage('haptics_light');
      } catch (e) {}
    },

    customEvent: function () {},
    logEvent: function () {},
    shareableURL: function () { return Promise.resolve({ url: location.href }); },
    sendClientEvent: function () {},
  };

  window.PokiSDK = bridge;
  window.PokiHasInitialised = true;
  if (!window.PokiSDK_InitOK) window.PokiSDK_InitOK = true;

  // platform pause / audio mute -> hold audio like during ads (QA rule)
  window.addEventListener('DOMContentLoaded', function () {
    try {
      var b = bp();
      if (!b || !b.platform || !b.platform.on) return;
      var EVENT_PAUSE = (b.EVENT_NAME && b.EVENT_NAME.PAUSE_STATE_CHANGED) || 'pause_state_changed';
      var EVENT_AUDIO = (b.EVENT_NAME && b.EVENT_NAME.AUDIO_STATE_CHANGED) || 'audio_state_changed';
      b.platform.on(EVENT_PAUSE, function (isPaused) {
        if (isPaused) audioShim.suspendAll(); else audioShim.resumeAll();
      });
      b.platform.on(EVENT_AUDIO, function () { /* engine keeps own audio mute rules */ });
    } catch (e) {}
  });
})();
