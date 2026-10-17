/*
 * snacks_bridge.js — GameSnacks → Playgama Bridge adapter (playgama-publish skill).
 *
 * Maps GameSnacks API onto the Playgama Bridge with real ads:
 *   GameSnacks.ad.break({type:'next'|'start'|'level'}) -> showInterstitial()
 *   GameSnacks.ad.break({type:'reward'})  -> full beforeAd→beforeReward→showAdFn→
 *       →adViewed→afterAd→adBreakDone flow mapped onto REWARDED_STATE_CHANGED
 *       (grants only on the 'rewarded' state; skips resolve closed=false)
 *   GameSnacks.score.update(n) / updateScore -> platform.sendMessage custom
 *   GameSnacks.game.ready()  -> platform.sendMessage('game_ready')
 *
 * Same AUDIO-SAFE SHIM as poki_bridge.js: the bridge holds ALL game audio for
 * the whole ad break and restores it after. Loaded FIRST in <head>.
 */
(function () {
  'use strict';
  if (typeof window === 'undefined') return;
  if (window.GameSnacks && window.GameSnacks.__pgBridge) return;

  var audioShim = (function () {
    var ctxs = [], pausedMedia = [];
    function track(ctx) { if (ctx && ctxs.indexOf(ctx) < 0) ctxs.push(ctx); }
    try {
      ['AudioContext', 'webkitAudioContext'].forEach(function (nm) {
        var Base = window[nm];
        if (!Base) return;
        function Tracked() {
          var c = new (Function.prototype.bind.apply(Base, [null].concat([].slice.call(arguments))))();
          track(c); return c;
        }
        Tracked.prototype = Base.prototype;
        window[nm] = Tracked;
      });
    } catch (e) {}
    return {
      suspendAll: function () {
        for (var i = 0; i < ctxs.length; i++) {
          try { var c = ctxs[i]; if (c && c.state === 'running') { c.__pgWas = true; c.suspend(); } } catch (e) {}
        }
        try {
          document.querySelectorAll('audio, video').forEach(function (el) {
            if (!el.paused) { pausedMedia.push(el); el.pause(); }
          });
        } catch (e) {}
      },
      resumeAll: function () {
        for (var k = 0; k < ctxs.length; k++) {
          try { var c = ctxs[k]; if (c && c.__pgWas) { c.__pgWas = false; c.resume(); } } catch (e) {}
        }
        pausedMedia.splice(0).forEach(function (el) { try { el.play(); } catch (e) {} });
      }
    };
  })();

  function bp() { return window.bridge || null; }
  function sendMessage(name) {
    try {
      var b = bp();
      if (b && b.platform && b.platform.sendMessage) b.platform.sendMessage(name);
    } catch (e) {}
  }
  function advSupported(name) {
    var b = bp();
    if (!b || !b.advertisement) return false;
    try {
      var v = b.advertisement[name];
      return typeof v === 'function' ? !!v() : !!v;
    } catch (e) { return false; }
  }

  window.GameSnacks = {
    __pgBridge: true,
    ad: {
      break: function (opts) {
        opts = opts || {};
        var type = opts.type || 'next';
        var breakId = opts.breakId || type;
        var beforeAd = opts.beforeAd || function () {};
        var beforeReward = opts.beforeReward || function (f) { f(); }; // default: show offer now
        var showAdFn = opts.showAdFn || function () {};
        var adDismissed = opts.adDismissed || function () {};
        var adViewed = opts.adViewed || function () {};
        var adBreakDone = opts.adBreakDone || function () {};

        if (type === 'reward') {
          // GameSnacks rewarded contract
          beforeReward(function showOffer() {
            beforeAd();
            audioShim.suspendAll();
            var granted = false;
            try {
              var b = bp();
              if (advSupported('isRewardedSupported')) {
                var names = (b.EVENT_NAME || {});
                var evt = names.REWARDED_STATE_CHANGED || 'rewarded_state_changed';
                b.advertisement.on(evt, function (state) {
                  if (state === 'rewarded') granted = true;
                  else if (state === 'closed' || state === 'failed') {
                    audioShim.resumeAll();
                    if (granted) adViewed(); else adDismissed();
                    adBreakDone({ breakStatus: granted ? 'viewed' : 'dismissed', breakId: breakId });
                  }
                });
                showAdFn();
                b.advertisement.showRewarded('rewarded');
                setTimeout(function () {
                  audioShim.resumeAll();
                  if (!granted) { adDismissed(); adBreakDone({ breakStatus: 'dismissed', breakId: breakId }); }
                }, 120000);
              } else {
                audioShim.resumeAll();
                adDismissed();
                adBreakDone({ breakStatus: 'dismissed', breakId: breakId });
              }
            } catch (e) {
              audioShim.resumeAll();
              adDismissed();
              adBreakDone({ breakStatus: 'dismissed', breakId: breakId });
            }
          });
        } else {
          // interstitial (next/start/level)
          beforeAd();
          audioShim.suspendAll();
          var fin = function () { audioShim.resumeAll(); adBreakDone({ breakStatus: 'no_and_shown', breakId: breakId }); };
          try {
            var b = bp();
            if (advSupported('isInterstitialSupported')) b.advertisement.showInterstitial('interstitial');
          } catch (e) {}
          setTimeout(fin, 25000);
        }
      },
    },
    score: {
      update: function (n) { sendMessage('score_' + Number(n)); },
    },
    game: {
      ready: function () { sendMessage('game_ready'); },
      levelComplete: function (n) { sendMessage('level_completed'); },
    },
  };
})();
