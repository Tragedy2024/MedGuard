(() => {
  'use strict';

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const root = document.documentElement;

  /* 滚动进度：供 WebGL、叙述轨道与进度线共用。 */
  const setScrollProgress = () => {
    const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    root.style.setProperty('--page-scroll', Math.min(1, window.scrollY / max).toFixed(4));
  };
  let scrollTicking = false;
  const requestScroll = () => {
    if (scrollTicking) return;
    scrollTicking = true;
    requestAnimationFrame(() => {
      setScrollProgress();
      scrollTicking = false;
    });
  };
  window.addEventListener('scroll', requestScroll, { passive: true });
  window.addEventListener('resize', requestScroll);
  setScrollProgress();

  /* 分段入场。 */
  const revealTargets = Array.from(document.querySelectorAll('[data-reveal]'));
  if (reduceMotion || !('IntersectionObserver' in window)) {
    revealTargets.forEach((el) => el.classList.add('is-visible'));
  } else {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    revealTargets.forEach((el) => observer.observe(el));
  }

  /* 鼠标跟随：磁吸聚焦。元素自身描边并轻抬，不再绘制自定义光标。 */
  if (window.matchMedia('(pointer: fine)').matches) {
    const SELECTOR = 'a, button, [role="button"], summary';
    let magnet = null;
    const release = () => {
      if (!magnet) return;
      magnet.classList.remove('is-magnetic');
      magnet = null;
    };
    document.addEventListener('pointerover', (event) => {
      if (!(event.target instanceof Element)) return;
      const next = event.target.closest(SELECTOR);
      if (next === magnet) return;
      release();
      if (!next) return;
      next.classList.add('is-magnetic');
      magnet = next;
    }, { passive: true });
    document.addEventListener('pointerout', (event) => {
      if (!magnet) return;
      if (event.relatedTarget instanceof Node && magnet.contains(event.relatedTarget)) return;
      release();
    }, { passive: true });
    window.addEventListener('blur', release);
  }

  /* 页面跳转渐变：从点击位置向外展开，再进入下一页。 */
  const veil = document.getElementById('pageVeil');
  const veilText = veil ? veil.querySelector('.veil-label span') : null;
  let navigating = false;

  const navigate = (href, label = '正在进入安全域', pointX, pointY) => {
    if (navigating) return;
    navigating = true;
    if (!veil) {
      window.location.href = href;
      return;
    }
    veil.style.setProperty('--veil-x', pointX == null ? '50%' : `${pointX}px`);
    veil.style.setProperty('--veil-y', pointY == null ? '50%' : `${pointY}px`);
    if (veilText) veilText.textContent = label;
    veil.classList.add('is-active');
    const wait = reduceMotion ? 40 : 620;
    window.setTimeout(() => { window.location.href = href; }, wait);
  };

  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-transition]');
    if (!link) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== '_self') return;
    if (link.hasAttribute('download')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(url.href, link.dataset.transitionLabel || '正在进入安全域', event.clientX, event.clientY);
  });

  const scrollToId = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const nav = document.querySelector('.site-nav');
    const offset = nav ? nav.offsetHeight + 14 : 0;
    const top = el.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' });
  };

  document.querySelectorAll('a[href^="#"]:not([data-transition])').forEach((link) => {
    const id = link.getAttribute('href').slice(1);
    if (!id) return;
    link.addEventListener('click', (event) => {
      if (!document.getElementById(id)) return;
      event.preventDefault();
      scrollToId(id);
      history.replaceState(null, '', `#${id}`);
    });
  });

  window.MG = {
    navigate,
    reduceMotion,
    scrollToId,
    revealAll: () => revealTargets.forEach((el) => el.classList.add('is-visible')),
  };
})();
