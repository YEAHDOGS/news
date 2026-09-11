// DOGS NEWS landing behavior. Vanilla JS, no dependencies.
// transform/opacity only; everything is cheap and skips cleanly without JS.
(function () {
  'use strict';

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- newspaper dateline ---- */
  var dateline = document.getElementById('dateline');
  if (dateline) {
    try {
      dateline.textContent = new Date().toLocaleDateString('en-US', {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
      });
    } catch (e) { /* leave empty */ }
  }

  /* ---- headline word reveal (transform only) ---- */
  var headline = document.querySelector('[data-split]');
  if (headline && !reduceMotion) {
    var words = headline.textContent.trim().split(/\s+/);
    headline.textContent = '';
    words.forEach(function (w) {
      var wrap = document.createElement('span');
      wrap.className = 'w';
      var inner = document.createElement('span');
      inner.className = 'wi';
      inner.textContent = w;
      wrap.appendChild(inner);
      headline.appendChild(wrap);
      headline.appendChild(document.createTextNode(' '));
    });
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { headline.classList.add('on'); });
    });
  }

  /* ---- reading progress (rAF-throttled, passive) ---- */
  var progress = document.querySelector('.progress span');
  var ticking = false;
  function updateProgress() {
    ticking = false;
    if (!progress) return;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    progress.style.transform = 'scaleX(' + p + ')';
  }
  if (progress) {
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; requestAnimationFrame(updateProgress); }
    }, { passive: true });
    updateProgress();
  }

  /* ---- section rail follows the visible section ---- */
  var rails = document.querySelectorAll('.rail a');
  var sections = document.querySelectorAll('main section[id]');
  if (rails.length && sections.length && 'IntersectionObserver' in window) {
    var byId = {};
    rails.forEach(function (r) { byId[r.getAttribute('href').slice(1)] = r; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          rails.forEach(function (r) { r.classList.remove('active'); });
          var r = byId[en.target.id];
          if (r) r.classList.add('active');
        }
      });
    }, { threshold: 0.4 });
    sections.forEach(function (s) { io.observe(s); });
  }

  /* ---- scroll reveals (transform + opacity) ---- */
  var reveals = document.querySelectorAll('[data-reveal]');
  if (reveals.length) {
    if (reduceMotion || !('IntersectionObserver' in window)) {
      reveals.forEach(function (el) { el.classList.add('in'); });
    } else {
      var rio = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            en.target.classList.add('in');
            rio.unobserve(en.target);
          }
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
      reveals.forEach(function (el) { rio.observe(el); });
    }
  }

  /* ---- sticky mobile CTA: hide while the notify section is visible ---- */
  var sticky = document.getElementById('sticky-cta');
  var notify = document.getElementById('notify');
  if (sticky && notify && 'IntersectionObserver' in window) {
    var sio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        sticky.classList.toggle('hide', en.isIntersecting);
      });
    }, { threshold: 0.15 });
    sio.observe(notify);
  }

  /* ---- notify form: front-end only, no backend yet ---- */
  var form = document.querySelector('form[data-notify]');
  if (!form) return;
  var email = form.querySelector('input[type="email"]');
  var error = form.querySelector('.field-error');
  var button = form.querySelector('button');

  function setError(msg) {
    error.textContent = msg;
    email.setAttribute('aria-invalid', msg ? 'true' : 'false');
    if (msg) email.focus();
  }

  email.addEventListener('input', function () { setError(''); });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var value = email.value.trim();
    if (!value) { setError('Please enter your email address.'); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      setError('That does not look like an email address. Even a dog can tell.');
      return;
    }
    setError('');
    button.disabled = true;
    button.classList.add('loading');
    setTimeout(function () { window.location.href = './thanks.html'; }, 800);
  });
})();
