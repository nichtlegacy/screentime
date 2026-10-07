// Entrance, scroll reveal, header state, nav highlight, menu-bar clock and the
// copy buttons. The .js class gates every hidden state in CSS, so without this
// file the page is simply static and fully visible.
document.documentElement.classList.add('js');

// ---------- header ----------

const header = document.getElementById('top-bar');
const syncHeader = () => header.classList.toggle('scrolled', window.scrollY > 8);
syncHeader();
window.addEventListener('scroll', syncHeader, { passive: true });

// ---------- hero ----------

const device = document.getElementById('device');
const heroCopy = document.querySelector('.hero-copy');
const shot = device.querySelector('.window-view img');

device.querySelectorAll('.extra').forEach((el, index) => el.style.setProperty('--i', index));
[...heroCopy.children].forEach((el, index) => el.style.setProperty('--i', index));

// Wait for the dashboard to decode so the window opens whole instead of painting in.
const start = () => requestAnimationFrame(() => {
  device.classList.add('is-on');
  heroCopy.classList.add('is-on');
});
(shot.decode ? shot.decode() : Promise.resolve()).then(start, start);

// The menu bar shows the visitor's own clock, macOS style, on a 24-hour clock.
const clock = document.getElementById('menu-time');
const dayFormat = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
const timeFormat = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const tick = () => {
  const now = new Date();
  clock.textContent = `${dayFormat.format(now).replace(',', '')}  ${timeFormat.format(now)}`;
  clock.dateTime = now.toISOString();
};
tick();
setInterval(tick, 15000);

// ---------- scroll reveal ----------

const items = [...document.querySelectorAll('.reveal')];

// Siblings inside one group cascade; the index resets per group so a late
// section never inherits a long delay from the one above it.
document.querySelectorAll('.features, .more-grid, .duo, .steps').forEach((group) => {
  [...group.children].forEach((child, index) => {
    if (child.classList.contains('reveal')) child.style.setProperty('--i', index);
  });
});

const pending = new Set(items);
const show = (el) => {
  el.classList.add('in');
  pending.delete(el);
};

if (!window.IntersectionObserver) {
  items.forEach(show);
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        show(entry.target);
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: '0px 0px -10% 0px', threshold: 0.05 },
  );
  items.forEach((el) => observer.observe(el));

  // The bottom rootMargin holds an element back until it is a little way into
  // the viewport. On a very tall window the last block can sit inside that dead
  // zone at full scroll and never be released, so a plain "is it on screen"
  // pass runs alongside the observer and wins whenever they disagree.
  const sweep = () => {
    pending.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) show(el);
    });
    if (!pending.size) {
      window.removeEventListener('scroll', sweep);
      window.removeEventListener('resize', sweep);
    }
  };
  window.addEventListener('scroll', sweep, { passive: true });
  window.addEventListener('resize', sweep, { passive: true });
}

// ---------- nav ----------

const nav = document.querySelector('.top-nav');
const glow = nav.querySelector('.nav-glow');
const navLinks = [...nav.querySelectorAll('a')];
const spied = navLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
let activeLink = null;
let hovered = null;
// While a clicked link scrolls the page, the spy would light every section
// passed on the way. It stays quiet until the scroll has settled.
let scrollLock = false;
let lockTimer;

// Light one link and park the glow behind it, or fade out where there is none.
// Coming back from hidden it appears in place instead of sliding in.
const light = (link) => {
  navLinks.forEach((other) => other.classList.toggle('lit', other === link));
  if (!link) {
    glow.style.opacity = '0';
    return;
  }
  glow.classList.toggle('no-slide', glow.style.opacity !== '1');
  glow.style.width = `${link.offsetWidth}px`;
  glow.style.transform = `translateX(${link.offsetLeft}px)`;
  glow.style.opacity = '1';
};
const settle = () => light(hovered || activeLink);

navLinks.forEach((link) => {
  link.addEventListener('pointerenter', (event) => {
    if (event.pointerType !== 'mouse') return;
    hovered = link;
    settle();
  });
  link.addEventListener('click', () => {
    activeLink = link;
    scrollLock = true;
    clearTimeout(lockTimer);
    lockTimer = setTimeout(() => { scrollLock = false; }, 1200);
    settle();
  });
});
nav.addEventListener('pointerleave', () => {
  hovered = null;
  settle();
});
window.addEventListener('scroll', () => {
  if (!scrollLock) return;
  clearTimeout(lockTimer);
  lockTimer = setTimeout(() => { scrollLock = false; }, 160);
}, { passive: true });
window.addEventListener('resize', settle);

if (window.IntersectionObserver && spied.length) {
  const visible = new Map();
  const spy = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => visible.set(entry.target.id, entry.isIntersecting));
      if (scrollLock) return;
      // The topmost section in the band wins, so the highlight moves in reading
      // order. Night belongs to the Grafana link.
      const current = spied.find((section) => visible.get(section.id))
        || (visible.get('night') ? document.getElementById('grafana') : null);
      activeLink = current ? navLinks.find((link) => link.getAttribute('href') === `#${current.id}`) : null;
      settle();
    },
    { rootMargin: '-45% 0px -50% 0px' },
  );
  [...spied, document.getElementById('night')].forEach((section) => spy.observe(section));
}

// ---------- copy ----------

document.querySelectorAll('[data-copy]').forEach((button) => {
  const icon = button.querySelector('.cmd-icon use');
  const label = button.getAttribute('aria-label');
  let timer;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
    } catch {
      return;
    }
    button.classList.add('copied');
    button.setAttribute('aria-label', 'Copied');
    icon.setAttribute('href', '#i-check');
    clearTimeout(timer);
    timer = setTimeout(() => {
      button.classList.remove('copied');
      button.setAttribute('aria-label', label);
      icon.setAttribute('href', '#i-copy');
    }, 1600);
  });
});
